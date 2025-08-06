import pandas as pd
import os
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
from models import Condition, StockSignal, ConditionLog, User
from config import Config
import aiofiles
import json

logger = logging.getLogger(__name__)

class ExportService:
    def __init__(self):
        self.export_dir = Config.EXPORT_DIR
        self.max_export_rows = Config.MAX_EXPORT_ROWS
        
        # 내보내기 디렉토리 생성
        os.makedirs(self.export_dir, exist_ok=True)
        
    def export_conditions_to_csv(self, db: Session, user_id: Optional[str] = None) -> str:
        """조건식 데이터를 CSV로 내보내기"""
        try:
            query = db.query(Condition)
            
            if user_id:
                query = query.filter(Condition.user_id == user_id)
                
            conditions = query.all()
            
            if not conditions:
                raise ValueError("내보낼 조건식이 없습니다.")
                
            # DataFrame 생성
            data = []
            for condition in conditions:
                data.append({
                    'ID': condition.id,
                    '사용자ID': condition.user_id,
                    '조건식명': condition.condition_name,
                    '조건식': condition.condition_expression,
                    '활성화': condition.is_active,
                    '생성일시': condition.created_at,
                    '수정일시': condition.updated_at
                })
                
            df = pd.DataFrame(data)
            
            # 파일명 생성
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"conditions_{timestamp}.csv"
            filepath = os.path.join(self.export_dir, filename)
            
            # CSV 저장
            df.to_csv(filepath, index=False, encoding='utf-8-sig')
            
            logger.info(f"조건식 내보내기 완료: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"조건식 내보내기 실패: {e}")
            raise
            
    def export_signals_to_csv(self, db: Session, condition_id: Optional[int] = None, 
                            start_date: Optional[datetime] = None, 
                            end_date: Optional[datetime] = None) -> str:
        """신호 데이터를 CSV로 내보내기"""
        try:
            query = db.query(StockSignal)
            
            if condition_id:
                query = query.filter(StockSignal.condition_id == condition_id)
                
            if start_date:
                query = query.filter(StockSignal.signal_time >= start_date)
                
            if end_date:
                query = query.filter(StockSignal.signal_time <= end_date)
                
            # 최대 행 수 제한
            signals = query.order_by(StockSignal.signal_time.desc()).limit(self.max_export_rows).all()
            
            if not signals:
                raise ValueError("내보낼 신호가 없습니다.")
                
            # DataFrame 생성
            data = []
            for signal in signals:
                data.append({
                    'ID': signal.id,
                    '조건식ID': signal.condition_id,
                    '종목코드': signal.stock_code,
                    '종목명': signal.stock_name,
                    '신호타입': signal.signal_type,
                    '가격': signal.price,
                    '거래량': signal.volume,
                    '신호시간': signal.signal_time,
                    '처리완료': signal.is_processed
                })
                
            df = pd.DataFrame(data)
            
            # 파일명 생성
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"signals_{timestamp}.csv"
            filepath = os.path.join(self.export_dir, filename)
            
            # CSV 저장
            df.to_csv(filepath, index=False, encoding='utf-8-sig')
            
            logger.info(f"신호 내보내기 완료: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"신호 내보내기 실패: {e}")
            raise
            
    def export_logs_to_csv(self, db: Session, condition_id: Optional[int] = None,
                          log_level: Optional[str] = None,
                          start_date: Optional[datetime] = None,
                          end_date: Optional[datetime] = None) -> str:
        """로그 데이터를 CSV로 내보내기"""
        try:
            query = db.query(ConditionLog)
            
            if condition_id:
                query = query.filter(ConditionLog.condition_id == condition_id)
                
            if log_level:
                query = query.filter(ConditionLog.log_level == log_level)
                
            if start_date:
                query = query.filter(ConditionLog.created_at >= start_date)
                
            if end_date:
                query = query.filter(ConditionLog.created_at <= end_date)
                
            # 최대 행 수 제한
            logs = query.order_by(ConditionLog.created_at.desc()).limit(self.max_export_rows).all()
            
            if not logs:
                raise ValueError("내보낼 로그가 없습니다.")
                
            # DataFrame 생성
            data = []
            for log in logs:
                data.append({
                    'ID': log.id,
                    '조건식ID': log.condition_id,
                    '로그메시지': log.log_message,
                    '로그레벨': log.log_level,
                    '생성일시': log.created_at
                })
                
            df = pd.DataFrame(data)
            
            # 파일명 생성
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"logs_{timestamp}.csv"
            filepath = os.path.join(self.export_dir, filename)
            
            # CSV 저장
            df.to_csv(filepath, index=False, encoding='utf-8-sig')
            
            logger.info(f"로그 내보내기 완료: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"로그 내보내기 실패: {e}")
            raise
            
    def export_users_to_csv(self, db: Session) -> str:
        """사용자 데이터를 CSV로 내보내기"""
        try:
            users = db.query(User).all()
            
            if not users:
                raise ValueError("내보낼 사용자가 없습니다.")
                
            # DataFrame 생성
            data = []
            for user in users:
                data.append({
                    'ID': user.id,
                    '사용자명': user.username,
                    '이메일': user.email,
                    '활성화': user.is_active,
                    '생성일시': user.created_at,
                    '수정일시': user.updated_at
                })
                
            df = pd.DataFrame(data)
            
            # 파일명 생성
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"users_{timestamp}.csv"
            filepath = os.path.join(self.export_dir, filename)
            
            # CSV 저장
            df.to_csv(filepath, index=False, encoding='utf-8-sig')
            
            logger.info(f"사용자 내보내기 완료: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"사용자 내보내기 실패: {e}")
            raise
            
    def export_summary_report(self, db: Session, user_id: Optional[str] = None) -> str:
        """요약 리포트 생성"""
        try:
            # 기본 통계 조회
            total_conditions = db.query(Condition).count()
            active_conditions = db.query(Condition).filter(Condition.is_active == True).count()
            total_signals = db.query(StockSignal).count()
            total_users = db.query(User).count()
            
            # 사용자별 조건식 수
            user_conditions = db.query(Condition.user_id, db.func.count(Condition.id).label('count')).group_by(Condition.user_id).all()
            
            # 신호 타입별 통계
            signal_types = db.query(StockSignal.signal_type, db.func.count(StockSignal.id).label('count')).group_by(StockSignal.signal_type).all()
            
            # 최근 7일 신호 통계
            week_ago = datetime.now() - timedelta(days=7)
            recent_signals = db.query(StockSignal).filter(StockSignal.signal_time >= week_ago).count()
            
            # 리포트 데이터 구성
            report_data = {
                '생성일시': datetime.now().isoformat(),
                '전체통계': {
                    '전체조건식수': total_conditions,
                    '활성조건식수': active_conditions,
                    '전체신호수': total_signals,
                    '전체사용자수': total_users,
                    '최근7일신호수': recent_signals
                },
                '사용자별조건식수': [{'사용자ID': uc.user_id, '조건식수': uc.count} for uc in user_conditions],
                '신호타입별통계': [{'신호타입': st.signal_type, '개수': st.count} for st in signal_types]
            }
            
            # JSON 파일로 저장
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"summary_report_{timestamp}.json"
            filepath = os.path.join(self.export_dir, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)
                
            logger.info(f"요약 리포트 생성 완료: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"요약 리포트 생성 실패: {e}")
            raise
            
    def get_export_files(self) -> List[Dict[str, Any]]:
        """내보내기 파일 목록 조회"""
        try:
            files = []
            for filename in os.listdir(self.export_dir):
                filepath = os.path.join(self.export_dir, filename)
                if os.path.isfile(filepath):
                    stat = os.stat(filepath)
                    files.append({
                        'filename': filename,
                        'filepath': filepath,
                        'size': stat.st_size,
                        'created_at': datetime.fromtimestamp(stat.st_ctime),
                        'modified_at': datetime.fromtimestamp(stat.st_mtime)
                    })
                    
            # 최신 파일순으로 정렬
            files.sort(key=lambda x: x['modified_at'], reverse=True)
            return files
            
        except Exception as e:
            logger.error(f"내보내기 파일 목록 조회 실패: {e}")
            return []
            
    def delete_export_file(self, filename: str) -> bool:
        """내보내기 파일 삭제"""
        try:
            filepath = os.path.join(self.export_dir, filename)
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"내보내기 파일 삭제: {filename}")
                return True
            else:
                logger.warning(f"삭제할 파일이 없습니다: {filename}")
                return False
                
        except Exception as e:
            logger.error(f"내보내기 파일 삭제 실패: {e}")
            return False

# 전역 ExportService 인스턴스
export_service = ExportService() 