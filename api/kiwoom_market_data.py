from __future__ import annotations

import asyncio
import json
import random
import ssl
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiohttp

from api.api_rate_limiter import api_rate_limiter
from api.kiwoom_api_shared import (
    _expected_intraday_bars_for_day,
    _parse_kiwoom_float,
    _parse_kiwoom_int,
    logger,
)
from core.config import Config
from utils.api_traffic_guard import APIPriority
from utils.datetime_kst import kst_today, now_kst

__all__ = ["_MarketDataMixin"]


class _MarketDataMixin:
    async def get_stock_chart_data(
        self, stock_code: str, period: str = "1D", max_bars: Optional[int] = None,
        *, allow_off_hours: bool = False, cache_ttl_sec: Optional[float] = None,
    ):
        """종목 차트 데이터 조회 - 실제 키움 API 사용.

        max_bars: 파싱 후 최근 N봉만 유지 (ATR 등 — API는 전체 일봉을 주므로 슬라이스).
        allow_off_hours: True면 일봉 조회 시 장외에도 API 호출 (검증·ATR 표시용).
        """
        import time
        from utils.market_hours import is_krx_session

        stock_code = self.normalize_stock_code(stock_code)
        normalized = (period or "1D").strip().upper()
        is_weekly = normalized in {"1W", "W", "WEEK", "WEEKLY"}
        if is_weekly:
            normalized = "1W"
        is_daily = normalized in {"1D", "D", "DAY", "DAILY"}
        is_minute_chart = normalized in {
            "5M", "5MIN", "M5", "5MINUTE", "5",
            "1M", "M1", "1MIN", "3M", "M3", "3MIN",
            "10M", "M10", "10MIN", "15M", "M15",
            "30M", "M30", "60M", "M60", "60MIN", "1H",
        }
        # 분봉은 통합(_AL) 시세 — 캐시 키에 venue 구분
        cache_key = (
            f"{stock_code}:AL:{normalized}" if is_minute_chart else f"{stock_code}:{normalized}"
        )
        ttl = self._daily_chart_cache_ttl if (is_daily or is_weekly) else self._chart_cache_ttl
        if cache_ttl_sec is not None:
            try:
                ttl = max(1.0, float(cache_ttl_sec))
            except (TypeError, ValueError):
                pass
        cached = self._chart_cache.get(cache_key)
        if cached:
            data, ts = cached
            if time.time() - ts < ttl:
                logger.debug(f"💾 [CHART_CACHE_HIT] {cache_key}")
                return data[-max_bars:] if max_bars and data else data

        # 분봉도 검증·시뮬용 allow_off_hours 허용
        if not is_krx_session() and not (allow_off_hours and (is_daily or is_weekly or is_minute_chart)):
            if cached:
                logger.debug(f"장외 시간 — 차트 캐시 사용: {cache_key}")
                data = cached[0]
                return data[-max_bars:] if max_bars and data else data
            logger.debug(f"장외 시간 — 차트 조회 생략: {cache_key}")
            return []

        try:
            logger.info(f"차트 데이터 조회 시작: {stock_code}, 기간: {period}")
            
            if not self.token_manager.get_valid_token():
                logger.error("키움 API 토큰이 없습니다")
                return cached[0] if cached else []
            
            # LIMITED여도 allow_off_hours(검증·시뮬)는 즉시 포기하지 않고 슬롯 대기로 넘긴다.
            if not api_rate_limiter.is_api_available() and not allow_off_hours:
                logger.warning("차트 조회 건너뜀 - API 제한 상태")
                return cached[0] if cached else []
            
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            endpoint = '/api/dostk/chart'
            url = host + endpoint
            
            if is_minute_chart:
                api_id = 'ka10080'
                if normalized in {"5M", "5MIN", "M5", "5MINUTE", "5"}:
                    tic_scope = "5"
                elif normalized in {"1M", "M1", "1MIN"}:
                    tic_scope = "1"
                elif normalized in {"3M", "M3", "3MIN"}:
                    tic_scope = "3"
                elif normalized in {"10M", "M10", "10MIN"}:
                    tic_scope = "10"
                elif normalized in {"15M", "M15", "15MIN"}:
                    tic_scope = "15"
                elif normalized in {"30M", "M30", "30MIN"}:
                    tic_scope = "30"
                elif normalized in {"60M", "M60", "60MIN", "1H"}:
                    tic_scope = "60"
                else:
                    tic_scope = "5"
                request_data = {
                    "stk_cd": self.minute_chart_stk_cd(stock_code),
                    "tic_scope": tic_scope,
                    "upd_stkpc_tp": "1",
                }
            else:
                api_id = 'ka10082' if is_weekly else 'ka10081'
                today = now_kst()
                if today.weekday() == 5:
                    base_dt = (today - timedelta(days=1)).strftime('%Y%m%d')
                elif today.weekday() == 6:
                    base_dt = (today - timedelta(days=2)).strftime('%Y%m%d')
                else:
                    base_dt = today.strftime('%Y%m%d')
                request_data = {
                    "dmst_stex_tp": "KRX",
                    "stk_cd": stock_code,
                    "base_dt": base_dt,
                    "upd_stkpc_tp": "1",
                }
            
            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'cont-yn': 'N',
                'next-key': '',
                'api-id': api_id,
            }

            max_attempts = 3
            slot_wait = 45.0 if allow_off_hours else 6.0
            for attempt in range(max_attempts):
                if not await self._acquire_api_slot("chart_data", max_wait=slot_wait, priority=APIPriority.HIGH):
                    logger.warning("차트 조회 슬롯 확보 실패")
                    if attempt < max_attempts - 1:
                        # 분당 한도/간격 — 윈도우가 밀릴 때까지 대기
                        wait_more = max(
                            2.0,
                            min(api_rate_limiter.seconds_until_available() or 3.0, 20.0),
                        )
                        await asyncio.sleep(wait_more)
                        continue
                    return cached[0] if cached else []

                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=request_data) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get('return_code') == 0:
                                parsed = self._parse_kiwoom_chart_data(data, stock_code)
                                # 캐시는 자르지 않은 전체 봉 — max_bars 가 다른 호출끼리 공유
                                if parsed:
                                    self._chart_cache[cache_key] = (parsed, time.time())
                                return parsed[-max_bars:] if max_bars and parsed else parsed
                            msg = (data.get('return_msg') or "").lower()
                            if any(k in msg for k in ["rate limit", "too many", "요청 한도", "429", "제한", "초과"]):
                                api_rate_limiter.handle_api_error(Exception(data.get('return_msg', 'rate limit')))
                                backoff = (2 ** attempt) + random.uniform(0, 0.5)
                                logger.warning(f"차트 조회 제한 — {backoff:.2f}s 후 재시도 {attempt+1}/{max_attempts}")
                                await asyncio.sleep(backoff)
                                continue
                            if attempt == 0 and self._is_kiwoom_token_invalid(data.get('return_msg', ''), data):
                                logger.warning("🔑 [TOKEN] 차트 조회 토큰 무효 — 재인증 후 재시도")
                                if await self._reauthenticate_async():
                                    headers['authorization'] = f'Bearer {self.token_manager.get_valid_token()}'
                                    continue
                            logger.error(f"키움 API 오류: {data.get('return_msg')}")
                            return cached[0] if cached else []
                        elif response.status == 429:
                            api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                            backoff = (2 ** attempt) + random.uniform(0, 0.5)
                            await asyncio.sleep(backoff)
                            continue
                        else:
                            logger.error(f"키움 API 호출 실패: {response.status}")
                            return cached[0] if cached else []
            return cached[0] if cached else []
                        
        except Exception as e:
            logger.error(f"실제 차트 데이터 조회 중 오류: {e}")
            return cached[0] if cached else []

    async def get_intraday_chart_for_date(
        self,
        stock_code: str,
        trade_date: str,
        tic_scope: str = "15",
        max_pages: int = 1,
        *,
        priority=APIPriority.NORMAL,
    ) -> Dict:
        """특정 거래일(KST) 분봉 OHLCV 조회 (ka10080, 검증 페이지용).

        장외 시간에도 조회 가능. trade_date: YYYY-MM-DD.
        키움 API는 페이지당 최대 약 900봉을 반환할 수 있으나, 해당 일자만 필터해 사용한다.
        15분봉 1일치(≈27봉)면 1페이지로 충분하다.
        """
        stock_code = self.normalize_stock_code(stock_code)
        date_str = (trade_date or "").strip()[:10]
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"success": False, "error": "날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)", "bars": []}

        yyyymmdd = target.strftime("%Y%m%d")
        # 분봉 검증/시뮬은 통합(_AL) 시세
        cache_key = f"{stock_code}:AL:M{tic_scope}:{yyyymmdd}"
        today_kst = kst_today().strftime("%Y%m%d")
        ttl = self._chart_cache_ttl if yyyymmdd == today_kst else 86400

        cached = self._chart_cache.get(cache_key)
        if cached:
            data, ts = cached
            if time.time() - ts < ttl:
                logger.debug(f"💾 [VERIFY_CHART_CACHE_HIT] {cache_key}")
                return {"success": True, "bars": data, "cached": True}

        if not self.token_manager.get_valid_token():
            if cached:
                return {"success": True, "bars": cached[0], "cached": True, "warning": "토큰 없음 — 캐시 사용"}
            return {"success": False, "error": "키움 API 토큰이 없습니다", "bars": []}

        if not api_rate_limiter.is_api_available():
            # 검증용 분봉은 LIMITED에서도 슬롯 대기로 진행 (즉시 포기하지 않음)
            pass

        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        url = host + "/api/dostk/chart"
        headers_base = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.token_manager.get_valid_token()}",
            "api-id": "ka10080",
        }
        body = {
            "stk_cd": self.minute_chart_stk_cd(stock_code),
            "tic_scope": str(tic_scope),
            "upd_stkpc_tp": "1",
            "date": yyyymmdd,
        }

        all_bars: List[Dict] = []
        cont_yn, next_key = "N", ""
        pages = 0
        last_error = ""
        day_prefix = date_str
        need_bars = _expected_intraday_bars_for_day(tic_scope)

        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                while pages < max_pages:
                    pages += 1
                    if not await self._acquire_api_slot("verify_intraday_chart", max_wait=45.0, priority=priority):
                        last_error = "API 슬롯 확보 실패"
                        break

                    headers = {**headers_base, "cont-yn": cont_yn, "next-key": next_key}
                    async with session.post(url, headers=headers, json=body) as resp:
                        text = await resp.text()
                        resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                        resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""

                        if resp.status == 429:
                            api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                            last_error = "API 호출 제한 (429)"
                            break

                        if resp.status != 200:
                            last_error = f"HTTP {resp.status}"
                            break

                        data = json.loads(text)

                    ok = (data.get("return_code") == 0) or (data.get("returnCode") == 0) or (data.get("rt_cd") == "0")
                    if not ok:
                        last_error = data.get("return_msg") or data.get("returnMsg") or "조회 실패"
                        msg_lower = str(last_error).lower()
                        if any(k in msg_lower for k in ["rate limit", "too many", "요청 한도", "429", "제한", "초과"]):
                            api_rate_limiter.handle_api_error(Exception(last_error))
                        break

                    parsed = self._parse_kiwoom_chart_data(
                        data, stock_code, trade_yyyymmdd=yyyymmdd, verbose=False,
                    )
                    all_bars.extend(parsed)

                    if len(all_bars) >= need_bars:
                        break

                    cont_yn = data.get("cont_yn") or resp_cont or "N"
                    next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                    if str(cont_yn).upper() != "Y" or not next_key:
                        break

            # 해당 거래일 봉만 유지 (파싱 단계에서 1차 필터, 여기서 재확인)
            bars = [b for b in all_bars if str(b.get("timestamp", "")).startswith(day_prefix)]
            bars.sort(key=lambda x: x["timestamp"])

            if bars:
                self._chart_cache[cache_key] = (bars, time.time())
                return {"success": True, "bars": bars, "pages": pages, "bar_count": len(bars)}

            if cached:
                return {"success": True, "bars": cached[0], "cached": True, "warning": "당일 데이터 없음 — 캐시 사용"}

            hint = last_error or "해당 일자 분봉 데이터가 없습니다"
            return {"success": False, "error": hint, "bars": []}

        except Exception as e:
            logger.error(f"검증용 분봉 조회 오류: {stock_code} {date_str} — {e}")
            if cached:
                return {"success": True, "bars": cached[0], "cached": True, "warning": str(e)}
            return {"success": False, "error": str(e), "bars": []}

    @staticmethod
    def parse_ka20005_bars(api_response: dict, trade_yyyymmdd: str) -> List[Dict]:
        """ka20005 응답(inds_min_pole_qry) → 해당 일자 분봉 OHLCV 리스트 (오름차순)."""
        rows = (api_response or {}).get("inds_min_pole_qry") or []
        bars: List[Dict] = []
        for item in rows:
            cntr_tm = str(item.get("cntr_tm", "") or "")
            if len(cntr_tm) < 12 or cntr_tm[:8] != trade_yyyymmdd:
                continue
            formatted = f"{cntr_tm[:4]}-{cntr_tm[4:6]}-{cntr_tm[6:8]} {cntr_tm[8:10]}:{cntr_tm[10:12]}:00"
            bars.append({
                "timestamp": formatted,
                "open": abs(_parse_kiwoom_float(item.get("open_pric", 0))),
                "high": abs(_parse_kiwoom_float(item.get("high_pric", 0))),
                "low": abs(_parse_kiwoom_float(item.get("low_pric", 0))),
                "close": abs(_parse_kiwoom_float(item.get("cur_prc", 0))),
                "volume": abs(_parse_kiwoom_int(item.get("trde_qty", 0))),
            })
        bars.sort(key=lambda x: x["timestamp"])
        return bars

    async def get_index_intraday_chart_for_date(
        self,
        index_key: str,
        trade_date: str,
        tic_scope: str = "15",
        *,
        priority=APIPriority.NORMAL,
    ) -> Dict:
        """특정 거래일(KST) 업종 지수 분봉 OHLC 조회 (ka20005, 검증 페이지 지수 카드용).

        index_key: kospi / kosdaq. trade_date: YYYY-MM-DD.
        지수 값은 소수점이 있으므로 float으로 파싱한다.
        """
        inds_cd = self.INDEX_CHART_CODES.get(str(index_key or "").lower())
        if not inds_cd:
            return {"success": False, "error": f"지원하지 않는 지수: {index_key}", "bars": []}

        date_str = (trade_date or "").strip()[:10]
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"success": False, "error": "날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)", "bars": []}

        yyyymmdd = target.strftime("%Y%m%d")
        cache_key = f"INDEX:{inds_cd}:M{tic_scope}:{yyyymmdd}"
        today_kst = kst_today().strftime("%Y%m%d")
        ttl = self._chart_cache_ttl if yyyymmdd == today_kst else 86400

        cached = self._chart_cache.get(cache_key)
        if cached:
            data, ts = cached
            if time.time() - ts < ttl:
                return {"success": True, "bars": data, "cached": True}

        if not self.token_manager.get_valid_token():
            if cached:
                return {"success": True, "bars": cached[0], "cached": True, "warning": "토큰 없음 — 캐시 사용"}
            return {"success": False, "error": "키움 API 토큰이 없습니다", "bars": []}

        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        url = host + "/api/dostk/chart"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.token_manager.get_valid_token()}",
            "api-id": "ka20005",
            "cont-yn": "N",
            "next-key": "",
        }
        body = {
            "inds_cd": inds_cd,
            "tic_scope": str(tic_scope),
            "base_dt": yyyymmdd,
        }

        last_error = ""
        try:
            if not await self._acquire_api_slot("verify_index_chart", max_wait=45.0, priority=priority):
                if cached:
                    return {"success": True, "bars": cached[0], "cached": True, "warning": "API 슬롯 확보 실패 — 캐시 사용"}
                return {"success": False, "error": "API 슬롯 확보 실패", "bars": []}

            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=body) as resp:
                    text = await resp.text()
                    if resp.status == 429:
                        api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                        last_error = "API 호출 제한 (429)"
                        data = None
                    elif resp.status != 200:
                        last_error = f"HTTP {resp.status}"
                        data = None
                    else:
                        data = json.loads(text)

            if data is None:
                if cached:
                    return {"success": True, "bars": cached[0], "cached": True, "warning": last_error}
                return {"success": False, "error": last_error or "조회 실패", "bars": []}

            ok = (data.get("return_code") == 0) or (data.get("returnCode") == 0) or (data.get("rt_cd") == "0")
            if not ok:
                last_error = data.get("return_msg") or data.get("returnMsg") or "조회 실패"
                msg_lower = str(last_error).lower()
                if any(k in msg_lower for k in ["rate limit", "too many", "요청 한도", "429", "제한", "초과"]):
                    api_rate_limiter.handle_api_error(Exception(last_error))
                if cached:
                    return {"success": True, "bars": cached[0], "cached": True, "warning": str(last_error)}
                return {"success": False, "error": str(last_error), "bars": []}

            bars = self.parse_ka20005_bars(data, yyyymmdd)

            if bars:
                self._chart_cache[cache_key] = (bars, time.time())
                return {"success": True, "bars": bars, "bar_count": len(bars)}

            if cached:
                return {"success": True, "bars": cached[0], "cached": True, "warning": "당일 데이터 없음 — 캐시 사용"}
            return {"success": False, "error": last_error or "해당 일자 지수 분봉 데이터가 없습니다", "bars": []}

        except Exception as e:
            logger.error(f"지수 분봉 조회 오류: {index_key} {date_str} — {e}")
            if cached:
                return {"success": True, "bars": cached[0], "cached": True, "warning": str(e)}
            return {"success": False, "error": str(e), "bars": []}

    async def _get_current_price_ka10006(
        self, stock_code: str, *, venue: str = "AL",
    ) -> Optional[int]:
        """ka10006 주식시세 — venue별 종목코드(_AL/_NX/KRX)로 현재가."""
        stk = self.quote_stk_cd(stock_code, venue)
        if not stk:
            return None
        resp = await self._request_stockinfo_tr("ka10006", {"stk_cd": stk})
        if not resp.get("success"):
            logger.debug(
                f"ka10006 현재가 실패 ({stk}): {resp.get('error') or resp.get('return_msg') or ''}"
            )
            return None
        parsed = self.parse_ka10006_basic(
            resp.get("data") if isinstance(resp.get("data"), dict) else {}
        )
        px = int(parsed.get("current_price") or 0)
        return px if px > 0 else None

    def peek_cached_price(self, stock_code: str) -> Optional[int]:
        """캐시에 남은 현재가. 네트워크 호출 없음."""
        try:
            stock_code = self.normalize_stock_code(stock_code)
            cached = self._price_cache.get(stock_code)
            if not cached:
                return None
            price, timestamp = cached
            age = datetime.now().timestamp() - float(timestamp)
            if age < self._price_cache_ttl and price:
                return int(price)
        except Exception:
            return None
        return None

    async def get_current_price(
        self, stock_code: str, *, priority=None, allow_off_hours: bool = False,
    ) -> Optional[int]:
        """종목 현재가 조회 (캐싱 적용).

        allow_off_hours: True면 KRX 정규장 외(NXT 연장 등)에도 API 호출.
        장외·연장 세션은 ka10006 통합(_AL)→NXT(_NX) 시세를 우선하고,
        실패 시 기존 KRX 일봉(ka10081)으로 폴백한다.
        """
        try:
            from utils.market_hours import is_krx_session

            stock_code = self.normalize_stock_code(stock_code)
            if stock_code in self._price_cache:
                price, timestamp = self._price_cache[stock_code]
                age = datetime.now().timestamp() - timestamp
                if age < self._price_cache_ttl:
                    logger.debug(f"💾 [CACHE_HIT] {stock_code} 캐시 사용 (나이: {age:.1f}초)")
                    return price

            if not is_krx_session() and not allow_off_hours:
                if stock_code in self._price_cache:
                    price, _ = self._price_cache[stock_code]
                    logger.debug(f"장외 시간 — 현재가 캐시 사용: {stock_code}")
                    return price
                logger.debug(f"장외 시간 — 현재가 조회 생략: {stock_code}")
                return None
            
            logger.debug(f"현재가 조회 시작: {stock_code}")
            
            if not self.token_manager.get_valid_token():
                logger.error("키움 API 토큰이 없습니다")
                return None

            if not await self._acquire_api_slot(
                f"get_current_price_{stock_code}", priority=priority,
            ):
                if stock_code in self._price_cache:
                    price, _ = self._price_cache[stock_code]
                    logger.debug(f"API 슬롯 대기 초과 — 캐시 사용: {stock_code}")
                    return price
                return None

            # NXT 연장 등: 일봉(KRX)은 정규장 종가에 고정될 수 있어 시세 TR 우선
            if allow_off_hours and not is_krx_session():
                for venue in ("AL", "NX"):
                    live_px = await self._get_current_price_ka10006(
                        stock_code, venue=venue,
                    )
                    if live_px and live_px > 0:
                        self._price_cache[stock_code] = (
                            live_px, datetime.now().timestamp(),
                        )
                        logger.info(
                            f"💾 현재가 조회 성공 (ka10006/{venue}): "
                            f"{stock_code} = {live_px:,}원"
                        )
                        return live_px
                logger.debug(
                    f"ka10006 AL/NX 실패 — KRX 일봉 폴백: {stock_code}"
                )
            
            current_price = await self._get_current_price_ka10081(stock_code)
            if current_price and current_price > 0:
                self._price_cache[stock_code] = (current_price, datetime.now().timestamp())
                logger.info(f"💾 현재가 조회 성공 (캐시 저장): {stock_code} = {current_price:,}원")
                return current_price

            # 개장 직후 등: 당일 일봉(ka10081)이 아직 생성되지 않아 0/실패로 오는 경우가 있어
            # 실시간 시세(ka10006)로 한 번 더 시도한다.
            live_px = await self._get_current_price_ka10006(stock_code, venue="KRX")
            if live_px and live_px > 0:
                self._price_cache[stock_code] = (live_px, datetime.now().timestamp())
                logger.info(f"💾 현재가 조회 성공 (ka10006 폴백): {stock_code} = {live_px:,}원")
                return live_px
            return None

        except Exception as e:
            logger.error(f"현재가 조회 중 오류: {e}")
            return None

    async def _get_current_price_ka10081(self, stock_code: str) -> Optional[int]:
        """ka10081 일봉 차트로 현재가(종가) 조회."""
        # 키움 API 호출 설정 - 실전/모의 분기
        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        endpoint = '/api/dostk/chart'
        url = host + endpoint

        # 요청 헤더
        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.token_manager.get_valid_token()}',
            'cont-yn': 'N',
            'next-key': '',
            'api-id': 'ka10081',  # 일봉 차트 API 사용
        }

        # 요청 데이터 (최근 1일 데이터만 조회)
        # 최근 거래일 계산 (주말 제외)
        today = now_kst()
        if today.weekday() == 5:  # 토요일
            base_dt = (today - timedelta(days=1)).strftime('%Y%m%d')
        elif today.weekday() == 6:  # 일요일
            base_dt = (today - timedelta(days=2)).strftime('%Y%m%d')
        else:  # 평일
            base_dt = today.strftime('%Y%m%d')

        request_data = {
            'dmst_stex_tp': 'KRX',
            'stk_cd': stock_code,
            'period': '1D',
            'limit': 1,
            'base_dt': base_dt,  # 기준일자 추가
            'upd_stkpc_tp': '1'  # 주가 업데이트 타입 추가
        }

        # SSL 검증 완화 및 타임아웃 설정 (모의투자 서버 연결 문제 해결)
        timeout = aiohttp.ClientTimeout(total=60, connect=20, sock_read=30)
        # SSL 컨텍스트 생성 - 인증서 검증 비활성화
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_context)

        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.post(url, headers=headers, json=request_data) as response:
                    if response.status == 200:
                        try:
                            response_data = await response.json()

                            # 응답 데이터 파싱 및 디버깅
                            rt_cd = response_data.get("rt_cd")
                            return_msg = response_data.get("return_msg", "")

                            # 디버깅: 전체 응답 구조 로깅
                            logger.debug(f"현재가 조회 응답 - {stock_code}: rt_cd={rt_cd}, return_msg={return_msg}")
                            logger.debug(f"응답 데이터 키: {list(response_data.keys())}")

                            # 성공 조건 확인 (rt_cd가 "0"이거나 "정상적으로 처리되었습니다" 메시지)
                            is_success = (rt_cd == "0" or rt_cd == "1" or "정상적으로 처리되었습니다" in return_msg)

                            if is_success:
                                # 다양한 가능한 필드명 시도
                                chart_list = None
                                possible_fields = [
                                    'stk_dt_pole_chart_qry',
                                    'output',
                                    'data',
                                    'chart_data',
                                    'stock_data'
                                ]

                                for field in possible_fields:
                                    if field in response_data and response_data[field]:
                                        chart_list = response_data[field]
                                        logger.debug(f"데이터 필드 발견: {field}")
                                        break

                                if chart_list and len(chart_list) > 0:
                                    # 다양한 가격 필드명 시도
                                    price_fields = ['cur_prc', 'close_price', 'price', 'current_price', 'close', 'last_price']
                                    current_price = None

                                    for price_field in price_fields:
                                        if price_field in chart_list[0]:
                                            current_price = int(chart_list[0].get(price_field, 0))
                                            logger.debug(f"가격 필드 발견: {price_field} = {current_price}")
                                            break

                                    if current_price and current_price > 0:
                                        return current_price
                                    logger.warning(f"유효한 가격 데이터 없음: {stock_code}")
                                    logger.debug(f"차트 데이터: {chart_list[0]}")
                                    return None
                                logger.warning(f"차트 데이터 없음: {stock_code}")
                                logger.debug(f"전체 응답: {response_data}")
                                return None
                            logger.error(f"현재가 조회 실패: {stock_code} - rt_cd={rt_cd}, return_msg={return_msg}")
                            return None

                        except json.JSONDecodeError as e:
                            logger.error(f"현재가 조회 응답 파싱 실패: {e}")
                            return None
                    else:
                        # 429 에러는 Rate Limit 초과를 의미
                        if response.status == 429:
                            logger.error(f"❌ [429 ERROR] API 호출 제한 초과!")
                            logger.error(f"   - 종목코드: {stock_code}")
                            logger.error(f"   - API URL: {url}")
                            logger.error(f"   - 응답 헤더: {dict(response.headers)}")

                            # 응답 본문 확인
                            try:
                                error_body = await response.text()
                                logger.error(f"   - 응답 본문: {error_body}")
                            except:
                                pass

                            logger.error(f"   ⚠️  해결방법:")
                            logger.error(f"      1. API 호출 간격을 더 늘리세요 (현재: {api_rate_limiter.min_call_interval}초)")
                            logger.error(f"      2. 동시에 여러 종목 조회를 줄이세요")
                            logger.error(f"      3. 키움 API 제한 정책 확인: 1초당 1회, 1분당 20회")
                        else:
                            logger.error(f"현재가 조회 API 호출 실패: HTTP {response.status}")
                            try:
                                error_body = await response.text()
                                logger.error(f"   - 오류 내용: {error_body}")
                            except:
                                pass

                        return None
        except Exception as e:
            logger.error(f"현재가 조회(ka10081) 중 오류: {e}")
            return None

    async def _request_stockinfo_tr(self, api_id: str, request_data: Dict) -> Dict:
        """국내주식 시세/호가 TR 호출 공용 함수 (환경별 URI 차이를 자동 재시도)."""
        if not self.token_manager.get_valid_token():
            return {"success": False, "error": "토큰 없음"}

        try:
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            # 키움 REST 게이트웨이는 계정/버전에 따라 API ID별 허용 URI가 다를 수 있다.
            # 1504(해당 URI에서 지원하지 않는 API ID) 발생 시 다음 URI로 재시도한다.
            endpoint_candidates = ["/api/dostk/stkinfo", "/api/dostk/chart", "/api/dostk/iteminfo"]
            if api_id == "ka10004":
                # 주식호가 — 공식 URI는 /api/dostk/mrkcond (hoga/stkinfo 등은 1504)
                endpoint_candidates = [
                    "/api/dostk/mrkcond",
                    "/api/dostk/hoga",
                    "/api/dostk/stkinfo",
                ]
            elif api_id in ("ka10008", "ka10009", "ka10131"):
                endpoint_candidates = ["/api/dostk/frgnistt", "/api/dostk/stkinfo", "/api/dostk/mrkcond"]
            elif api_id in ("ka10045", "ka10063", "ka10066"):
                endpoint_candidates = ["/api/dostk/mrkcond", "/api/dostk/stkinfo"]
            elif api_id in ("ka90005", "ka90006", "ka90007", "ka90008", "ka90010", "ka90013"):
                endpoint_candidates = ["/api/dostk/mrkcond", "/api/dostk/stkinfo"]
            elif api_id in ("ka90003", "ka90004"):
                endpoint_candidates = ["/api/dostk/stkinfo", "/api/dostk/mrkcond"]
            elif api_id in ("ka90001", "ka90002"):
                endpoint_candidates = ["/api/dostk/thme"]
            elif api_id == "ka10006":
                # 주식시분요청도 시세(mrkcond) 계열
                endpoint_candidates = [
                    "/api/dostk/mrkcond",
                    "/api/dostk/stkinfo",
                    "/api/dostk/chart",
                    "/api/dostk/iteminfo",
                ]

            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {self.token_manager.get_valid_token()}",
                "cont-yn": "N",
                "next-key": "",
                "api-id": api_id,
            }

            logger.info(f"[TR_REQUEST] api_id={api_id}, stock={request_data.get('stk_cd','')}, endpoint_candidates={endpoint_candidates}")
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                last_error = ""
                for endpoint in endpoint_candidates:
                    url = host + endpoint
                    logger.info(f"[TR_ATTEMPT] api_id={api_id}, endpoint={endpoint}, request_data={request_data}")
                    async with session.post(url, headers=headers, json=request_data) as response:
                        body_text = await response.text()
                        if response.status != 200:
                            last_error = f"HTTP {response.status} @ {endpoint}"
                            logger.warning(f"[TR_HTTP_FAIL] api_id={api_id}, endpoint={endpoint}, status={response.status}, body={body_text[:500]}")
                            continue

                        try:
                            data = json.loads(body_text)
                        except json.JSONDecodeError:
                            last_error = f"JSON 파싱 실패 @ {endpoint}"
                            logger.error(f"[TR_JSON_FAIL] api_id={api_id}, endpoint={endpoint}, body={body_text[:500]}")
                            continue

                        ok = (data.get("return_code") == 0) or (data.get("rt_cd") == "0")
                        logger.info(
                            f"[TR_RESPONSE] api_id={api_id}, endpoint={endpoint}, "
                            f"return_code={data.get('return_code')}, rt_cd={data.get('rt_cd')}, "
                            f"return_msg={data.get('return_msg') or data.get('msg1') or ''}"
                        )
                        if ok:
                            sample_keys = list(data.keys())[:20]
                            logger.info(f"[TR_SUCCESS] api_id={api_id}, endpoint={endpoint}, keys={sample_keys}")
                            return {"success": True, "data": data, "endpoint": endpoint}

                        msg = data.get("return_msg") or data.get("msg1") or "TR 호출 실패"
                        # URI/API-ID 매핑 오류(1504)면 다음 URI 재시도
                        if "1504" in str(msg) or "지원하는 API ID가 아닙니다" in str(msg):
                            last_error = f"{msg} @ {endpoint}"
                            logger.warning(f"[TR_RETRY_URI] api_id={api_id}, endpoint={endpoint}, msg={msg}")
                            continue

                        logger.error(f"[TR_FAIL] api_id={api_id}, endpoint={endpoint}, msg={msg}, body_keys={list(data.keys())[:20]}")
                        return {"success": False, "error": f"{msg} @ {endpoint}", "raw": data}

                logger.error(f"[TR_ALL_FAILED] api_id={api_id}, last_error={last_error}, request_data={request_data}")
                return {"success": False, "error": last_error or "TR 호출 실패(모든 URI 재시도 실패)"}
        except Exception as e:
            logger.exception(f"[TR_EXCEPTION] api_id={api_id}, request_data={request_data}, error={e}")
            return {"success": False, "error": str(e)}
