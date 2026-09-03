"""
조건식 실시간 편입 알림 (CNSRREQ search_type=1).

정시 스냅샷은 돌파 직후 조건식에서 빠지면 놓칩니다.
WebSocket 실시간 편입(REAL) 시점에 텔레그램을 보냅니다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config import Config
from notifications.condition_alert import filter_conditions, format_diff, format_price, format_rate
from notifications.telegram_notifier import TelegramNotifier
from utils.datetime_kst import now_kst

logger = logging.getLogger(__name__)

# 키움 REAL values.843 — 삽입(편입) / 삭제(이탈)
_INSERT_MARKERS = frozenset({"I", "i", "1", "+", "편입", "삽입"})
_DELETE_MARKERS = frozenset({"D", "d", "0", "-", "이탈", "삭제"})


def _dedup_sec() -> float:
    try:
        return max(30.0, float(getattr(Config, "TELEGRAM_CONDITION_REALTIME_DEDUP_SEC", 300) or 300))
    except (TypeError, ValueError):
        return 300.0


def _is_insert_event(values: Dict[str, Any]) -> bool:
    raw = values.get("843")
    if raw is None:
        raw = values.get("삽입삭제구분")
    mark = str(raw or "").strip()
    if mark in _INSERT_MARKERS:
        return True
    if mark in _DELETE_MARKERS:
        return False
    # 신호종류만 오는 환경 — 841에 편입 문구가 있으면 편입으로 간주
    kind = str(values.get("841") or "").strip()
    if "편입" in kind or "삽입" in kind:
        return True
    if "이탈" in kind or "삭제" in kind:
        return False
    # 구분값 없으면 편입으로 보수적 처리하지 않음(오탐 방지)
    return False


def _stock_code_from_values(values: Dict[str, Any], item: Dict[str, Any]) -> str:
    code = (
        values.get("9001")
        or values.get("jmcode")
        or item.get("name")
        or ""
    )
    return str(code).replace("A", "").strip()


def _parse_real_items(msg: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any], bool]]:
    """REAL 메시지 → [(stock_code, values, is_insert), ...]."""
    out: List[Tuple[str, Dict[str, Any], bool]] = []
    data = msg.get("data")
    if not isinstance(data, list):
        # 단일 flat REAL
        if msg.get("trnm") == "REAL" and isinstance(msg.get("values"), dict):
            data = [msg]
        else:
            return out
    for item in data:
        if not isinstance(item, dict):
            continue
        values = item.get("values") if isinstance(item.get("values"), dict) else item
        if not isinstance(values, dict):
            continue
        code = _stock_code_from_values(values, item)
        if not code:
            continue
        out.append((code, values, _is_insert_event(values)))
    return out


def build_entry_message(
    condition_name: str,
    stock_code: str,
    *,
    stock_name: str = "",
    values: Optional[Dict[str, Any]] = None,
) -> str:
    values = values or {}
    name = stock_name or str(values.get("302") or "").strip() or stock_code
    # REAL에는 시세 필드가 빈약한 경우가 많음
    stub = {
        "current_price": values.get("10") or "",
        "price_diff": values.get("11") or "",
        "change_rate": values.get("12") or "",
    }
    price = format_price(stub) if stub["current_price"] not in ("", None) else "-"
    diff = format_diff(stub) if stub["price_diff"] not in ("", None) else "-"
    rate = format_rate(stub) if stub["change_rate"] not in ("", None) else "-"
    ts = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"🔔 조건식 편입\n"
        f"조건: {condition_name}\n"
        f"종목: {name}({stock_code})\n"
        f"시세: {price} | {diff} ({rate})\n"
        f"시각: {ts}"
    )


class ConditionRealtimeAlerter:
    """조건식 실시간 편입 → 텔레그램."""

    def __init__(
        self,
        api,
        notifier: Optional[TelegramNotifier] = None,
        names: Optional[List[str]] = None,
    ):
        self.api = api
        self.notifier = notifier or TelegramNotifier()
        self.names = names if names is not None else list(Config.TELEGRAM_ALERT_CONDITION_NAMES or [])
        self._ws = None
        self._seq_to_name: Dict[str, str] = {}
        self._code_names: Dict[str, str] = {}
        self._recent: Dict[Tuple[str, str], float] = {}
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _market_block(self) -> Optional[str]:
        if not Config.TELEGRAM_ALERT_MARKET_HOURS_ONLY:
            return None
        from utils.market_hours import telegram_market_alert_block_reason
        return telegram_market_alert_block_reason()

    def _should_send(self, cond_key: str, code: str) -> bool:
        key = (cond_key, code)
        now = time.monotonic()
        last = self._recent.get(key, 0.0)
        if now - last < _dedup_sec():
            return False
        self._recent[key] = now
        # 오래된 키 정리
        if len(self._recent) > 500:
            cutoff = now - _dedup_sec()
            self._recent = {k: t for k, t in self._recent.items() if t >= cutoff}
        return True

    def _is_ma1592_condition(self, condition_name: str) -> bool:
        """텔레그램/설정에 잡힌 MA1592 조건식인지 (1592매매·레거시 1590매매)."""
        name = str(condition_name or "")
        if "1592" in name or "1590" in name:
            return True
        for kw in (self.names or []):
            if kw and kw in name:
                return True
        return False

    async def _ma1592_universe_on_insert(
        self, condition_name: str, code: str, values: Optional[Dict[str, Any]],
    ) -> None:
        if not self._is_ma1592_condition(condition_name):
            return
        try:
            from core.models import AutoTradeSettings, get_db
            from utils.ma1592 import params_from_settings, upsert_from_condition_async

            settings = None
            for db in get_db():
                settings = db.query(AutoTradeSettings).first()
                break
            p = params_from_settings(settings)

            sname = str((values or {}).get("302") or self._code_names.get(code) or "")
            try:
                price = int((values or {}).get("10") or 0)
            except (TypeError, ValueError):
                price = 0
            ttl = float(getattr(Config, "MA1592_CHART_CACHE_TTL", 60) or 60)
            ok, reason, _ = await upsert_from_condition_async(
                self.api,
                code,
                sname,
                price=price,
                source="condition_realtime",
                condition_label=condition_name,
                params=p,
                cache_ttl_sec=ttl,
            )
            if ok:
                logger.info(f"MA1592 유니버스 편입: {condition_name} {sname or code}({code})")
            else:
                logger.info(f"MA1592 유니버스 편입 거부: {code} ({reason})")
        except Exception as e:
            logger.debug(f"MA1592 유니버스 편입 오류: {e}")

    def _ma1592_universe_on_exit(self, condition_name: str, code: str) -> bool:
        """Deprecated: 조건식 이탈로 장부를 빼지 않음."""
        return False

    async def _resolve_name_and_quote(
        self,
        stock_code: str,
        values: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """REAL에는 302(종목명)·시세가 비는 경우가 많아 ka10001로 보강.

        참고: ka10006(mrkcond flat)에는 stk_nm이 없어 종목명 보강에 쓸 수 없음.
        """
        values = dict(values or {})
        name = (
            self._code_names.get(stock_code, "")
            or str(values.get("302") or "").strip()
        )
        if name == stock_code:
            name = ""
        need_name = not name
        need_px = values.get("10") in ("", None)
        if not need_name and not need_px:
            return name, values

        try:
            code = self.api.normalize_stock_code(stock_code)
            # 종목정보는 기본 코드(venue suffix 없이)로 조회
            resp = await self.api._request_stockinfo_tr("ka10001", {"stk_cd": code})
            if not resp.get("success"):
                logger.info(
                    f"종목명 보강 실패({stock_code}): "
                    f"{resp.get('error') or resp.get('return_msg') or 'unknown'}"
                )
                return name, values
            raw = resp.get("data") if isinstance(resp.get("data"), dict) else {}
            sn = str(
                raw.get("stk_nm")
                or raw.get("stock_name")
                or raw.get("302")
                or ""
            ).strip()
            if sn:
                name = sn
                self._code_names[stock_code] = sn
                self._code_names[code] = sn
                values["302"] = sn
            else:
                logger.info(f"종목명 보강 응답에 이름 없음({stock_code}): keys={list(raw.keys())[:12]}")
            if need_px:
                # ka10001: cur_prc / flu_rt (부호 포함 문자열일 수 있음)
                px_raw = raw.get("cur_prc") or raw.get("close_pric") or raw.get("lst_pric")
                if px_raw not in ("", None):
                    try:
                        values["10"] = abs(int(float(str(px_raw).replace(",", "").replace("+", "").replace("-", "") or "0")))
                        # 부호는 별도 유지하지 않고 절대가; 등락은 flu_rt로
                    except (TypeError, ValueError):
                        pass
                rate = raw.get("flu_rt")
                if rate not in ("", None):
                    values["12"] = rate
                # 전일대비는 없으면 생략 (N/A 허용)
        except Exception as e:
            logger.warning(f"종목명/시세 보강 예외({stock_code}): {e}")
        return name, values

    async def _notify_entry(
        self,
        condition_name: str,
        stock_code: str,
        values: Optional[Dict[str, Any]] = None,
    ) -> None:
        block = self._market_block()
        if block:
            logger.info(f"실시간 편입 알림 스킵({stock_code}): {block}")
            return
        if not self._should_send(condition_name, stock_code):
            logger.debug(f"실시간 편입 중복 스킵: {condition_name} {stock_code}")
            return
        name, enriched = await self._resolve_name_and_quote(stock_code, values)
        msg = build_entry_message(
            condition_name, stock_code, stock_name=name, values=enriched,
        )
        ok = self.notifier.send_message(msg)
        logger.info(
            f"실시간 편입 알림 {'성공' if ok else '실패'}: {condition_name} {name or stock_code}({stock_code})"
        )

    async def _subscribe(self, conditions: List[Dict]) -> None:
        assert self._ws is not None
        for cond in conditions:
            seq = str(cond.get("condition_id") or cond.get("api_id") or "").strip()
            name = str(cond.get("condition_name") or "").strip() or seq
            if not seq:
                continue
            self._seq_to_name[seq] = name
            param = {
                "trnm": "CNSRREQ",
                "seq": seq,
                "search_type": "1",
                "stex_tp": "K",
            }
            await self._ws.send(json.dumps(param))
            logger.info(f"실시간 조건검색 등록: [{seq}] {name}")
            await asyncio.sleep(0.3)

    def _ingest_snapshot_stocks(self, seq: str, data: Any) -> None:
        """초기 CNSRREQ 스냅샷 — 종목명 캐시만 (알림은 실시간 편입만)."""
        if not isinstance(data, list):
            return
        name = self._seq_to_name.get(str(seq), seq)
        for item in data:
            if not isinstance(item, dict):
                continue
            code = str(item.get("9001") or item.get("jmcode") or "").replace("A", "").strip()
            sname = str(item.get("302") or "").strip()
            if code and sname:
                self._code_names[code] = sname
        logger.info(
            f"초기 스냅샷 [{seq}] {name}: {len(data) if isinstance(data, list) else 0}종 "
            f"(알림은 이후 편입만)"
        )

    async def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        trnm = msg.get("trnm")
        if trnm == "PING":
            await self._ws.send(raw if isinstance(raw, str) else json.dumps(msg))
            return
        if trnm in ("LOGIN", "SYSTEM", "CNSRLST"):
            return
        if trnm == "CNSRREQ":
            seq = str(msg.get("seq") or "")
            if msg.get("return_code") not in (0, None):
                logger.warning(f"실시간 조건검색 등록 실패 seq={seq}: {msg}")
                return
            self._ingest_snapshot_stocks(seq, msg.get("data"))
            return
        if trnm == "REAL":
            # 어떤 조건식인지 — seq가 있으면 사용, 없으면 단일 구독 가정
            seq = str(msg.get("seq") or "")
            cond_name = self._seq_to_name.get(seq) if seq else None
            if not cond_name and len(self._seq_to_name) == 1:
                cond_name = next(iter(self._seq_to_name.values()))
            if not cond_name:
                # 전체 등록명 중 첫 값 폴백보다는 로그
                cond_name = "조건식"
            for code, values, is_insert in _parse_real_items(msg):
                sname = str(values.get("302") or "").strip()
                if sname:
                    self._code_names[code] = sname
                if is_insert:
                    await self._notify_entry(cond_name, code, values)
                    await self._ma1592_universe_on_insert(cond_name, code, values)
                else:
                    # 돌파형 조건은 편입 직후 이탈이 흔함 → MA1592 장부는 유지
                    # (장부 제거는 주기 정리·L3에서 EMA15≤EMA90 추세 전환 시)
                    logger.debug(f"조건식 이탈(장부 유지): {cond_name} {code}")
            return
        logger.debug(f"실시간 조건식 기타 메시지: {trnm}")

    async def run_forever(self) -> None:
        if not self.notifier.is_configured():
            raise RuntimeError("텔레그램 미설정 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        # 이미 발급된 토큰이 있으면 재발급하지 않음 (au10001 분당 한도 회피)
        if not self.api.token_manager.get_valid_token():
            if not self.api.authenticate():
                raise RuntimeError("키움 API 인증 실패")

        backoff = 5.0
        while not self._stop:
            try:
                await self._run_session()
                backoff = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"실시간 조건식 세션 오류: {e}")
            if self._stop:
                break
            logger.info(f"실시간 조건식 재연결 대기 {backoff:.0f}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 60.0)
            if not self.api.token_manager.get_valid_token():
                try:
                    self.api.authenticate()
                except Exception:
                    logger.exception("재연결 전 토큰 재발급 실패")

    async def _list_conditions_on_ws(self) -> List[Dict]:
        """이미 열린 조건식 WS에서 CNSRLST 재조회."""
        assert self._ws is not None
        await self._ws.send(json.dumps({
            "trnm": "CNSRLST",
            "token": self.api.token_manager.get_valid_token(),
        }))
        for _ in range(10):
            raw = await asyncio.wait_for(self._ws.recv(), timeout=12.0)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            trnm = msg.get("trnm")
            if trnm == "PING":
                await self._ws.send(raw)
                continue
            if trnm in ("SYSTEM", "LOGIN"):
                continue
            if trnm == "CNSRLST":
                if msg.get("return_code") not in (0, None):
                    raise RuntimeError(f"CNSRLST 실패: {msg}")
                rows = msg.get("data") or []
                out: List[Dict] = []
                for item in rows:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        out.append({
                            "condition_id": str(item[0]),
                            "api_id": str(item[0]),
                            "condition_name": str(item[1]),
                        })
                    elif isinstance(item, dict):
                        seq = str(item.get("seq") or item.get("condition_id") or "")
                        name = str(item.get("name") or item.get("condition_name") or "")
                        if seq:
                            out.append({
                                "condition_id": seq,
                                "api_id": seq,
                                "condition_name": name,
                            })
                return out
        raise TimeoutError("CNSRLST 미수신")

    async def _run_session(self) -> None:
        # 스냅샷 세션과 동일하게 메인 WS와 충돌 방지
        main_state = await self.api._suspend_main_websocket()
        try:
            self._ws = await self.api._open_condition_websocket()
            conditions = await self._list_conditions_on_ws()
            conditions = filter_conditions(conditions, self.names or None)
            if not conditions:
                raise RuntimeError(
                    f"대상 조건식 없음 (필터={self.names or '전체'})"
                )
            await self._subscribe(conditions)
            logger.info(
                f"실시간 편입 알림 대기 중 — {len(conditions)}개 조건식 "
                f"{[c.get('condition_name') for c in conditions]}"
            )
            while not self._stop and self._ws:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=120.0)
                await self._handle_message(raw)
        finally:
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
            await self.api._resume_main_websocket(main_state)


async def run_realtime_condition_alerts(
    api,
    notifier: Optional[TelegramNotifier] = None,
    names: Optional[List[str]] = None,
) -> None:
    alerter = ConditionRealtimeAlerter(api, notifier=notifier, names=names)
    await alerter.run_forever()
