"""
디버깅 모드 - 함수 호출 추적 및 실행 시간 측정
"""
import logging
import time
import asyncio
from functools import wraps
from datetime import datetime
from typing import Callable, Any

logger = logging.getLogger(__name__)

# 디버그 모드 플래그
DEBUG_MODE = False
TRACE_DEPTH = 0

class DebugTracer:
    """함수 호출 추적 및 성능 측정"""
    
    def __init__(self):
        self.call_stack = []
        self.execution_times = {}
        self.call_count = {}
        
    def trace_sync(self, component: str = "SYSTEM"):
        """동기 함수 추적 데코레이터"""
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **kwargs) -> Any:
                if not DEBUG_MODE:
                    return func(*args, **kwargs)
                
                global TRACE_DEPTH
                indent = "  " * TRACE_DEPTH
                func_name = f"{component}.{func.__name__}"
                
                # 호출 시작 로그
                logger.info(f"{indent}┌─ 🔍 [{func_name}] 시작")
                logger.debug(f"{indent}│  args={args[:2] if args else '()'}, kwargs={list(kwargs.keys())}")
                
                TRACE_DEPTH += 1
                start_time = time.time()
                
                try:
                    result = func(*args, **kwargs)
                    elapsed = time.time() - start_time
                    
                    # 실행 시간 기록
                    if func_name not in self.execution_times:
                        self.execution_times[func_name] = []
                    self.execution_times[func_name].append(elapsed)
                    
                    # 호출 횟수 기록
                    self.call_count[func_name] = self.call_count.get(func_name, 0) + 1
                    
                    TRACE_DEPTH -= 1
                    logger.info(f"{indent}└─ ✅ [{func_name}] 완료 (소요시간: {elapsed:.3f}초)")
                    
                    return result
                    
                except Exception as e:
                    elapsed = time.time() - start_time
                    TRACE_DEPTH -= 1
                    logger.error(f"{indent}└─ ❌ [{func_name}] 실패 (소요시간: {elapsed:.3f}초, 오류: {e})")
                    raise
                    
            return wrapper
        return decorator
    
    def trace_async(self, component: str = "SYSTEM"):
        """비동기 함수 추적 데코레이터"""
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                if not DEBUG_MODE:
                    return await func(*args, **kwargs)
                
                global TRACE_DEPTH
                indent = "  " * TRACE_DEPTH
                func_name = f"{component}.{func.__name__}"
                
                # 호출 시작 로그
                logger.info(f"{indent}┌─ 🔍 [{func_name}] 시작")
                logger.debug(f"{indent}│  args={args[:2] if args else '()'}, kwargs={list(kwargs.keys())}")
                
                TRACE_DEPTH += 1
                start_time = time.time()
                
                try:
                    result = await func(*args, **kwargs)
                    elapsed = time.time() - start_time
                    
                    # 실행 시간 기록
                    if func_name not in self.execution_times:
                        self.execution_times[func_name] = []
                    self.execution_times[func_name].append(elapsed)
                    
                    # 호출 횟수 기록
                    self.call_count[func_name] = self.call_count.get(func_name, 0) + 1
                    
                    TRACE_DEPTH -= 1
                    logger.info(f"{indent}└─ ✅ [{func_name}] 완료 (소요시간: {elapsed:.3f}초)")
                    
                    return result
                    
                except Exception as e:
                    elapsed = time.time() - start_time
                    TRACE_DEPTH -= 1
                    logger.error(f"{indent}└─ ❌ [{func_name}] 실패 (소요시간: {elapsed:.3f}초, 오류: {e})")
                    raise
                    
            return wrapper
        return decorator
    
    def log_checkpoint(self, message: str, component: str = "SYSTEM"):
        """체크포인트 로그 (중간 상태 기록)"""
        if not DEBUG_MODE:
            return
        
        global TRACE_DEPTH
        indent = "  " * TRACE_DEPTH
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        logger.info(f"{indent}│  ⏱️  [{component}] {message} (시각: {timestamp})")
    
    def print_statistics(self):
        """실행 통계 출력"""
        if not DEBUG_MODE or not self.execution_times:
            return
        
        logger.info("=" * 80)
        logger.info("📊 디버그 모드 - 실행 통계")
        logger.info("=" * 80)
        
        # 호출 횟수
        logger.info("\n🔢 함수 호출 횟수:")
        for func_name, count in sorted(self.call_count.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"  - {func_name}: {count}회")
        
        # 실행 시간
        logger.info("\n⏱️  평균 실행 시간:")
        for func_name, times in sorted(self.execution_times.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True):
            avg_time = sum(times) / len(times)
            total_time = sum(times)
            logger.info(f"  - {func_name}: 평균 {avg_time:.3f}초, 총 {total_time:.3f}초 ({len(times)}회)")
        
        # 가장 느린 호출
        logger.info("\n🐌 가장 느린 실행:")
        all_times = [(func, max(times), times.index(max(times))) for func, times in self.execution_times.items() if times]
        all_times.sort(key=lambda x: x[1], reverse=True)
        for func_name, max_time, idx in all_times[:5]:
            logger.info(f"  - {func_name}: {max_time:.3f}초 ({idx+1}번째 호출)")
        
        logger.info("=" * 80)
    
    def reset_statistics(self):
        """통계 초기화"""
        self.execution_times.clear()
        self.call_count.clear()
        logger.info("📊 디버그 통계 초기화 완료")

# 전역 트레이서 인스턴스
debug_tracer = DebugTracer()

def enable_debug_mode():
    """디버그 모드 활성화"""
    global DEBUG_MODE
    DEBUG_MODE = True
    logger.info("=" * 80)
    logger.info("🔍 디버그 모드 활성화")
    logger.info("=" * 80)
    # 로깅 레벨을 DEBUG로 변경
    logging.getLogger().setLevel(logging.DEBUG)
    
def disable_debug_mode():
    """디버그 모드 비활성화"""
    global DEBUG_MODE
    DEBUG_MODE = False
    logger.info("=" * 80)
    logger.info("🔍 디버그 모드 비활성화")
    logger.info("=" * 80)
    # 로깅 레벨을 INFO로 변경
    logging.getLogger().setLevel(logging.INFO)

def is_debug_enabled() -> bool:
    """디버그 모드 활성화 여부 확인"""
    return DEBUG_MODE


