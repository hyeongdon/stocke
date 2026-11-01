from __future__ import annotations

import json
from typing import Dict, List

import aiohttp

from api.kiwoom_api_shared import _parse_kiwoom_int, logger
from core.config import Config

__all__ = ["_PortfolioMixin"]


class _PortfolioMixin:
    async def get_executions(
        self,
        sell_tp: str = "0",
        stk_cd: str = "",
        ord_no: str = "",
        stex_tp: str = "0",
        max_pages: int = 20,
    ) -> Dict:
        """체결 내역 조회 (ka10076, /api/dostk/acnt).
        sell_tp: 0 전체 / 1 매도 / 2 매수
        """
        token = self.token_manager.get_valid_token()
        if not token:
            return {"success": False, "error": "토큰 없음", "items": []}

        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        account_no = Config.KIWOOM_MOCK_ACCOUNT_NUMBER if use_mock else Config.KIWOOM_ACCOUNT_NUMBER
        url = host + "/api/dostk/acnt"
        headers_base = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10076",
        }
        body = {
            "stk_cd": stk_cd or "",
            "qry_tp": "0",
            "sell_tp": sell_tp,
            "ord_no": ord_no or "",
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
                            logger.warning(f"[EXECUTIONS] HTTP {resp.status} body={text[:300]}")
                            return {"success": False, "error": f"HTTP {resp.status}", "items": items}

                        data = json.loads(text)

                    ok = (data.get("return_code") == 0) or (data.get("returnCode") == 0) or (data.get("rt_cd") == "0")
                    if not ok:
                        msg = data.get("return_msg") or data.get("returnMsg") or data.get("msg1") or "조회 실패"
                        logger.warning(f"[EXECUTIONS] fail msg={msg}")
                        if items:
                            break
                        return {"success": False, "error": msg, "items": []}

                    rows = data.get("cntr") or data.get("output") or []
                    for r in rows:
                        items.append(r)

                    cont_yn = data.get("cont_yn") or resp_cont or "N"
                    next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                    if str(cont_yn).upper() != "Y" or not next_key:
                        break

            logger.info(f"[EXECUTIONS] ka10076 success count={len(items)} pages={pages}")
            return {"success": True, "items": items}
        except Exception as e:
            logger.exception(f"[EXECUTIONS] error={e}")
            return {"success": False, "error": str(e), "items": items}

    async def get_daily_stock_realized_pnl(
        self,
        strt_dt: str,
        end_dt: str,
        stk_cd: str = "",
        max_pages: int = 50,
    ) -> Dict:
        """일자별종목별실현손익 조회 (ka10073, /api/dostk/acnt).

        매도 체결이 발생한 종목/일자별로 실현손익을 반환한다.
        stk_cd를 비우면 계좌 전체 종목을 조회한다.
        각 행의 tdy_sel_pl은 수수료·거래세가 차감된 '순실현손익'이다.
        strt_dt/end_dt: YYYYMMDD
        """
        token = self.token_manager.get_valid_token()
        if not token:
            return {"success": False, "error": "토큰 없음", "items": []}

        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        url = host + "/api/dostk/acnt"
        headers_base = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10073",
        }
        body = {
            "stk_cd": stk_cd or "",
            "strt_dt": strt_dt,
            "end_dt": end_dt,
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
                            logger.warning(f"[REALIZED_PNL] HTTP {resp.status} body={text[:300]}")
                            return {"success": False, "error": f"HTTP {resp.status}", "items": items}

                        data = json.loads(text)

                    if data.get("return_code") != 0:
                        msg = data.get("return_msg") or "조회 실패"
                        logger.warning(f"[REALIZED_PNL] fail msg={msg}")
                        if items:
                            break
                        return {"success": False, "error": msg.strip(), "items": []}

                    rows = data.get("dt_stk_rlzt_pl") or []
                    items.extend(rows)

                    cont_yn = resp_cont or "N"
                    next_key = resp_next or ""
                    if str(cont_yn).upper() != "Y" or not next_key:
                        break

            logger.info(
                f"[REALIZED_PNL] ka10073 success count={len(items)} pages={pages} "
                f"period={strt_dt}~{end_dt}"
            )
            return {"success": True, "items": items}
        except Exception as e:
            logger.exception(f"[REALIZED_PNL] error={e}")
            return {"success": False, "error": str(e), "items": items}

    async def get_daily_realized_pnl(
        self,
        strt_dt: str,
        end_dt: str,
        max_pages: int = 50,
    ) -> Dict:
        """일자별실현손익 조회 (ka10074, /api/dostk/acnt).

        기간 내 일별 실현손익 합계. rlzt_pl은 기간 전체 실현손익 합계이다.
        strt_dt/end_dt: YYYYMMDD
        """
        token = self.token_manager.get_valid_token()
        if not token:
            return {"success": False, "error": "토큰 없음", "items": [], "rlzt_pl": 0}

        use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
        host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
        url = host + "/api/dostk/acnt"
        headers_base = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10074",
        }
        body = {"strt_dt": strt_dt, "end_dt": end_dt}

        items: List[Dict] = []
        cont_yn, next_key = "N", ""
        pages = 0
        rlzt_pl = 0

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
                            logger.warning(f"[DAILY_PNL] HTTP {resp.status} body={text[:300]}")
                            return {"success": False, "error": f"HTTP {resp.status}", "items": items, "rlzt_pl": rlzt_pl}

                        data = json.loads(text)

                    if data.get("return_code") != 0:
                        msg = data.get("return_msg") or "조회 실패"
                        logger.warning(f"[DAILY_PNL] fail msg={msg}")
                        if items:
                            break
                        return {"success": False, "error": msg.strip(), "items": [], "rlzt_pl": 0}

                    if pages == 1:
                        rlzt_pl = _parse_kiwoom_int(data.get("rlzt_pl"))

                    rows = data.get("dt_rlzt_pl") or []
                    items.extend(rows)

                    cont_yn = resp_cont or "N"
                    next_key = resp_next or ""
                    if str(cont_yn).upper() != "Y" or not next_key:
                        break

            logger.info(
                f"[DAILY_PNL] ka10074 success count={len(items)} pages={pages} "
                f"rlzt_pl={rlzt_pl} period={strt_dt}~{end_dt}"
            )
            return {"success": True, "items": items, "rlzt_pl": rlzt_pl}
        except Exception as e:
            logger.exception(f"[DAILY_PNL] error={e}")
            return {"success": False, "error": str(e), "items": items, "rlzt_pl": rlzt_pl}
