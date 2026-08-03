import logging
import asyncio
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Set
from enum import Enum
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from core.models import PendingBuySignal, get_db
from api.api_rate_limiter import api_rate_limiter
from utils.datetime_kst import kst_today, now_kst, utc_now_naive

logger = logging.getLogger(__name__)

class SignalType(Enum):
    """신호 타입 정의"""
    CONDITION_SIGNAL = "condition"  # 조건식 신호
    REFERENCE_CANDLE = "reference"  # 기준봉 신호
    STRATEGY = "strategy"          # 전략 신호
    AUTO_TRADE = "auto_trade"      # 대시보드 자동매매 스캐너

class SignalStatus(Enum):
    """신호 상태 정의"""
    WATCHING = "WATCHING"    # 관측(유예·진입확인 대기) — 슬롯 미점유, 주문 전
    PENDING = "PENDING"      # 매수 대기 (주문 파이프라인)
    PROCESSING = "PROCESSING" # 처리 중
    ORDERED = "ORDERED"      # 주문 완료
    FAILED = "FAILED"        # 실패
    CANCELLED = "CANCELLED"  # 취소됨
    EXPIRED = "EXPIRED"      # 만료
    FILLED = "FILLED"        # 체결 확정


# 매수 슬롯·동시보유 예약에 포함되는 상태 (WATCHING 제외)
BUY_SLOT_STATUSES = (
    SignalStatus.PENDING.value,
    SignalStatus.PROCESSING.value,
    SignalStatus.ORDERED.value,
)
# 신규 매수 신호 생성 차단 (이미 파이프라인)
BUY_PIPELINE_STATUSES = BUY_SLOT_STATUSES
# 관측 갱신·승격 시 덮어쓰면 안 되는 상태
BUY_TERMINAL_OR_ACTIVE = BUY_PIPELINE_STATUSES + (
    SignalStatus.FILLED.value,
    "COMPLETED",
)

