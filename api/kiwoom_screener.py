from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

import aiohttp

from api.api_rate_limiter import api_rate_limiter
from api.kiwoom_api_shared import (
    SANGTTA_CHANGE_RATE_RANK_FILTERS,
    SANGTTA_UNIVERSE_MIN_CHANGE_RATE,
    SCREENER_VOLUME_RANK_FILTERS,
    _normalize_stock_code,
    _parse_kiwoom_float,
    _parse_kiwoom_int,
    logger,
)
from core.config import Config
from utils.api_traffic_guard import APIPriority

__all__ = ["_ScreenerMixin"]


class _ScreenerMixin:
    @staticmethod
    def _is_etf_like_name(name: str) -> bool:
        """KODEX/TIGER 등 ETF·ETN·레버리지·인버스·곱버스 계열 종목명 판별."""
        nm = name or ""
        upper = nm.upper().replace(" ", "")
        if "ETN" in upper or "ETF" in upper:
            return True
        if ("인버스" in nm) or ("레버리지" in nm) or ("곱버스" in nm):
            return True
        if "INVERSE" in upper or "LEVERAGE" in upper:
            return True
        etf_prefixes = (
            "KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "SOL", "KOSEF", "KINDEX",
            "ACE", "PLUS", "RISE", "KIWOOM", "TIMEFOLIO", "히어로즈", "WOORI", "BNK", "FOCUS",
        )
        return upper.startswith(etf_prefixes)

    @staticmethod
    def classify_product_type(name: str) -> str:
        """종목명 기반 상품 종류 분류.
        반환: LEVERAGE(레버리지+2X) / INVERSE(인버스-1X) / DOUBLE_INVERSE(곱버스-2X) / ETF / ETN / STOCK
        """
        nm = name or ""
        upper = nm.upper().replace(" ", "")
        is_inverse = ("인버스" in nm) or ("INVERSE" in upper)
        is_double = ("곱버스" in nm) or ("2X" in upper) or ("2배" in nm)
        is_leverage = ("레버리지" in nm) or ("LEVERAGE" in upper)

        if is_inverse and is_double:
            return "DOUBLE_INVERSE"
        if is_inverse:
            return "INVERSE"
        if is_leverage or is_double:
            return "LEVERAGE"
        if "ETN" in upper:
            return "ETN"
        # 대표적인 ETF 브랜드 프리픽스(레버리지/인버스가 아닌 일반 ETF)
        etf_prefixes = (
            "KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "SOL", "KOSEF", "KINDEX",
            "ACE", "PLUS", "RISE", "KIWOOM", "TIMEFOLIO", "히어로즈", "WOORI", "BNK", "FOCUS",
        )
        if upper.startswith(etf_prefixes):
            return "ETF"
        return "STOCK"

    @staticmethod
    def _is_etf_family_item(name: str, product_type: Optional[str] = None) -> bool:
        """ETF/ETN/레버리지/인버스/곱버스 계열 여부 (mang_stk_incls=16이 놓치는 파생상품 포함)."""
        nm = (name or "").strip()
        if not nm:
            return False
        pt = product_type if product_type is not None else _ScreenerMixin.classify_product_type(nm)
        return pt != "STOCK" or _ScreenerMixin._is_etf_like_name(nm)

    @staticmethod
    def _is_screener_stock(name: str, product_type: Optional[str] = None) -> bool:
        """스크리너 편입 가능한 개별 주식인지 판별."""
        nm = (name or "").strip()
        if not nm:
            return False
        if _ScreenerMixin._is_etf_family_item(nm, product_type):
            return False
        if "스팩" in nm or "SPAC" in nm.upper():
            return False
        if "정리매매" in nm:
            return False
        if re.search(r"우(B|C)?$", nm) or nm.endswith("우선주"):
            return False
        return True

    @staticmethod
    def _post_filter_screener_items(items: List[Dict]) -> Tuple[List[Dict], int]:
        """스크리너 — 개별 주식만 남김 (ETF/ETN/레버리지/인버스/곱버스·SPAC·우선주·정리매매 제외).

        Returns:
            (kept_items, excluded_etf_count)
        """
        out: List[Dict] = []
        excluded_etf = 0
        for it in items:
            name = (it.get("stock_name") or "").strip()
            if not name:
                continue
            pt = it.get("product_type")
            if _ScreenerMixin._is_etf_family_item(name, pt):
                excluded_etf += 1
                continue
            if not _ScreenerMixin._is_screener_stock(name, pt):
                continue
            out.append(it)
        return out, excluded_etf

    @staticmethod
    def _parse_volume_rank_row(r: Dict) -> Dict:
        name = r.get("stk_nm", "")
        return {
            "stock_code": _normalize_stock_code(str(r.get("stk_cd", ""))),
            "stock_name": name,
            "current_price": abs(_parse_kiwoom_int(r.get("cur_prc", "0"))),
            "price_diff": _parse_kiwoom_int(r.get("pred_pre", "0")),
            "change_rate": _parse_kiwoom_float(r.get("flu_rt", "0")),
            "volume": abs(_parse_kiwoom_int(r.get("trde_qty", "0"))),
            "trade_amount": abs(_parse_kiwoom_int(r.get("trde_amt", "0"))),
            "product_type": _ScreenerMixin.classify_product_type(name),
        }

    async def get_volume_rank(
        self,
        market: str = "000",
        sort_tp: str = "3",
        limit: int = 50,
        *,
        screener_filters: bool = True,
        positive_change_only: Optional[bool] = None,
        min_change_rate: Optional[float] = None,
        max_change_rate: Optional[float] = None,
        min_trade_amount_eok: Optional[float] = None,
        mang_stk_incls: Optional[str] = None,
        crd_tp: Optional[str] = None,
        trde_qty_tp: Optional[str] = None,
        pric_tp: Optional[str] = None,
        trde_prica_tp: Optional[str] = None,
        mrkt_open_tp: Optional[str] = None,
        stex_tp: Optional[str] = None,
    ) -> Dict:
        """당일거래량상위 조회 (ka10030, /api/dostk/rkinfo).

        market: 000 전체 / 001 코스피 / 101 코스닥
        sort_tp: 1 거래량 / 2 거래회전율 / 3 거래대금
        screener_filters: True면 KRX·20만주+·관리/우선주 제외 등 스크리너 기본값 적용
        positive_change_only: True면 등락률>0만 채움(기본: screener_filters와 동일).
            API에 등락 필터가 없어 후처리하며, limit개 채울 때까지 페이징한다.
        min_change_rate: 지정 시 등락률>=이 값만 채움. screener_filters면
            Config.SCREENER_MIN_CHANGE_RATE(기본 3.3)를 쓰고, 0이면 플러스(>0)만.
        max_change_rate: 지정 시 등락률>=이 값은 과열로 제외. screener_filters면
            Config.SCREENER_MAX_CHANGE_RATE(기본 12). 0이면 상한 미적용.
        min_trade_amount_eok: 당일 거래대금 하한(억원). screener_filters면
            Config.SCREENER_MIN_TRADE_AMOUNT_EOK(기본 20). 0이면 하한 미적용.
            trde_amt 단위는 백만원이므로 내부에서 ×100 변환한다.
        """
        if positive_change_only is None:
            positive_change_only = bool(screener_filters)
        if min_change_rate is None and screener_filters:
            try:
                min_change_rate = float(Config.SCREENER_MIN_CHANGE_RATE)
            except (TypeError, ValueError, AttributeError):
                min_change_rate = 0.0
        if max_change_rate is None and screener_filters:
            try:
                max_change_rate = float(Config.SCREENER_MAX_CHANGE_RATE)
            except (TypeError, ValueError, AttributeError):
                max_change_rate = 0.0
        if min_trade_amount_eok is None and screener_filters:
            try:
                min_trade_amount_eok = float(Config.SCREENER_MIN_TRADE_AMOUNT_EOK)
            except (TypeError, ValueError, AttributeError):
                min_trade_amount_eok = 0.0
        # 0 이하면 플러스(>0) 규칙으로 폴백
        change_floor: Optional[float] = None
        if min_change_rate is not None and float(min_change_rate) > 0:
            change_floor = float(min_change_rate)
        change_ceil: Optional[float] = None
        if max_change_rate is not None and float(max_change_rate) > 0:
            change_ceil = float(max_change_rate)
        # 억원 → 백만원 (ka10030 trde_amt)
        amount_floor_m: Optional[float] = None
        if min_trade_amount_eok is not None and float(min_trade_amount_eok) > 0:
            amount_floor_m = float(min_trade_amount_eok) * 100.0
        if screener_filters:
            filt = dict(SCREENER_VOLUME_RANK_FILTERS)
            # API 거래대금구분에 20억 단계는 없음(10억=100, 30억=300).
            # 10억 이상으로 1차 축소 후 후처리로 정확한 하한 적용.
            if amount_floor_m is not None and amount_floor_m >= 1000 and trde_prica_tp is None:
                filt["trde_prica_tp"] = "100"
        else:
            filt = {
                "mang_stk_incls": "0",
                "crd_tp": "0",
                "trde_qty_tp": "0",
                "pric_tp": "0",
                "trde_prica_tp": "0",
                "mrkt_open_tp": "0",
                "stex_tp": "3",
            }
        for key, val in (
            ("mang_stk_incls", mang_stk_incls),
            ("crd_tp", crd_tp),
            ("trde_qty_tp", trde_qty_tp),
            ("pric_tp", pric_tp),
            ("trde_prica_tp", trde_prica_tp),
            ("mrkt_open_tp", mrkt_open_tp),
            ("stex_tp", stex_tp),
        ):
            if val is not None:
                filt[key] = val

        body = {"mrkt_tp": market, "sort_tp": sort_tp, **filt}
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            url = host + "/api/dostk/rkinfo"

            for auth_try in range(2):
                token = self.token_manager.get_valid_token()
                if not token:
                    return {"success": False, "error": "토큰 없음", "items": []}

                headers = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "cont-yn": "N",
                    "next-key": "",
                    "api-id": "ka10030",
                }
                kept: List[Dict] = []
                raw_count = 0
                excluded_etf_count = 0
                excluded_negative_count = 0
                excluded_overheat_count = 0
                excluded_low_amount_count = 0
                seen_codes: set[str] = set()
                cont_yn, next_key = "N", ""
                pages = 0
                # 등락·대금 필터 시 비중이 크면 페이지가 더 필요할 수 있음.
                # ka10030 약 100건/페이지 — limit만 커도 연속조회 필요.
                need_paging = (
                    screener_filters or positive_change_only
                    or change_floor is not None or change_ceil is not None
                    or amount_floor_m is not None
                    or int(limit or 0) > 100
                )
                max_pages = 12 if need_paging else 1
                retry_auth = False

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    while pages < max_pages:
                        pages += 1
                        if not await self._acquire_api_slot("ka10030", max_wait=20.0 if limit > 20 else 8.0, priority=APIPriority.HIGH):
                            logger.warning("[VOLUME_RANK] rate limit slot timeout")
                            if kept:
                                break
                            return {"success": False, "error": "API 호출 제한", "items": []}
                        req_headers = {
                            **headers,
                            "cont-yn": cont_yn,
                            "next-key": next_key,
                        }
                        async with session.post(url, headers=req_headers, json=body) as resp:
                            text = await resp.text()
                            resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                            resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""
                            if resp.status == 429:
                                api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                                logger.warning(f"[VOLUME_RANK] HTTP 429, body={text[:300]}")
                                if kept:
                                    break
                                return {"success": False, "error": "HTTP 429", "items": []}
                            if resp.status != 200:
                                logger.warning(f"[VOLUME_RANK] HTTP {resp.status}, body={text[:300]}")
                                if kept:
                                    break
                                return {"success": False, "error": f"HTTP {resp.status}", "items": []}
                            data = json.loads(text)

                        ok = (data.get("return_code") == 0) or (data.get("returnCode") == 0) or (data.get("rt_cd") == "0")
                        if not ok:
                            msg = data.get("return_msg") or data.get("returnMsg") or "조회 실패"
                            logger.warning(f"[VOLUME_RANK] 실패 msg={msg}")
                            if kept:
                                break
                            if auth_try == 0 and self._is_kiwoom_token_invalid(msg, data):
                                logger.warning("🔑 [TOKEN] VOLUME_RANK 토큰 무효 — 재인증 후 재시도")
                                if await self._reauthenticate_async():
                                    retry_auth = True
                                    break
                            return {"success": False, "error": msg, "items": []}

                        rows = data.get("tdy_trde_qty_upper") or []
                        raw_count += len(rows)
                        for r in rows:
                            it = self._parse_volume_rank_row(r)
                            code = it.get("stock_code", "")
                            if code and code in seen_codes:
                                continue
                            if code:
                                seen_codes.add(code)
                            if screener_filters:
                                if self._is_etf_family_item(it.get("stock_name", ""), it.get("product_type")):
                                    excluded_etf_count += 1
                                    continue
                                if not self._is_screener_stock(it.get("stock_name", ""), it.get("product_type")):
                                    continue
                            chg = float(it.get("change_rate") or 0)
                            if change_floor is not None:
                                if chg < change_floor:
                                    excluded_negative_count += 1
                                    continue
                            elif positive_change_only and chg <= 0:
                                excluded_negative_count += 1
                                continue
                            if change_ceil is not None and chg >= change_ceil:
                                excluded_overheat_count += 1
                                continue
                            if amount_floor_m is not None:
                                amt = float(it.get("trade_amount") or 0)
                                if amt < amount_floor_m:
                                    excluded_low_amount_count += 1
                                    continue
                            kept.append(it)
                            if len(kept) >= limit:
                                break

                        if len(kept) >= limit:
                            break
                        next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                        cont_yn = "Y" if (resp_cont or "").upper() == "Y" and next_key else "N"
                        if cont_yn != "Y":
                            break

                if retry_auth:
                    continue

                items = kept[:limit]
                logger.info(
                    f"[VOLUME_RANK] success count={len(items)} raw={raw_count} "
                    f"excluded_etf={excluded_etf_count} excluded_neg={excluded_negative_count} "
                    f"excluded_oh={excluded_overheat_count} excluded_amt={excluded_low_amount_count} "
                    f"pages={pages} market={market} sort={sort_tp} screener={screener_filters} "
                    f"pos_only={positive_change_only} min_chg={change_floor} "
                    f"max_chg={change_ceil} min_amt_eok="
                    f"{(amount_floor_m / 100.0) if amount_floor_m is not None else None} "
                    f"body={body}"
                )
                return {
                    "success": True,
                    "items": items,
                    "raw_count": raw_count,
                    "excluded_etf_count": excluded_etf_count,
                    "excluded_negative_count": excluded_negative_count,
                    "excluded_overheat_count": excluded_overheat_count,
                    "excluded_low_amount_count": excluded_low_amount_count,
                    "positive_change_only": positive_change_only,
                    "min_change_rate": change_floor,
                    "max_change_rate": change_ceil,
                    "min_trade_amount_eok": (
                        (amount_floor_m / 100.0) if amount_floor_m is not None else None
                    ),
                    "api_filters": body,
                }

            return {"success": False, "error": "토큰 재인증 후에도 조회 실패", "items": []}
        except Exception as e:
            logger.exception(f"[VOLUME_RANK] error={e}")
            return {"success": False, "error": str(e), "items": []}

    @staticmethod
    def _parse_change_rate_rank_row(r: Dict) -> Dict:
        name = r.get("stk_nm", "")
        trade_amt_raw = (
            r.get("trde_amt")
            or r.get("acc_trde_amt")
            or r.get("trde_prica")
            or r.get("now_trde_prica")
            or "0"
        )
        return {
            "stock_code": _normalize_stock_code(str(r.get("stk_cd", ""))),
            "stock_name": name,
            "current_price": abs(_parse_kiwoom_int(r.get("cur_prc", "0"))),
            "price_diff": _parse_kiwoom_int(r.get("pred_pre", "0")),
            "change_rate": _parse_kiwoom_float(r.get("flu_rt", "0")),
            "volume": abs(_parse_kiwoom_int(r.get("now_trde_qty", "0"))),
            "trade_amount": abs(_parse_kiwoom_int(trade_amt_raw)),
            "product_type": _ScreenerMixin.classify_product_type(name),
            "stock_class": (r.get("stk_cls") or "").strip() or None,
        }

    @staticmethod
    def cap_by_trade_amount(items: List[Dict], limit: int) -> List[Dict]:
        """거래대금(없으면 거래량×현재가) 내림차순으로 상위 limit개."""
        n = max(1, int(limit or 1))

        def _amt(it: Dict) -> float:
            try:
                v = it.get("trade_amount")
                if v is not None and str(v).strip() != "" and float(v) > 0:
                    return float(v)
            except (TypeError, ValueError):
                pass
            try:
                return float(it.get("volume") or 0) * float(it.get("current_price") or 0)
            except (TypeError, ValueError):
                return 0.0

        return sorted(items or [], key=_amt, reverse=True)[:n]

    @staticmethod
    def _parse_theme_group_row(r: Dict) -> Dict:
        return {
            "theme_code": str(r.get("thema_grp_cd") or "").strip(),
            "theme_name": str(r.get("thema_nm") or "").strip(),
            "stock_count": abs(_parse_kiwoom_int(r.get("stk_num", "0"))),
            "change_rate": _parse_kiwoom_float(r.get("flu_rt", "0")),
            "rising_count": abs(_parse_kiwoom_int(r.get("rising_stk_num", "0"))),
            "fall_count": abs(_parse_kiwoom_int(r.get("fall_stk_num", "0"))),
            "period_return": _parse_kiwoom_float(r.get("dt_prft_rt", "0")),
            "main_stocks": str(r.get("main_stk") or "").strip(),
            "flu_sig": str(r.get("flu_sig") or "").strip(),
        }

    @staticmethod
    def _parse_theme_stock_row(r: Dict) -> Dict:
        name = str(r.get("stk_nm") or "").strip()
        return {
            "stock_code": _normalize_stock_code(str(r.get("stk_cd", ""))),
            "stock_name": name,
            "current_price": abs(_parse_kiwoom_int(r.get("cur_prc", "0"))),
            "price_diff": _parse_kiwoom_int(r.get("pred_pre", "0")),
            "change_rate": _parse_kiwoom_float(r.get("flu_rt", "0")),
            "volume": abs(_parse_kiwoom_int(r.get("acc_trde_qty", "0"))),
            "period_return": _parse_kiwoom_float(r.get("dt_prft_rt_n", "0")),
            "product_type": _ScreenerMixin.classify_product_type(name),
        }

    async def get_theme_group_list(
        self,
        *,
        qry_tp: str = "0",
        stk_cd: str = "",
        date_tp: str = "1",
        thema_nm: str = "",
        flu_pl_amt_tp: str = "3",
        stex_tp: str = "3",
        max_pages: int = 20,
    ) -> Dict:
        """테마그룹별요청 (ka90001, /api/dostk/thme).

        qry_tp: 0전체검색 / 1테마검색 / 2종목검색
        date_tp: 기간수익률 기준 일수 (1~99)
        flu_pl_amt_tp: 1상위기간수익 2하위기간수익 3상위등락률 4하위등락률
        stex_tp: 1KRX / 2NXT / 3통합
        """
        body: Dict = {
            "qry_tp": str(qry_tp or "0"),
            "date_tp": str(date_tp or "1"),
            "flu_pl_amt_tp": str(flu_pl_amt_tp or "3"),
            "stex_tp": str(stex_tp or "3"),
        }
        code = self.normalize_stock_code(stk_cd) if stk_cd else ""
        if code:
            body["stk_cd"] = code
        if thema_nm:
            body["thema_nm"] = str(thema_nm)

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            url = host + "/api/dostk/thme"

            for auth_try in range(2):
                token = self.token_manager.get_valid_token()
                if not token:
                    if not self.authenticate():
                        return {"success": False, "error": "토큰 없음", "items": []}
                    token = self.token_manager.get_valid_token()
                if not token:
                    return {"success": False, "error": "토큰 없음", "items": []}

                headers_base = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "api-id": "ka90001",
                }
                items: List[Dict] = []
                seen: set[str] = set()
                cont_yn, next_key = "N", ""
                pages = 0
                retry_auth = False

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    while pages < max(1, int(max_pages or 1)):
                        pages += 1
                        if not await self._acquire_api_slot(
                            "ka90001", max_wait=30.0, priority=APIPriority.LOW
                        ):
                            if items:
                                break
                            return {"success": False, "error": "API 호출 제한", "items": []}

                        headers = {**headers_base, "cont-yn": cont_yn, "next-key": next_key}
                        async with session.post(url, headers=headers, json=body) as resp:
                            body_text = await resp.text()
                            resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                            resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""

                            if resp.status == 429:
                                api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                                continue
                            if resp.status != 200:
                                if self._is_kiwoom_token_invalid(body_text) and auth_try == 0:
                                    retry_auth = True
                                    break
                                return {
                                    "success": False,
                                    "error": f"HTTP {resp.status}",
                                    "items": items,
                                }

                            try:
                                data = json.loads(body_text)
                            except json.JSONDecodeError:
                                return {
                                    "success": False,
                                    "error": "JSON 파싱 실패",
                                    "items": items,
                                }

                            ok = (data.get("return_code") == 0) or (data.get("rt_cd") == "0")
                            if not ok:
                                msg = data.get("return_msg") or data.get("msg1") or "ka90001 실패"
                                if self._is_kiwoom_token_invalid(msg, data) and auth_try == 0:
                                    retry_auth = True
                                    break
                                if any(
                                    k in str(msg).lower()
                                    for k in ["rate limit", "too many", "요청 한도", "429", "제한", "초과"]
                                ):
                                    api_rate_limiter.handle_api_error(Exception(msg))
                                    continue
                                return {"success": False, "error": msg, "items": items, "raw": data}

                            for raw in data.get("thema_grp") or []:
                                parsed = self._parse_theme_group_row(raw if isinstance(raw, dict) else {})
                                tc = parsed.get("theme_code") or ""
                                if not tc or tc in seen:
                                    continue
                                seen.add(tc)
                                items.append(parsed)

                            cont_yn = (resp_cont or data.get("cont_yn") or data.get("cont-yn") or "N").upper()
                            next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                            if cont_yn != "Y" or not next_key:
                                break

                if retry_auth:
                    await self._reauthenticate_async()
                    continue

                logger.info("[THEME_GROUP] pages=%s items=%s", pages, len(items))
                return {"success": True, "items": items, "pages": pages}

            return {"success": False, "error": "토큰 재인증 실패", "items": []}
        except Exception as e:
            logger.exception(f"[THEME_GROUP] error={e}")
            return {"success": False, "error": str(e), "items": []}

    async def get_theme_component_stocks(
        self,
        thema_grp_cd: str,
        *,
        date_tp: str = "1",
        stex_tp: str = "3",
        max_pages: int = 10,
    ) -> Dict:
        """테마구성종목요청 (ka90002, /api/dostk/thme)."""
        theme_code = str(thema_grp_cd or "").strip()
        if not theme_code:
            return {"success": False, "error": "thema_grp_cd 필요", "items": []}

        body = {
            "thema_grp_cd": theme_code,
            "date_tp": str(date_tp or "1"),
            "stex_tp": str(stex_tp or "3"),
        }

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            url = host + "/api/dostk/thme"

            for auth_try in range(2):
                token = self.token_manager.get_valid_token()
                if not token:
                    if not self.authenticate():
                        return {"success": False, "error": "토큰 없음", "items": []}
                    token = self.token_manager.get_valid_token()
                if not token:
                    return {"success": False, "error": "토큰 없음", "items": []}

                headers_base = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "api-id": "ka90002",
                }
                items: List[Dict] = []
                seen: set[str] = set()
                cont_yn, next_key = "N", ""
                pages = 0
                theme_flu_rt = None
                theme_period_return = None
                retry_auth = False

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    while pages < max(1, int(max_pages or 1)):
                        pages += 1
                        if not await self._acquire_api_slot(
                            "ka90002", max_wait=30.0, priority=APIPriority.LOW
                        ):
                            if items:
                                break
                            return {"success": False, "error": "API 호출 제한", "items": []}

                        headers = {**headers_base, "cont-yn": cont_yn, "next-key": next_key}
                        async with session.post(url, headers=headers, json=body) as resp:
                            body_text = await resp.text()
                            resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                            resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""

                            if resp.status == 429:
                                api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                                continue
                            if resp.status != 200:
                                if self._is_kiwoom_token_invalid(body_text) and auth_try == 0:
                                    retry_auth = True
                                    break
                                return {
                                    "success": False,
                                    "error": f"HTTP {resp.status}",
                                    "items": items,
                                }

                            try:
                                data = json.loads(body_text)
                            except json.JSONDecodeError:
                                return {
                                    "success": False,
                                    "error": "JSON 파싱 실패",
                                    "items": items,
                                }

                            ok = (data.get("return_code") == 0) or (data.get("rt_cd") == "0")
                            if not ok:
                                msg = data.get("return_msg") or data.get("msg1") or "ka90002 실패"
                                if self._is_kiwoom_token_invalid(msg, data) and auth_try == 0:
                                    retry_auth = True
                                    break
                                if any(
                                    k in str(msg).lower()
                                    for k in ["rate limit", "too many", "요청 한도", "429", "제한", "초과"]
                                ):
                                    api_rate_limiter.handle_api_error(Exception(msg))
                                    continue
                                return {"success": False, "error": msg, "items": items, "raw": data}

                            if theme_flu_rt is None and data.get("flu_rt") is not None:
                                theme_flu_rt = _parse_kiwoom_float(data.get("flu_rt", "0"))
                            if theme_period_return is None and data.get("dt_prft_rt") is not None:
                                theme_period_return = _parse_kiwoom_float(data.get("dt_prft_rt", "0"))

                            for raw in data.get("thema_comp_stk") or []:
                                parsed = self._parse_theme_stock_row(raw if isinstance(raw, dict) else {})
                                sc = parsed.get("stock_code") or ""
                                if not sc or len(sc) != 6 or sc in seen:
                                    continue
                                seen.add(sc)
                                items.append(parsed)

                            cont_yn = (resp_cont or data.get("cont_yn") or data.get("cont-yn") or "N").upper()
                            next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                            if cont_yn != "Y" or not next_key:
                                break

                if retry_auth:
                    await self._reauthenticate_async()
                    continue

                return {
                    "success": True,
                    "items": items,
                    "theme_code": theme_code,
                    "change_rate": theme_flu_rt,
                    "period_return": theme_period_return,
                    "pages": pages,
                }

            return {"success": False, "error": "토큰 재인증 실패", "items": []}
        except Exception as e:
            logger.exception(f"[THEME_STOCKS] theme={theme_code} error={e}")
            return {"success": False, "error": str(e), "items": []}

    async def get_change_rate_rank(
        self,
        market: str = "000",
        limit: int = 100,
        *,
        sangtta_filters: bool = True,
        min_change_rate: Optional[float] = None,
        exclude_etf: bool = True,
        sort_tp: Optional[str] = None,
        trde_qty_cnd: Optional[str] = None,
        stk_cnd: Optional[str] = None,
        crd_cnd: Optional[str] = None,
        updown_incls: Optional[str] = None,
        pric_cnd: Optional[str] = None,
        trde_prica_cnd: Optional[str] = None,
        stex_tp: Optional[str] = None,
    ) -> Dict:
        """전일대비등락률상위 조회 (ka10027, /api/dostk/rkinfo).

        market: 000 전체 / 001 코스피 / 101 코스닥
        sangtta_filters: True면 상따 기본 필터(관리제외·천원↑·대금10억↑·KRX) 적용
        min_change_rate: 등락률 하한(%). sangtta_filters면 기본 13.0
        exclude_etf: True면 ETF/ETN/파생·스팩·우선주 후처리 제외
        """
        if min_change_rate is None and sangtta_filters:
            min_change_rate = float(SANGTTA_UNIVERSE_MIN_CHANGE_RATE)
        change_floor: Optional[float] = None
        if min_change_rate is not None and float(min_change_rate) > 0:
            change_floor = float(min_change_rate)

        if sangtta_filters:
            filt = dict(SANGTTA_CHANGE_RATE_RANK_FILTERS)
        else:
            filt = {
                "sort_tp": "1",
                "trde_qty_cnd": "0",
                "stk_cnd": "0",
                "crd_cnd": "0",
                "updown_incls": "1",
                "pric_cnd": "0",
                "trde_prica_cnd": "0",
                "stex_tp": "1",
            }
        for key, val in (
            ("sort_tp", sort_tp),
            ("trde_qty_cnd", trde_qty_cnd),
            ("stk_cnd", stk_cnd),
            ("crd_cnd", crd_cnd),
            ("updown_incls", updown_incls),
            ("pric_cnd", pric_cnd),
            ("trde_prica_cnd", trde_prica_cnd),
            ("stex_tp", stex_tp),
        ):
            if val is not None:
                filt[key] = val

        body = {"mrkt_tp": market, **filt}
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            use_mock = Config.KIWOOM_USE_MOCK_ACCOUNT
            host = Config.KIWOOM_MOCK_API_URL if use_mock else Config.KIWOOM_REAL_API_URL
            url = host + "/api/dostk/rkinfo"

            for auth_try in range(2):
                token = self.token_manager.get_valid_token()
                if not token:
                    return {"success": False, "error": "토큰 없음", "items": []}

                headers = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "cont-yn": "N",
                    "next-key": "",
                    "api-id": "ka10027",
                }
                kept: List[Dict] = []
                raw_count = 0
                excluded_etf_count = 0
                excluded_low_chg_count = 0
                seen_codes: set[str] = set()
                cont_yn, next_key = "N", ""
                pages = 0
                max_pages = 8 if (exclude_etf or change_floor is not None) else 1
                retry_auth = False
                # 상승률 정렬이면 하한 미만이 연속으로 나오면 이후도 더 낮음 → 조기 종료
                hit_below_floor = False

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    while pages < max_pages and not hit_below_floor:
                        pages += 1
                        if not await self._acquire_api_slot(
                            "ka10027",
                            max_wait=20.0 if limit > 20 else 8.0,
                            priority=APIPriority.HIGH,
                        ):
                            logger.warning("[CHANGE_RATE_RANK] rate limit slot timeout")
                            if kept:
                                break
                            return {"success": False, "error": "API 호출 제한", "items": []}
                        req_headers = {
                            **headers,
                            "cont-yn": cont_yn,
                            "next-key": next_key,
                        }
                        async with session.post(url, headers=req_headers, json=body) as resp:
                            text = await resp.text()
                            resp_cont = resp.headers.get("cont-yn") or resp.headers.get("Cont-Yn") or "N"
                            resp_next = resp.headers.get("next-key") or resp.headers.get("Next-Key") or ""
                            if resp.status == 429:
                                api_rate_limiter.handle_api_error(Exception("429 Too Many Requests"))
                                logger.warning(f"[CHANGE_RATE_RANK] HTTP 429, body={text[:300]}")
                                if kept:
                                    break
                                return {"success": False, "error": "HTTP 429", "items": []}
                            if resp.status != 200:
                                logger.warning(f"[CHANGE_RATE_RANK] HTTP {resp.status}, body={text[:300]}")
                                if kept:
                                    break
                                return {"success": False, "error": f"HTTP {resp.status}", "items": []}
                            data = json.loads(text)

                        ok = (data.get("return_code") == 0) or (data.get("returnCode") == 0) or (data.get("rt_cd") == "0")
                        if not ok:
                            msg = data.get("return_msg") or data.get("returnMsg") or "조회 실패"
                            logger.warning(f"[CHANGE_RATE_RANK] 실패 msg={msg}")
                            if kept:
                                break
                            if auth_try == 0 and self._is_kiwoom_token_invalid(msg, data):
                                logger.warning("🔑 [TOKEN] CHANGE_RATE_RANK 토큰 무효 — 재인증 후 재시도")
                                if await self._reauthenticate_async():
                                    retry_auth = True
                                    break
                            return {"success": False, "error": msg, "items": []}

                        rows = data.get("pred_pre_flu_rt_upper") or []
                        raw_count += len(rows)
                        for r in rows:
                            it = self._parse_change_rate_rank_row(r)
                            code = it.get("stock_code", "")
                            if code and code in seen_codes:
                                continue
                            if code:
                                seen_codes.add(code)
                            chg = float(it.get("change_rate") or 0)
                            if change_floor is not None and chg < change_floor:
                                excluded_low_chg_count += 1
                                # sort_tp=1(상승률)이면 이후 행도 하한 미만
                                if str(filt.get("sort_tp") or "1") == "1":
                                    hit_below_floor = True
                                    break
                                continue
                            if exclude_etf:
                                if self._is_etf_family_item(it.get("stock_name", ""), it.get("product_type")):
                                    excluded_etf_count += 1
                                    continue
                                if not self._is_screener_stock(it.get("stock_name", ""), it.get("product_type")):
                                    continue
                            kept.append(it)
                            if len(kept) >= limit:
                                break

                        if len(kept) >= limit or hit_below_floor:
                            break
                        next_key = data.get("next_key") or data.get("next-key") or resp_next or ""
                        cont_yn = "Y" if (resp_cont or "").upper() == "Y" and next_key else "N"
                        if cont_yn != "Y":
                            break

                if retry_auth:
                    continue

                items = kept[:limit]
                logger.info(
                    f"[CHANGE_RATE_RANK] success count={len(items)} raw={raw_count} "
                    f"excluded_etf={excluded_etf_count} excluded_low_chg={excluded_low_chg_count} "
                    f"pages={pages} market={market} min_chg={change_floor} "
                    f"sangtta={sangtta_filters} body={body}"
                )
                return {
                    "success": True,
                    "items": items,
                    "raw_count": raw_count,
                    "excluded_etf_count": excluded_etf_count,
                    "excluded_low_chg_count": excluded_low_chg_count,
                    "min_change_rate": change_floor,
                    "api_filters": body,
                }

            return {"success": False, "error": "토큰 재인증 후에도 조회 실패", "items": []}
        except Exception as e:
            logger.exception(f"[CHANGE_RATE_RANK] error={e}")
            return {"success": False, "error": str(e), "items": []}
