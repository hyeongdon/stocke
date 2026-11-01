from __future__ import annotations

import asyncio
import json
import time
from typing import Dict, List, Optional

import aiohttp

from api.api_rate_limiter import api_rate_limiter
from api.kiwoom_api_shared import (
    _ACCOUNT_BALANCE_FRESH_SEC,
    _account_balance_cache,
    _get_balance_fetch_lock,
    _parse_kiwoom_int,
    _parse_kiwoom_rate,
    _stale_account_balance,
    invalidate_account_balance_cache,
    logger,
)
from core.config import Config
from utils.api_traffic_guard import APIPriority

__all__ = ["_AccountMixin"]

_balance_inflight: Optional[asyncio.Task] = None


class _AccountMixin:
    async def get_account_profit(self, stex_tp: str = "0", limit: int = 500) -> Dict:
        """ka10085: 계좌수익률요청 - 보유종목별 수익현황 조회"""
        if not self.token_manager.get_valid_token():
            logger.error("키움 API 토큰이 없습니다")
            return {"positions": [], "_data_source": "API_ERROR"}

        try:
            # API 제한 확인 및 기록
            if not api_rate_limiter.is_api_available():
                logger.warning("🚫 [KIWOOM_API] API 제한 상태로 인해 손익 조회 건너뜀")
                return {"positions": [], "_data_source": "API_ERROR"}
            
            # API 호출 기록 (간격 체크 포함)
            if not api_rate_limiter.record_api_call("get_account_profit"):
                logger.warning("🚫 [KIWOOM_API] API 호출 간격 부족으로 손익 조회 건너뜀")
                return {"positions": [], "_data_source": "API_ERROR"}
            
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            url = host + "/api/dostk/acnt"

            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'api-id': 'ka10085',
            }

            body = {
                'stex_tp': stex_tp,
            }

            positions: List[Dict] = []
            cont_yn, next_key = 'N', ''

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                while True:
                    h = dict(headers)
                    h['cont-yn'] = cont_yn
                    h['next-key'] = next_key

                    async with session.post(url, headers=h, json=body) as resp:
                        text = await resp.text()
                        if resp.status != 200:
                            logger.error(f"ka10085 호출 실패: {resp.status} {text}")
                            break
                        try:
                            data = json.loads(text)
                        except json.JSONDecodeError:
                            logger.error(f"ka10085 JSON 파싱 실패: {text}")
                            break

                        if data.get('return_code') != 0:
                            logger.error(f"ka10085 오류: {data}")
                            break

                        for it in data.get('acnt_prft_rt', []) or []:
                            def _to_int(v: str) -> int:
                                try:
                                    if isinstance(v, str) and (v.startswith('+') or v.startswith('-')):
                                        return int(v.replace('+',''))
                                    return int(v)
                                except Exception:
                                    return 0

                            positions.append({
                                "stock_code": it.get('stk_cd', ''),
                                "stock_name": it.get('stk_nm', ''),
                                "quantity": _to_int(it.get('rmnd_qty', '0')),
                                "avg_price": _to_int(it.get('pur_pric', '0')),
                                "purchase_amount": _to_int(it.get('pur_amt', '0')),
                                "current_price_delta": _to_int(it.get('cur_prc', '0')),
                                "today_pl": _to_int(it.get('tdy_sel_pl', '0')),
                                "commission_today": _to_int(it.get('tdy_trde_cmsn', '0')),
                                "tax_today": _to_int(it.get('tdy_trde_tax', '0')),
                                "credit_type": it.get('crd_tp', ''),
                                "loan_date": it.get('loan_dt', ''),
                                "settle_remain": _to_int(it.get('setl_remn', '0')),
                            })
                            if len(positions) >= limit:
                                break

                        if len(positions) >= limit:
                            break

                        cont_yn = resp.headers.get('cont-yn', 'N')
                        next_key = resp.headers.get('next-key', '')
                        if cont_yn != 'Y' or not next_key:
                            break

            return {
                "positions": positions[:limit],
                "_data_source": "REAL_API",
                "_api_connected": True,
                "_token_valid": True,
            }

        except Exception as e:
            logger.error(f"계좌수익률 요청 오류: {e}")
            return {"positions": [], "_data_source": "API_ERROR"}

    async def place_buy_order(
        self,
        stock_code: str,
        quantity: int,
        price: int = 0,
        order_type: str = "3",
        *,
        dmst_stex_tp: str = "KRX",
    ) -> Dict:
        """주식 매수 주문 (키움 API kt10000 스펙).

        dmst_stex_tp: KRX | NXT | SOR. NXT 연장 세션은 시장가 미지원 → SOR+지정가 권장.
        """
        if not self.token_manager.get_valid_token():
            logger.error("키움 API 토큰이 없습니다")
            return {"success": False, "error": "토큰 없음"}
            
        try:
            # 계좌 타입에 따른 도메인 설정
            use_mock_account = Config.KIWOOM_USE_MOCK_ACCOUNT
            if use_mock_account:
                host = Config.KIWOOM_MOCK_API_URL
            else:
                host = Config.KIWOOM_REAL_API_URL
            
            endpoint = '/api/dostk/ordr'
            url = host + endpoint

            # 계좌번호 (모의/실전 분기)
            account_no = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
            if not account_no:
                logger.error("매수 주문 실패 - 계좌번호가 설정되지 않았습니다")
                return {"success": False, "error": "계좌번호 없음"}
            
            # appkey/appsecret (실전/모의 분기) - 일부 엔드포인트에서 필수일 수 있음
            app_key = Config.KIWOOM_MOCK_APP_KEY if use_mock_account else Config.KIWOOM_APP_KEY
            app_secret = Config.KIWOOM_MOCK_APP_SECRET if use_mock_account else Config.KIWOOM_APP_SECRET

            # 주문 요청 데이터 (kt10000 스펙)
            account_pw = Config.KIWOOM_MOCK_ACCOUNT_PASSWORD if use_mock_account else Config.KIWOOM_ACCOUNT_PASSWORD
            stock_code = self.normalize_stock_code(stock_code)
            stex = (dmst_stex_tp or "KRX").strip().upper()
            if stex not in ("KRX", "NXT", "SOR"):
                stex = "KRX"
            request_data = {
                'dmst_stex_tp': stex,  # 국내거래소구분 KRX,NXT,SOR
                'acnt_no': account_no,  # 계좌번호
                'stk_cd': stock_code,   # 종목코드
                'ord_qty': str(quantity),  # 주문수량
                # 주문단가: API 구현에 따라 시장가에서도 "0"을 요구하는 경우가 있어 0으로 고정
                'ord_uv': str(price if price > 0 else 0),
                'trde_tp': order_type,  # 매매구분 (3:시장가, 0:보통)
                'cond_uv': '0',  # 조건단가
                'ord_side_cd': '1',  # 매수 주문 (1:매수, 2:매도)
            }
            # 계좌 비밀번호가 설정된 경우만 포함(필수인 환경에서 RC4058 해결 가능)
            if account_pw:
                request_data['acnt_pwd'] = account_pw
                request_data['acnt_pw'] = account_pw

            order_kind = "시장가" if order_type == "3" else "지정가"
            logger.info(
                f"매수 주문 요청: {stock_code}, 수량: {quantity}, "
                f"가격: {price}, 타입: {order_type}({order_kind}), 거래소: {stex}"
            )

            async def _do_request() -> tuple[int, Optional[dict], str]:
                headers = {
                    'Content-Type': 'application/json;charset=UTF-8',
                    'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                    'appkey': app_key,
                    'appsecret': app_secret,
                    'cont-yn': 'N',
                    'next-key': '',
                    'api-id': 'kt10000',
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        headers=headers,
                        json=request_data,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as response:
                        response_text = await response.text()
                        if response.status != 200:
                            return response.status, None, response_text
                        try:
                            return response.status, json.loads(response_text), response_text
                        except json.JSONDecodeError:
                            return response.status, None, response_text

            for attempt in range(2):
                resp_status, data, response_text = await _do_request()
                logger.info(f"매수 주문 응답: {resp_status} - {response_text}")

                if resp_status == 200 and data:
                    return_code = data.get("return_code")
                    rt_cd = data.get("rt_cd")
                    success = (return_code == 0) or (rt_cd == "0")

                    if success:
                        order_no = data.get('ord_no', '') or data.get("order_no", "")
                        logger.info(f"매수 주문 성공: {stock_code} - 주문번호: {order_no}")
                        invalidate_account_balance_cache()
                        return {
                            "success": True,
                            "order_id": order_no,
                            "order_no": order_no,
                            "message": data.get('return_msg') or data.get("msg1") or '정상적으로 처리되었습니다',
                        }

                    error_msg = data.get('return_msg') or data.get("msg1") or '알 수 없는 오류'
                    if attempt == 0 and self._is_kiwoom_token_invalid(error_msg, data):
                        logger.warning("🔑 [TOKEN] 매수 주문 토큰 무효(8005) — 재인증 후 1회 재시도")
                        if await self._reauthenticate_async():
                            continue
                    logger.error(f"매수 주문 실패: {error_msg}")
                    return {
                        "success": False,
                        "error": error_msg,
                        "_request": request_data,
                        "_response": data,
                    }

                err = response_text or f"HTTP {resp_status}"
                if attempt == 0 and self._is_kiwoom_token_invalid(err, data):
                    logger.warning("🔑 [TOKEN] 매수 주문 HTTP 오류 중 토큰 무효 — 재인증 후 1회 재시도")
                    if await self._reauthenticate_async():
                        continue
                logger.error(f"매수 주문 API 호출 실패: {resp_status}")
                return {
                    "success": False,
                    "error": f"API 호출 실패: {resp_status}" if resp_status != 200 else "응답 파싱 실패",
                }
                        
        except Exception as e:
            logger.error(f"매수 주문 중 오류: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def place_sell_order(
        self,
        stock_code: str,
        quantity: int,
        price: int = 0,
        order_type: str = "3",
        *,
        dmst_stex_tp: str = "KRX",
    ) -> Dict:
        """주식 매도 주문 (키움 API kt10001 스펙 — kt10000은 매수 전용).

        dmst_stex_tp: KRX | NXT | SOR. NXT 연장 세션은 시장가 미지원 → SOR+지정가 권장.
        """
        if not self.token_manager.get_valid_token():
            logger.error("키움 API 토큰이 없습니다")
            return {"success": False, "error": "토큰 없음"}

        stock_code = self.normalize_stock_code(stock_code)
        stex = (dmst_stex_tp or "KRX").strip().upper()
        if stex not in ("KRX", "NXT", "SOR"):
            stex = "KRX"
            
        try:
            # 계좌 타입에 따른 도메인 설정
            use_mock_account = Config.KIWOOM_USE_MOCK_ACCOUNT
            if use_mock_account:
                host = Config.KIWOOM_MOCK_API_URL
            else:
                host = Config.KIWOOM_REAL_API_URL
            
            endpoint = '/api/dostk/ordr'
            url = host + endpoint

            # 계좌번호 (모의/실전 분기)
            account_no = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER
            if not account_no:
                logger.error("매도 주문 실패 - 계좌번호가 설정되지 않았습니다")
                return {"success": False, "error": "계좌번호 없음"}
            
            # appkey/appsecret (실전/모의 분기) - 일부 엔드포인트에서 필수일 수 있음
            app_key = Config.KIWOOM_MOCK_APP_KEY if use_mock_account else Config.KIWOOM_APP_KEY
            app_secret = Config.KIWOOM_MOCK_APP_SECRET if use_mock_account else Config.KIWOOM_APP_SECRET

            # 요청 헤더 (kt10001 = 매도 주문)
            headers = {
                'Content-Type': 'application/json;charset=UTF-8',
                'authorization': f'Bearer {self.token_manager.get_valid_token()}',
                'appkey': app_key,
                'appsecret': app_secret,
                'cont-yn': 'N',  # 연속조회여부
                'next-key': '',  # 연속조회키
                'api-id': 'kt10001',  # TR명 (매도)
            }
            
            # 주문 요청 데이터 (kt10001 스펙)
            account_pw = Config.KIWOOM_MOCK_ACCOUNT_PASSWORD if use_mock_account else Config.KIWOOM_ACCOUNT_PASSWORD
            request_data = {
                'dmst_stex_tp': stex,
                'acnt_no': account_no,  # 계좌번호
                'stk_cd': stock_code,   # 종목코드
                'ord_qty': str(quantity),  # 주문수량
                'ord_uv': str(price if price > 0 else 0),
                'trde_tp': order_type,  # 매매구분 (3:시장가, 0:보통)
                'cond_uv': '0',  # 조건단가
            }
            if account_pw:
                request_data['acnt_pwd'] = account_pw
                request_data['acnt_pw'] = account_pw
            
            logger.info(
                f"매도 주문 요청: {stock_code}, 수량: {quantity}, 가격: {price}, "
                f"타입: {order_type}, 거래소: {stex}"
            )
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=request_data) as response:
                    if response.status == 200:
                        try:
                            response_text = await response.text()
                            logger.info(f"매도 주문 응답: {response.status} - {response_text}")

                            response_data = json.loads(response_text)

                            # 키움 응답 키가 버전에 따라 다를 수 있어 둘 다 허용
                            return_code = response_data.get("return_code")
                            rt_cd = response_data.get("rt_cd")
                            success = (return_code == 0) or (rt_cd == "0")

                            if success:
                                order_no = response_data.get("ord_no", "") or response_data.get("order_no", "")
                                invalidate_account_balance_cache()
                                return {
                                    "success": True,
                                    "order_id": order_no,
                                    "order_no": order_no,
                                    "message": response_data.get("return_msg") or response_data.get("msg1") or "매도 주문이 성공적으로 접수되었습니다."
                                }

                            error_msg = response_data.get("return_msg") or response_data.get("msg1") or "매도 주문 실패"
                            logger.error(f"매도 주문 실패: {error_msg}")
                            return {
                                "success": False,
                                "error": error_msg,
                                "_request": request_data,
                                "_response": response_data,
                            }
                        except json.JSONDecodeError as e:
                            logger.error(f"매도 주문 응답 파싱 실패: {e}")
                            return {
                                "success": False,
                                "error": "응답 파싱 실패"
                            }
                    else:
                        logger.error(f"매도 주문 API 호출 실패: {response.status}")
                        return {
                            "success": False,
                            "error": f"API 호출 실패: {response.status}"
                        }
                        
        except Exception as e:
            logger.error(f"매도 주문 중 오류: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def get_unfilled_orders(
        self,
        *,
        stock_code: str = "",
        trde_tp: str = "0",
        stex_tp: str = "0",
        max_pages: int = 10,
    ) -> Dict:
        """미체결 주문 조회 (ka10075, /api/dostk/acnt).

        trde_tp: 0 전체 / 1 매도 / 2 매수
        반환 items: ord_no, stk_cd, ord_qty, oso_qty, io_tp_nm, trde_tp 등.
        """
        token = self.token_manager.get_valid_token()
        if not token:
            return {"success": False, "error": "토큰 없음", "items": []}

        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        account_no = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if use_mock else Config.KIWOOM_ACCOUNT_NUMBER
        code = self.normalize_stock_code(stock_code) if stock_code else ""
        url = host + "/api/dostk/acnt"
        headers_base = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10075",
        }
        body = {
            "all_stk_tp": "1" if code else "0",
            "trde_tp": str(trde_tp or "0"),
            "stk_cd": code,
            "stex_tp": stex_tp,
            "acnt_no": account_no or "",
        }

        items: List[Dict] = []
        cont_yn, next_key = "N", ""
        pages = 0

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                while pages < max_pages:
                    pages += 1
                    headers = {**headers_base, "cont-yn": cont_yn, "next-key": next_key}
                    async with session.post(url, headers=headers, json=body) as resp:
                        text = await resp.text()
                        resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                        resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""

                        if resp.status != 200:
                            logger.warning(f"[UNFILLED] HTTP {resp.status} body={text[:300]}")
                            return {"success": False, "error": f"HTTP {resp.status}", "items": items}

                        data = json.loads(text)

                    ok = (
                        (data.get("return_code") == 0)
                        or (data.get("returnCode") == 0)
                        or (data.get("rt_cd") == "0")
                    )
                    if not ok:
                        msg = data.get("return_msg") or data.get("returnMsg") or data.get("msg1") or "조회 실패"
                        logger.warning(f"[UNFILLED] fail msg={msg}")
                        if items:
                            break
                        return {"success": False, "error": msg, "items": []}

                    rows = data.get("oso") or data.get("output") or data.get("unfilled_list") or []
                    if isinstance(rows, dict):
                        rows = [rows]
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        items.append({
                            "ord_no": str(r.get("ord_no") or r.get("order_no") or ""),
                            "stk_cd": self.normalize_stock_code(str(r.get("stk_cd") or "")),
                            "stk_nm": str(r.get("stk_nm") or ""),
                            "ord_qty": _parse_kiwoom_int(r.get("ord_qty", "0")),
                            "oso_qty": _parse_kiwoom_int(r.get("oso_qty", "0")),
                            "io_tp_nm": str(r.get("io_tp_nm") or ""),
                            "trde_tp": str(r.get("trde_tp") or ""),
                            "ord_stt": str(r.get("ord_stt") or ""),
                            "orig_ord_no": str(r.get("orig_ord_no") or ""),
                        })

                    cont_yn = data.get("cont_yn") or resp_cont or "N"
                    next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                    if str(cont_yn).upper() != "Y" or not next_key:
                        break

            logger.info(f"[UNFILLED] ka10075 success count={len(items)} pages={pages}")
            return {"success": True, "items": items}
        except Exception as e:
            logger.exception(f"[UNFILLED] error={e}")
            return {"success": False, "error": str(e), "items": items}

    async def cancel_order(
        self,
        stock_code: str,
        order_no: str,
        quantity: int = 0,
    ) -> Dict:
        """주식 주문 취소 (kt10003, /api/dostk/ordr)."""
        if not self.token_manager.get_valid_token():
            logger.error("키움 API 토큰이 없습니다")
            return {"success": False, "error": "토큰 없음"}

        stock_code = self.normalize_stock_code(stock_code)
        order_no = str(order_no or "").strip()
        if not order_no:
            return {"success": False, "error": "원주문번호 없음"}

        try:
            use_mock_account = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock_account else Config.KIWOOM_REAL_API_URL
            account_no = (
                Config.KIWOOM_MOCK_ACCOUNT_NUMBER
                if use_mock_account
                else Config.KIWOOM_ACCOUNT_NUMBER
            )
            if not account_no:
                return {"success": False, "error": "계좌번호 없음"}

            app_key = Config.KIWOOM_MOCK_APP_KEY if use_mock_account else Config.KIWOOM_APP_KEY
            app_secret = Config.KIWOOM_MOCK_APP_SECRET if use_mock_account else Config.KIWOOM_APP_SECRET
            url = host + "/api/dostk/ordr"
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {self.token_manager.get_valid_token()}",
                "appkey": app_key,
                "appsecret": app_secret,
                "cont-yn": "N",
                "next-key": "",
                "api-id": "kt10003",
            }
            cncl_qty = max(0, int(quantity or 0))
            request_data = {
                "dmst_stex_tp": "KRX",
                "acnt_no": account_no,
                "stk_cd": stock_code,
                "orig_ord_no": order_no,
                "cncl_qty": str(cncl_qty),  # 0이면 잔량 전량 취소로 처리되는 경우가 많음
            }
            account_pw = (
                Config.KIWOOM_MOCK_ACCOUNT_PASSWORD
                if use_mock_account
                else Config.KIWOOM_ACCOUNT_PASSWORD
            )
            if account_pw:
                request_data["acnt_pwd"] = account_pw
                request_data["acnt_pw"] = account_pw

            logger.info(f"주문 취소 요청: {stock_code} ord_no={order_no} qty={cncl_qty}")
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=request_data) as response:
                    response_text = await response.text()
                    logger.info(f"주문 취소 응답: {response.status} - {response_text[:500]}")
                    if response.status != 200:
                        return {"success": False, "error": f"API 호출 실패: {response.status}"}
                    try:
                        response_data = json.loads(response_text)
                    except json.JSONDecodeError:
                        return {"success": False, "error": "응답 파싱 실패"}

                    return_code = response_data.get("return_code")
                    rt_cd = response_data.get("rt_cd")
                    success = (return_code == 0) or (rt_cd == "0")
                    if success:
                        invalidate_account_balance_cache()
                        return {
                            "success": True,
                            "order_id": response_data.get("ord_no") or order_no,
                            "message": response_data.get("return_msg")
                            or response_data.get("msg1")
                            or "주문 취소 접수",
                        }
                    error_msg = (
                        response_data.get("return_msg")
                        or response_data.get("msg1")
                        or "주문 취소 실패"
                    )
                    return {"success": False, "error": error_msg, "_response": response_data}
        except Exception as e:
            logger.error(f"주문 취소 중 오류: {e}")
            return {"success": False, "error": str(e)}

    async def get_account_balance(
        self,
        account_number: str = None,
        *,
        priority: Optional[APIPriority] = None,
        max_wait: float = 8.0,
        force_refresh: bool = False,
    ) -> Dict:
        """계좌 잔고 정보 조회 - 키움 API kt00004 (동시 호출은 하나로 합침).

        매수/손절 등 주문 경로에서는 priority=HIGH·max_wait를 넉넉히 넘겨
        스캐너 LOW 트래픽에 밀려 '계좌 정보 조회 실패'로 매수가 지연되지 않게 한다.
        force_refresh=True면 15초 캐시를 건너뛰고 재조회(체결 직후 동기화용).
        """
        if force_refresh:
            invalidate_account_balance_cache()

        now = time.monotonic()
        cached = _account_balance_cache.get("data")
        if (
            not force_refresh
            and cached
            and now - _account_balance_cache.get("at", 0.0) < _ACCOUNT_BALANCE_FRESH_SEC
        ):
            out = dict(cached)
            out["_cached"] = True
            return out

        lock = _get_balance_fetch_lock()
        async with lock:
            now = time.monotonic()
            cached = _account_balance_cache.get("data")
            if (
                not force_refresh
                and cached
                and now - _account_balance_cache.get("at", 0.0) < _ACCOUNT_BALANCE_FRESH_SEC
            ):
                out = dict(cached)
                out["_cached"] = True
                return out

            global _balance_inflight
            # force_refresh는 주문 직후 동기화 — 진행 중 조회(체결 전 잔고)를 재사용하지 않음
            if (
                not force_refresh
                and _balance_inflight is not None
                and not _balance_inflight.done()
            ):
                task = _balance_inflight
            else:
                task = asyncio.create_task(
                    self._fetch_account_balance(
                        account_number,
                        priority=priority,
                        max_wait=max_wait,
                    )
                )
                _balance_inflight = task

        try:
            return await task
        finally:
            async with lock:
                if _balance_inflight is task:
                    _balance_inflight = None

    async def _fetch_account_balance(
        self,
        account_number: str = None,
        *,
        priority: Optional[APIPriority] = None,
        max_wait: float = 8.0,
    ) -> Dict:
        """계좌 잔고 실제 조회 (get_account_balance에서 단일 inflight로만 호출)."""
        if not self.token_manager.get_valid_token():
            stale = _stale_account_balance("token_missing")
            if stale:
                return stale
            logger.error("키움 API 토큰이 없습니다")
            return {"_error": "no_token", "_error_msg": "토큰 없음"}

        slot_priority = priority if priority is not None else APIPriority.NORMAL
        slot_wait = max(1.0, float(max_wait or 8.0))
        try:
            if not await self._acquire_api_slot(
                "get_account_balance",
                max_wait=slot_wait,
                priority=slot_priority,
            ):
                stale = _stale_account_balance("rate_limit")
                if stale:
                    return stale
                return {"_error": "rate_limited", "_error_msg": "API 호출 제한 또는 슬롯 대기 초과"}

            # 계좌번호 설정 (매개변수 우선, 없으면 환경변수 사용)
            if not account_number:
                account_number = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if Config.KIWOOM_USE_MOCK_ACCOUNT else Config.KIWOOM_ACCOUNT_NUMBER

            if not account_number:
                logger.error("계좌번호가 설정되지 않았습니다 (KIWOOM_ACCOUNT_NUMBER / KIWOOM_MOCK_ACCOUNT_NUMBER)")
                return {}
            
            # 계좌 타입에 따른 도메인 설정
            use_mock_account = Config.KIWOOM_USE_MOCK_ACCOUNT
            if use_mock_account:
                # 모의투자 계좌인 경우
                host = Config.KIWOOM_MOCK_API_URL  # 모의투자용
                account_type = "모의투자"
            else:
                # 실계좌인 경우
                host = Config.KIWOOM_REAL_API_URL  # 실전투자용
                account_type = "실계좌"
            
            endpoint = '/api/dostk/acnt'
            url = host + endpoint
            
            logger.info(f"계좌 타입: {account_type}, 도메인: {host}")
            
            # 요청 데이터 - 참고 소스와 동일하게 수정
            request_data = {
                'qry_tp': '0',         # 상장폐지조회구분 0:전체, 1:상장폐지종목제외
                'dmst_stex_tp': 'KRX', # 국내거래소구분 KRX:한국거래소,NXT:넥스트트레이드
                'acnt_no': account_number,  # 계좌번호 추가
            }
            # 디버깅을 위한 로깅 추가
            logger.info(f"키움 API 호출 ({account_type}): {url}")
            logger.info(f"계좌번호: {account_number}")
            logger.info(f"앱키 존재: {bool(Config.KIWOOM_APP_KEY)}")
            
            def _is_token_invalid(error_msg: str, data: dict) -> bool:
                msg = (error_msg or "") + " " + str(data or "")
                msg_lower = msg.lower()
                # 예: '인증에 실패했습니다[8005:Token이 유효하지 않습니다]'
                return ("8005" in msg_lower) or ("token" in msg_lower and ("invalid" in msg_lower or "유효하지" in msg_lower))

            async def _do_request() -> tuple[int, dict | None, str]:
                # 요청 헤더(토큰은 매 시도마다 최신 값으로 넣어야 함)
                headers = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {self.token_manager.get_valid_token()}",
                    "cont-yn": "N",        # 연속조회여부
                    "next-key": "",        # 연속조회키
                    "api-id": "kt00004",   # TR명
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        headers=headers,
                        json=request_data,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        response_text = await response.text()
                        logger.debug(f"계좌 조회 응답 상태: {response.status}")
                        if response.status != 200:
                            return response.status, None, response_text
                        try:
                            return response.status, json.loads(response_text), response_text
                        except json.JSONDecodeError as e:
                            return response.status, None, response_text

            # 1회 시도 + 토큰 무효(8005)면 1회 재인증 후 재시도
            for attempt in range(2):
                resp_status, data, raw_text = await _do_request()
                if resp_status != 200 or not data:
                    logger.error(f"키움 API 호출 실패: status={resp_status}")
                    if raw_text:
                        logger.error(f"오류 응답: {raw_text}")
                    stale = _stale_account_balance("http_or_json_error")
                    if stale:
                        return stale
                    return {"_error": "http_or_json_error", "_error_msg": f"HTTP {resp_status}"}

                return_code = data.get("return_code")
                logger.debug(f"계좌 조회 return_code: {return_code}")
                if return_code == 0:
                    result = self._parse_account_balance_safe(data)
                    _account_balance_cache.clear()
                    _account_balance_cache.update({"at": time.monotonic(), "data": result})
                    return result

                error_msg = data.get("msg1") or "알 수 없는 오류"
                logger.error(f"키움 API 계좌조회 오류: {error_msg}")
                logger.error(f"전체 응답: {data}")

                if attempt == 0 and _is_token_invalid(error_msg, data):
                    logger.warning("🔑 [TOKEN] 토큰 무효(8005) 감지 — 토큰 재인증 후 1회 재시도")
                    # 토큰 무효화 → 재인증
                    self.token_manager.access_token = None
                    self.token_manager.token_expiry = None
                    self.token_manager.refresh_token = self.token_manager.refresh_token  # 유지
                    if not self.authenticate():
                        logger.error("토큰 재인증 실패")
                        break

                    # 재시도 전에 슬롯/레이트리밋 다시 확인
                    if not await self._acquire_api_slot(
                        "get_account_balance",
                        max_wait=min(slot_wait, 8.0),
                        priority=slot_priority,
                    ):
                        break
                    continue

                stale = _stale_account_balance("api_error")
                if stale:
                    return stale
                return {"_error": "api_error", "_error_msg": error_msg}

        except aiohttp.ClientError as e:
            logger.error(f"HTTP 클라이언트 오류: {e}")
            stale = _stale_account_balance("client_error")
            if stale:
                return stale
            return {"_error": "client_error", "_error_msg": str(e)}
        except asyncio.TimeoutError:
            logger.error("키움 API 호출 타임아웃")
            stale = _stale_account_balance("timeout")
            if stale:
                return stale
            return {"_error": "timeout", "_error_msg": "키움 API 타임아웃"}
        except Exception as e:
            logger.error(f"계좌 정보 조회 중 예상치 못한 오류: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"스택 트레이스: {traceback.format_exc()}")
            stale = _stale_account_balance("unexpected")
            if stale:
                return stale
            return {"_error": "unexpected", "_error_msg": str(e)}

    def _parse_account_balance_safe(self, api_response: dict) -> dict:
        """키움 API 계좌 잔고 응답 파싱 - kt00004 필드명 정규화"""
        try:
            logger.debug("계좌 응답 파싱 시작")

            def amt_str(raw) -> str:
                return str(_parse_kiwoom_int(raw))

            def map_holding(item: dict) -> dict:
                if not isinstance(item, dict):
                    return {}
                mapped = {
                    "stk_cd": str(item.get("stk_cd") or ""),
                    "stk_nm": str(item.get("stk_nm") or ""),
                    "qty": amt_str(item.get("rmnd_qty") or item.get("qty", "0")),
                    "pur_amt": amt_str(item.get("pur_amt", "0")),
                    "evlt_amt": amt_str(item.get("evlt_amt", "0")),
                    "lspft_amt": amt_str(item.get("pl_amt") or item.get("lspft_amt", "0")),
                    "lspft_rt": _parse_kiwoom_rate(item.get("pl_rt") or item.get("lspft_rt", "0")),
                    "cur_pr": amt_str(item.get("cur_prc") or item.get("cur_pr", "0")),
                    "avg_pr": amt_str(item.get("avg_prc") or item.get("avg_pr", "0")),
                }
                # 매도가능/청산가능 — 키움 필드명이 버전마다 다름
                for sellable_key in ("clrn_alow_qty", "trde_able_qty", "ord_psbl_qty", "sellable_qty"):
                    raw = item.get(sellable_key)
                    if raw is None or raw == "":
                        continue
                    mapped["sellable_qty"] = amt_str(raw)
                    break
                return mapped

            stk_acnt_evlt_prst = []
            stk_data = api_response.get("stk_acnt_evlt_prst")
            if isinstance(stk_data, list):
                stk_acnt_evlt_prst = [map_holding(item) for item in stk_data if isinstance(item, dict)]
            elif isinstance(stk_data, dict):
                mapped = map_holding(stk_data)
                if mapped.get("stk_cd"):
                    stk_acnt_evlt_prst = [mapped]

            amount_fields = (
                "entr", "d2_entra", "tot_est_amt", "aset_evlt_amt", "tot_pur_amt",
                "prsm_dpst_aset_amt", "tot_grnt_sella", "tdy_lspft_amt", "invt_bsamt",
                "lspft_amt", "tdy_lspft", "lspft2", "lspft",
            )
            result = {
                "acnt_nm": str(api_response.get("acnt_nm") or ""),
                "brch_nm": str(api_response.get("brch_nm") or ""),
                "tdy_lspft_rt": _parse_kiwoom_rate(api_response.get("tdy_lspft_rt", "0")),
                "lspft_ratio": _parse_kiwoom_rate(api_response.get("lspft_ratio", "0")),
                "lspft_rt": _parse_kiwoom_rate(api_response.get("lspft_rt", "0")),
                "stk_acnt_evlt_prst": stk_acnt_evlt_prst,
            }
            for key in amount_fields:
                result[key] = amt_str(api_response.get(key, "0"))

            # 계좌 레벨 손익이 0이면 보유종목 pl_amt 합산
            sum_pl = sum(_parse_kiwoom_int(h.get("lspft_amt")) for h in stk_acnt_evlt_prst)
            if _parse_kiwoom_int(result.get("lspft_amt")) == 0 and sum_pl != 0:
                result["lspft_amt"] = str(sum_pl)
                result["lspft"] = str(sum_pl)
                tot_pur = _parse_kiwoom_int(result.get("tot_pur_amt"))
                if tot_pur > 0:
                    result["lspft_rt"] = f"{sum_pl / tot_pur * 100:.2f}"

            aset_amt = _parse_kiwoom_int(result["aset_evlt_amt"])
            tot_amt = _parse_kiwoom_int(result["tot_est_amt"])
            pl_amt = _parse_kiwoom_int(result["lspft_amt"])
            logger.info(
                f"계좌 파싱 완료 - 보유 {len(stk_acnt_evlt_prst)}개, "
                f"예수금 {_parse_kiwoom_int(result['entr']):,}원, "
                f"평가손익 {pl_amt:+,}원, 자산평가 {aset_amt:,}원"
            )
            return result

        except Exception as e:
            logger.error(f"계좌 잔고 응답 파싱 오류: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"스택 트레이스: {traceback.format_exc()}")
            return {}