class SignalManager:
    """통합 신호 관리 시스템 - 신호 타입 구분 및 중복 방지"""
    
    def __init__(self):
        self.processed_signals: Dict[str, datetime] = {}  # 중복 감지 방지
        self.signal_ttl_minutes = 5  # 신호 중복 방지 TTL (분)
        self.duplicate_check_window = 10  # 중복 확인 윈도우 (분)
        
    async def create_signal(
        self,
        condition_id: int,
        stock_code: str,
        stock_name: str,
        signal_type: SignalType,
        additional_data: Optional[Dict] = None,
    ) -> bool:
        ok, _ = await self.create_signal_detail(
            condition_id, stock_code, stock_name, signal_type, additional_data,
        )
        return ok

    async def create_signal_detail(
        self,
        condition_id: int,
        stock_code: str,
        stock_name: str,
        signal_type: SignalType,
        additional_data: Optional[Dict] = None,
    ) -> tuple[bool, str]:
        """매수대기(PENDING) 신호 생성. WATCHING이 있으면 승격(PENDING)한다."""
        try:
            logger.info(
                f"📡 [SIGNAL_MANAGER] 신호 생성 요청 - {stock_name}({stock_code}), 타입: {signal_type.value}",
            )

            meta = dict(additional_data or {})
            is_add_buy = bool(meta.get("is_add_buy"))
            current_date = kst_today()
            existing_signal = await self._get_existing_signal(stock_code, condition_id, current_date)
            if existing_signal and existing_signal.status in BUY_TERMINAL_OR_ACTIVE:
                # 분할/피라미딩 추가매수: 당일 unique(종목) 제약상 FILLED 행을 PENDING으로 재개
                allow_reopen = is_add_buy and existing_signal.status in (
                    SignalStatus.FILLED.value,
                    "COMPLETED",
                )
                if not allow_reopen:
                    if existing_signal.status == SignalStatus.PENDING.value:
                        return False, "이미 매수대기"
                    return False, f"이미 {existing_signal.status}"

            # WATCHING → PENDING 승격·추가매수 재개는 TTL 중복 검사 생략
            upgrading_watching = bool(
                existing_signal and existing_signal.status == SignalStatus.WATCHING.value
            )
            if (
                not upgrading_watching
                and not is_add_buy
                and await self._is_duplicate_signal(condition_id, stock_code, signal_type)
            ):
                logger.debug(f"📡 [SIGNAL_MANAGER] 중복 신호 감지 - {stock_name}({stock_code})")
                return False, "중복 신호(TTL 내)"

            meta.pop("wait_kind", None)
            meta.pop("wait_reason", None)
            meta["order_ready"] = True

            if existing_signal:
                reopen = is_add_buy and existing_signal.status in (
                    SignalStatus.FILLED.value,
                    "COMPLETED",
                )
                logger.info(
                    f"📡 [SIGNAL_MANAGER] 같은 일자 신호 존재 - "
                    f"{'추가매수 재개' if reopen else ('WATCHING→PENDING 승격' if upgrading_watching else '업데이트')}: "
                    f"{stock_name}({stock_code})"
                )
                ok = await self._update_existing_signal(
                    existing_signal,
                    signal_type,
                    meta,
                    target_status=SignalStatus.PENDING.value,
                )
                if not ok:
                    return False, "기존 신호 갱신 실패"
                if reopen:
                    return True, "추가매수 재개"
                return (True, "WATCHING→PENDING" if upgrading_watching else "기존 신호 갱신")

            signal_id = await self._save_signal_to_db(
                condition_id,
                stock_code,
                stock_name,
                signal_type,
                meta,
                initial_status=SignalStatus.PENDING.value,
            )

            if signal_id:
                signal_key = f"{condition_id}_{stock_code}_{signal_type.value}"
                self.processed_signals[signal_key] = now_kst()
                logger.info(
                    f"📡 [SIGNAL_MANAGER] 신호 생성 완료 - ID: {signal_id}, {stock_name}({stock_code})",
                )
                return True, "신호 생성"

            logger.error(f"📡 [SIGNAL_MANAGER] 신호 생성 실패 - {stock_name}({stock_code})")
            return False, "DB 저장 실패"

        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 신호 생성 오류 - {stock_name}({stock_code}): {e}")
            return False, f"오류: {e}"

    async def create_watching_detail(
        self,
        condition_id: int,
        stock_code: str,
        stock_name: str,
        signal_type: SignalType,
        additional_data: Optional[Dict] = None,
    ) -> tuple[bool, str]:
        """관측(WATCHING) 신호 생성/갱신. 슬롯 미점유 · PENDING으로 강등하지 않음."""
        try:
            meta = dict(additional_data or {})
            meta["order_ready"] = False
            if not meta.get("wait_kind"):
                meta["wait_kind"] = "gate_wait"

            current_date = kst_today()
            existing = await self._get_existing_signal(stock_code, condition_id, current_date)
            if existing and existing.status in BUY_PIPELINE_STATUSES:
                return False, f"이미 {existing.status}(관측 생략)"
            if existing and existing.status in (SignalStatus.FILLED.value, "COMPLETED"):
                return False, "이미 체결"

            if existing and existing.status == SignalStatus.WATCHING.value:
                ok = await self._update_existing_signal(
                    existing,
                    signal_type,
                    meta,
                    target_status=SignalStatus.WATCHING.value,
                    bump_detected_at=False,
                )
                return (True, "WATCHING 갱신") if ok else (False, "WATCHING 갱신 실패")

            # FAILED/CANCELLED/EXPIRED/없음 → 신규 또는 재개
            if existing:
                ok = await self._update_existing_signal(
                    existing,
                    signal_type,
                    meta,
                    target_status=SignalStatus.WATCHING.value,
                    bump_detected_at=True,
                )
                return (True, "WATCHING 재개") if ok else (False, "WATCHING 재개 실패")

            signal_id = await self._save_signal_to_db(
                condition_id,
                stock_code,
                stock_name,
                signal_type,
                meta,
                initial_status=SignalStatus.WATCHING.value,
            )
            if signal_id:
                logger.info(
                    f"📡 [SIGNAL_MANAGER] WATCHING 생성 - ID: {signal_id}, "
                    f"{stock_name}({stock_code}) kind={meta.get('wait_kind')}"
                )
                return True, "WATCHING 생성"
            return False, "DB 저장 실패"
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] WATCHING 오류 - {stock_name}({stock_code}): {e}")
            return False, f"오류: {e}"

    async def promote_watching_to_pending(
        self,
        signal_id: int,
        additional_data: Optional[Dict] = None,
    ) -> tuple[bool, str]:
        """WATCHING → PENDING 승격 (매수 실행기)."""
        try:
            for db in get_db():
                session: Session = db
                signal = session.query(PendingBuySignal).filter(
                    PendingBuySignal.id == signal_id
                ).first()
                if not signal:
                    return False, "신호 없음"
                if signal.status != SignalStatus.WATCHING.value:
                    return False, f"상태={signal.status}"
                meta = {}
                raw = signal.additional_data
                if isinstance(raw, dict):
                    meta = dict(raw)
                elif isinstance(raw, str):
                    try:
                        import json
                        meta = json.loads(raw) or {}
                    except Exception:
                        meta = {}
                if additional_data:
                    meta.update(additional_data)
                meta.pop("wait_kind", None)
                meta.pop("wait_reason", None)
                meta["order_ready"] = True
                signal.status = SignalStatus.PENDING.value
                signal.additional_data = meta
                signal.failure_reason = None
                signal.detected_at = utc_now_naive()
                session.commit()
                logger.info(
                    f"📡 [SIGNAL_MANAGER] WATCHING→PENDING 승격 - ID: {signal_id} "
                    f"{signal.stock_name}({signal.stock_code})"
                )
                return True, "승격"
            return False, "DB 없음"
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 승격 오류 ID={signal_id}: {e}")
            return False, str(e)
    
    async def _is_duplicate_signal(self, condition_id: int, stock_code: str, signal_type: SignalType) -> bool:
        """중복 신호 확인"""
        try:
            signal_key = f"{condition_id}_{stock_code}_{signal_type.value}"
            current_time = now_kst()
            
            # 만료된 신호 정리
            self._cleanup_expired_signals()
            
            if signal_key in self.processed_signals:
                signal_time = self.processed_signals[signal_key]
                time_diff = current_time - signal_time
                
                if time_diff <= timedelta(minutes=self.signal_ttl_minutes):
                    logger.debug(f"📡 [SIGNAL_MANAGER] 중복 신호 감지 - {signal_key} (TTL 내: {time_diff.total_seconds():.1f}초 전)")
                    return True
                else:
                    # 만료된 신호는 제거
                    del self.processed_signals[signal_key]
                    logger.debug(f"📡 [SIGNAL_MANAGER] 만료된 신호 제거 - {signal_key}")
            
            return False
            
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 중복 신호 확인 오류: {e}")
            return False
    
    async def _get_existing_signal(self, stock_code: str, condition_id: int, target_date: date = None) -> Optional[PendingBuySignal]:
        """기존 신호 조회 (일자별 관리)"""
        try:
            if target_date is None:
                target_date = kst_today()
                
            for db in get_db():
                session: Session = db
                existing_signal = session.query(PendingBuySignal).filter(
                    PendingBuySignal.stock_code == stock_code,
                    PendingBuySignal.condition_id == condition_id,
                    PendingBuySignal.detected_date == target_date
                ).first()
                
                if existing_signal:
                    return existing_signal
                break
            
            return None
            
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 기존 신호 조회 오류: {e}")
            return None
    
    async def _save_signal_to_db(
        self,
        condition_id: int,
        stock_code: str,
        stock_name: str,
        signal_type: SignalType,
        additional_data: Optional[Dict] = None,
        *,
        initial_status: str = SignalStatus.PENDING.value,
    ) -> Optional[int]:
        """신호를 DB에 저장"""
        try:
            for db in get_db():
                session: Session = db
                
                # 신호 데이터 준비
                signal_data = {
                    "condition_id": condition_id,
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "status": initial_status,
                    "detected_at": utc_now_naive(),
                    "detected_date": kst_today(),  # KST 일자
                    "signal_type": signal_type.value
                }
                
                # 추가 데이터 → JSON 컬럼 저장
                if additional_data:
                    allowed_scalar = {
                        "reference_candle_high",
                        "reference_candle_date",
                        "target_price",
                    }
                    for k, v in additional_data.items():
                        if k in allowed_scalar:
                            signal_data[k] = v
                    signal_data["additional_data"] = additional_data
                
                # 신호 생성
                pending_signal = PendingBuySignal(**signal_data)
                session.add(pending_signal)
                session.commit()
                
                logger.info(
                    f"📡 [SIGNAL_MANAGER] 신호 DB 저장 완료 - ID: {pending_signal.id} "
                    f"status={initial_status}"
                )
                return pending_signal.id
                
        except IntegrityError as e:
            logger.warning(f"📡 [SIGNAL_MANAGER] 신호 저장 중복 오류: {e}")
            return None
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 신호 DB 저장 오류: {e}")
            return None
    
    async def record_failed_signal(
        self,
        condition_id: int,
        stock_code: str,
        stock_name: str,
        signal_type: SignalType,
        failure_reason: str,
        additional_data: Optional[Dict] = None,
    ) -> tuple[bool, str]:
        """매수 미실행·스킵을 FAILED로 남겨 체결 로그에 사유가 보이게 한다.

        이미 PENDING/PROCESSING/ORDERED/FILLED/WATCHING 이면 덮어쓰지 않는다.
        """
        reason = (failure_reason or "사유 미기록").strip()[:255] or "사유 미기록"
        meta = dict(additional_data or {})
        meta["order_ready"] = False
        code = (stock_code or "").strip() or "JONGGA"
        name = (stock_name or "").strip() or "종가배팅"
        try:
            current_date = kst_today()
            existing = await self._get_existing_signal(code, condition_id, current_date)
            if existing and existing.status in BUY_TERMINAL_OR_ACTIVE:
                return False, f"이미 {existing.status}"
            if existing and existing.status == SignalStatus.WATCHING.value:
                return False, "이미 WATCHING"

            if existing:
                ok = await self._update_existing_signal(
                    existing,
                    signal_type,
                    meta,
                    target_status=SignalStatus.FAILED.value,
                    bump_detected_at=True,
                    failure_reason=reason,
                )
                return (True, "실패 이력 갱신") if ok else (False, "실패 이력 갱신 실패")

            signal_id = await self._save_signal_to_db(
                condition_id,
                code,
                name,
                signal_type,
                meta,
                initial_status=SignalStatus.FAILED.value,
            )
            if not signal_id:
                return False, "DB 저장 실패"
            for db in get_db():
                session: Session = db
                row = (
                    session.query(PendingBuySignal)
                    .filter(PendingBuySignal.id == signal_id)
                    .first()
                )
                if row:
                    row.failure_reason = reason
                    session.commit()
                break
            logger.info(
                f"📡 [SIGNAL_MANAGER] 실패 이력 저장 - ID: {signal_id}, "
                f"{name}({code}): {reason}"
            )
            return True, "실패 이력 저장"
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 실패 이력 저장 오류 - {name}({code}): {e}")
            return False, f"오류: {e}"

    async def _update_existing_signal(
        self,
        existing_signal: PendingBuySignal,
        signal_type: SignalType,
        additional_data: Optional[Dict] = None,
        *,
        target_status: str = SignalStatus.PENDING.value,
        bump_detected_at: bool = True,
        failure_reason: Optional[str] = None,
    ) -> bool:
        """기존 신호 업데이트 (일자별 관리)."""
        try:
            signal_id = int(getattr(existing_signal, "id", 0) or 0)
            if signal_id <= 0:
                return False
            for db in get_db():
                session: Session = db
                # 다른 세션에서 가져온 객체는 detach 될 수 있어 id로 재조회
                signal = (
                    session.query(PendingBuySignal)
                    .filter(PendingBuySignal.id == signal_id)
                    .first()
                )
                if not signal:
                    return False

                if bump_detected_at:
                    signal.detected_at = utc_now_naive()
                signal.signal_type = signal_type.value
                signal.status = target_status
                if target_status != SignalStatus.FAILED.value:
                    signal.failure_reason = None
                elif failure_reason:
                    signal.failure_reason = str(failure_reason)[:255]

                if additional_data:
                    allowed_scalar = {
                        "reference_candle_high",
                        "reference_candle_date",
                        "target_price",
                    }
                    for k, v in additional_data.items():
                        if k in allowed_scalar:
                            setattr(signal, k, v)
                    signal.additional_data = additional_data

                session.commit()

                logger.info(
                    f"📡 [SIGNAL_MANAGER] 기존 신호 업데이트 완료 - ID: {signal.id} "
                    f"→ {target_status}"
                )
                return True

        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 기존 신호 업데이트 오류: {e}")
            return False
    
    def _cleanup_expired_signals(self):
        """만료된 신호 정리"""
        try:
            current_time = now_kst()
            expired_keys = [
                key for key, timestamp in self.processed_signals.items()
                if current_time - timestamp > timedelta(minutes=self.signal_ttl_minutes)
            ]
            
            for key in expired_keys:
                del self.processed_signals[key]
            
            if expired_keys:
                logger.debug(f"📡 [SIGNAL_MANAGER] 만료된 신호 {len(expired_keys)}개 정리 완료")
                
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 만료된 신호 정리 오류: {e}")
    
    async def update_signal_status(self, signal_id: int, status: SignalStatus, order_id: str = "", error_msg: str = ""):
        """신호 상태 업데이트 (실패 사유/주문ID 반영)"""
        try:
            for db in get_db():
                session: Session = db
                signal = session.query(PendingBuySignal).filter(PendingBuySignal.id == signal_id).first()
                
                if signal:
                    old_status = signal.status
                    signal.status = status.value
                    
                    # 주문 ID 저장 (필드가 있다면)
                    if order_id:
                        pass  # 주문 ID 필드가 있다면 여기에 추가
                    
                    # 실패 사유 저장 (모델 컬럼 존재 시)
                    if error_msg and status == SignalStatus.FAILED:
                        try:
                            signal.failure_reason = str(error_msg)[:255]
                        except Exception:
                            # 컬럼이 없거나 매핑 이슈 시 조용히 무시
                            pass
                    
                    try:
                        session.commit()
                    except IntegrityError:
                        # 동일 (condition_id, stock_code, status) 레코드가 이미 존재하는 경우
                        session.rollback()
                        duplicate = session.query(PendingBuySignal).filter(
                            PendingBuySignal.condition_id == signal.condition_id,
                            PendingBuySignal.stock_code == signal.stock_code,
                            PendingBuySignal.status == status.value,
                            PendingBuySignal.id != signal.id
                        ).first()
                        if duplicate:
                            # 현재 레코드를 삭제하여 유니크 충돌 해소
                            session.delete(signal)
                            session.commit()
                            logger.info(f"📡 [SIGNAL_MANAGER] 상태 중복 감지로 레코드 정리 - 기존 유지(ID: {duplicate.id}), 삭제(ID: {signal_id})")
                        else:
                            # 예외 재발생 방지용 재시도
                            session.commit()
                    
                    logger.info(f"📡 [SIGNAL_MANAGER] 신호 상태 변경 - ID: {signal_id}, {old_status} -> {status.value}")
                    
                    # 주문 완료 시 중복 방지 신호 제거
                    if status == SignalStatus.ORDERED:
                        signal_key = f"{signal.condition_id}_{signal.stock_code}_{signal.signal_type}"
                        if signal_key in self.processed_signals:
                            del self.processed_signals[signal_key]
                            logger.debug(f"📡 [SIGNAL_MANAGER] 완료된 신호 중복 방지 제거 - {signal_key}")
                break
                
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 신호 상태 업데이트 오류: {e}")
    
    async def get_signals_by_status(self, status: SignalStatus) -> List[PendingBuySignal]:
        """상태별 신호 조회"""
        try:
            signals = []
            for db in get_db():
                session: Session = db
                signals = session.query(PendingBuySignal).filter(
                    PendingBuySignal.status == status.value
                ).order_by(PendingBuySignal.detected_at.asc()).all()
                break
            
            return signals
            
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 신호 조회 오류: {e}")
            return []
    
    async def get_signal_statistics(self) -> Dict:
        """신호 통계 조회"""
        try:
            stats = {
                "total_signals": 0,
                "pending_signals": 0,
                "processing_signals": 0,
                "ordered_signals": 0,
                "failed_signals": 0,
                "cancelled_signals": 0,
                "condition_signals": 0,
                "reference_signals": 0,
                "duplicate_prevention": len(self.processed_signals)
            }
            
            for db in get_db():
                session: Session = db
                
                # 전체 신호 수
                stats["total_signals"] = session.query(PendingBuySignal).count()
                
                # 상태별 신호 수
                for status in SignalStatus:
                    count = session.query(PendingBuySignal).filter(
                        PendingBuySignal.status == status.value
                    ).count()
                    stats[f"{status.value.lower()}_signals"] = count
                
                # 타입별 신호 수
                for signal_type in SignalType:
                    count = session.query(PendingBuySignal).filter(
                        PendingBuySignal.signal_type == signal_type.value
                    ).count()
                    stats[f"{signal_type.value}_signals"] = count
                
                break
            
            return stats
            
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 신호 통계 조회 오류: {e}")
            return {}
    
    async def cleanup_old_signals(self, days: int = 7):
        """오래된 신호 정리 (기본 7일)"""
        try:
            cutoff_date = utc_now_naive() - timedelta(days=days)
            deleted_count = 0
            
            for db in get_db():
                session: Session = db
                
                # 완료되거나 실패한 오래된 신호 삭제
                old_signals = session.query(PendingBuySignal).filter(
                    PendingBuySignal.detected_at < cutoff_date,
                    PendingBuySignal.status.in_([SignalStatus.ORDERED.value, SignalStatus.FAILED.value])
                ).all()
                
                for signal in old_signals:
                    session.delete(signal)
                    deleted_count += 1
                
                session.commit()
                break
            
            if deleted_count > 0:
                logger.info(f"📡 [SIGNAL_MANAGER] 오래된 신호 {deleted_count}개 정리 완료")
            
            return deleted_count
            
        except Exception as e:
            logger.error(f"📡 [SIGNAL_MANAGER] 오래된 신호 정리 오류: {e}")
            return 0

# 전역 인스턴스
signal_manager = SignalManager()
