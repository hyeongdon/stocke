from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional

from api.kiwoom_api_shared import _parse_kiwoom_int, logger
from utils.datetime_kst import now_kst

__all__ = ["_StockAnalyticsMixin"]


class _StockAnalyticsMixin:
    @staticmethod
    def _pick_int(row: Dict, keys: List[str], default: int = 0) -> int:
        for key in keys:
            if key in row and row.get(key) not in (None, ""):
                try:
                    return abs(int(str(row.get(key)).replace(",", "").replace("+", "")))
                except Exception:
                    continue
        return default

    @staticmethod
    def _pick_str(row: Dict, keys: List[str], default: str = "") -> str:
        for key in keys:
            if key in row and row.get(key) not in (None, ""):
                return str(row.get(key))
        return default

    @staticmethod
    def _ka10004_level_keys(level: int) -> Dict[str, List[str]]:
        """ka10004(REST) 호가 레벨별 필드명. 1호가는 *_fpr_*, 2~10은 *_{n}th_pre_*."""
        if level == 1:
            return {
                "ask_price": ["sel_fpr_bid", "askp1", "offerho1", "sel_prc1"],
                "ask_qty": ["sel_fpr_req", "askp_rsqn1", "offerrem1", "sel_qty1"],
                "bid_price": ["buy_fpr_bid", "bidp1", "bidho1", "buy_prc1"],
                "bid_qty": ["buy_fpr_req", "bidp_rsqn1", "bidrem1", "buy_qty1"],
            }
        return {
            "ask_price": [
                f"sel_{level}th_pre_bid",
                f"askp{level}",
                f"offerho{level}",
                f"sel_prc{level}",
            ],
            "ask_qty": [
                f"sel_{level}th_pre_req",
                f"askp_rsqn{level}",
                f"offerrem{level}",
                f"sel_qty{level}",
            ],
            "bid_price": [
                f"buy_{level}th_pre_bid",
                f"bidp{level}",
                f"bidho{level}",
                f"buy_prc{level}",
            ],
            "bid_qty": [
                f"buy_{level}th_pre_req",
                f"bidp_rsqn{level}",
                f"bidrem{level}",
                f"buy_qty{level}",
            ],
        }

    @classmethod
    def _extract_quote_row(cls, quote_data: Dict) -> Dict:
        """ka10004 응답에서 호가 행 dict 추출 (flat 본문 또는 stk_hoga[0])."""
        if not isinstance(quote_data, dict) or not quote_data:
            return {}
        # REST flat 응답: sel_fpr_bid / buy_fpr_bid 가 본문에 직접 있음
        if any(
            k in quote_data
            for k in ("sel_fpr_bid", "buy_fpr_bid", "askp1", "bidp1", "bid_req_base_tm")
        ):
            return quote_data
        rows = (
            quote_data.get("stk_hoga")
            or quote_data.get("output")
            or quote_data.get("data")
            or []
        )
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
        if isinstance(rows, dict):
            return rows
        return {}

    @classmethod
    def _extract_basic_row(cls, basic_data: Dict) -> Dict:
        """ka10006 응답에서 시세 행 dict 추출. flat REST(close_pric/flu_rt) / 배열 래핑 모두 지원."""
        if not isinstance(basic_data, dict) or not basic_data:
            return {}
        rows = (
            basic_data.get("stk_mkprc")
            or basic_data.get("output")
            or basic_data.get("data")
            or []
        )
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
        if isinstance(rows, dict) and rows:
            return rows
        # REST flat: cur_prc 또는 close_pric/flu_rt 가 본문에 직접 있음
        if any(
            basic_data.get(k) not in (None, "")
            for k in ("cur_prc", "close_pric", "flu_rt", "trde_qty", "price")
        ):
            return basic_data
        return {}

    @classmethod
    def parse_ka10006_basic(cls, basic_data: Dict) -> Dict:
        """ka10006 응답 → {stock_name, current_price, price_diff, change_rate, volume, raw_basic}."""
        basic_row = cls._extract_basic_row(basic_data or {})
        change_raw = cls._pick_str(basic_row, ["flu_rt", "change_rate", "12"], "0")
        try:
            change_rate = f"{float(str(change_raw).replace(',', '').replace('+', '').strip() or '0'):.2f}"
        except (TypeError, ValueError):
            change_rate = "0"
        return {
            "stock_name": cls._pick_str(basic_row, ["stk_nm", "stock_name", "name", "302"], ""),
            "current_price": cls._pick_int(
                basic_row, ["cur_prc", "close_pric", "price", "10"]
            ),
            "price_diff": cls._pick_int(
                basic_row, ["pred_pre", "pre", "diff", "11"]
            ),
            "change_rate": change_rate,
            "volume": cls._pick_int(basic_row, ["trde_qty", "volume", "13"]),
            "raw_basic": basic_row,
        }

    @classmethod
    def parse_ka10004_orderbook(cls, quote_data: Dict) -> List[Dict]:
        """ka10004 응답 → [{level, ask_price, ask_qty, bid_price, bid_qty}, ...]."""
        quote_row = cls._extract_quote_row(quote_data or {})
        if not quote_row:
            return []
        out: List[Dict] = []
        for level in range(1, 11):
            keys = cls._ka10004_level_keys(level)
            ask_px = cls._pick_int(quote_row, keys["ask_price"])
            bid_px = cls._pick_int(quote_row, keys["bid_price"])
            ask_qty = cls._pick_int(quote_row, keys["ask_qty"])
            bid_qty = cls._pick_int(quote_row, keys["bid_qty"])
            if ask_px == 0 and bid_px == 0 and ask_qty == 0 and bid_qty == 0:
                continue
            out.append(
                {
                    "level": level,
                    "ask_price": ask_px,
                    "ask_qty": ask_qty,
                    "bid_price": bid_px,
                    "bid_qty": bid_qty,
                }
            )
        return out

    async def get_stock_snapshot(self, stock_code: str) -> Dict:
        """현재가(ka10006) + 10호가(ka10004) 스냅샷 조회."""
        code = self.normalize_stock_code(stock_code)
        if not code:
            return {"success": False, "error": "종목코드가 비어 있습니다."}

        logger.info(f"[SNAPSHOT_START] stock_code={code}")

        basic_resp = await self._request_stockinfo_tr("ka10006", {"stk_cd": code})
        quote_resp = await self._request_stockinfo_tr("ka10004", {"stk_cd": code})
        # ka10006/ka10004가 환경에 따라 실패할 수 있어,
        # 이미 검증된 현재가 조회 로직(ka10081)을 fallback으로 사용한다.
        fallback_price = None
        fallback_volume = 0
        fallback_prev_close = 0
        if not basic_resp.get("success"):
            fallback_price = await self.get_current_price(code)
            if fallback_price is None:
                logger.error(f"[SNAPSHOT_FAIL] stock_code={code}, reason=ka10006_failed_and_no_fallback, error={basic_resp.get('error')}")
                return {"success": False, "error": f"ka10006 실패: {basic_resp.get('error', 'unknown error')}"}
            # ka10081 일봉 데이터로 거래량/전일종가 보정 시도
            try:
                chart_data = await self.get_stock_chart_data(code, "1D")
                if chart_data:
                    last_row = chart_data[-1]
                    fallback_volume = int(last_row.get("volume", 0) or 0)
                    if len(chart_data) >= 2:
                        fallback_prev_close = int(chart_data[-2].get("close", 0) or 0)
            except Exception:
                pass
            logger.warning(f"get_stock_snapshot: ka10006 실패, fallback 현재가 사용 - {code}")
            basic_resp = {"success": True, "data": {"stk_mkprc": [{"cur_prc": fallback_price}]}}

        basic_data = basic_resp.get("data", {})
        quote_data = quote_resp.get("data", {}) if quote_resp.get("success") else {}

        parsed_basic = self.parse_ka10006_basic(basic_data if isinstance(basic_data, dict) else {})
        basic_row = parsed_basic.get("raw_basic") or {}

        quote_row = self._extract_quote_row(quote_data)
        logger.info(
            f"[SNAPSHOT_RAW] stock_code={code}, basic_endpoint={basic_resp.get('endpoint','')}, "
            f"quote_endpoint={quote_resp.get('endpoint','')}, "
            f"basic_keys={list(basic_data.keys())[:20] if isinstance(basic_data, dict) else []}, "
            f"quote_keys={list(quote_data.keys())[:20]}"
        )
        logger.info(
            f"[SNAPSHOT_ROW_KEYS] stock_code={code}, "
            f"basic_row_keys={list(basic_row.keys())[:40] if isinstance(basic_row, dict) else []}, "
            f"quote_row_keys={list(quote_row.keys())[:40] if isinstance(quote_row, dict) else []}"
        )

        orderbook = self.parse_ka10004_orderbook(quote_data)
        orderbook_live = bool(orderbook) and bool(quote_resp.get("success"))

        snapshot = {
            "stock_code": code,
            "stock_name": parsed_basic.get("stock_name") or "",
            "current_price": int(parsed_basic.get("current_price") or 0),
            "price_diff": int(parsed_basic.get("price_diff") or 0),
            "change_rate": str(parsed_basic.get("change_rate") or "0"),
            "volume": int(parsed_basic.get("volume") or 0),
            "orderbook_time": self._pick_str(
                quote_row,
                ["bid_req_base_tm", "hotime", "hoga_time", "time"],
                now_kst().strftime("%H:%M:%S"),
            ),
            "orderbook": orderbook,
            "orderbook_live": orderbook_live,
            "raw_basic": basic_row,
            "raw_quote": quote_row,
        }

        # fallback 가격이 있으면 current_price를 보정
        if fallback_price is not None and snapshot["current_price"] == 0:
            snapshot["current_price"] = fallback_price
        if snapshot["volume"] == 0 and fallback_volume > 0:
            snapshot["volume"] = fallback_volume
        if snapshot["price_diff"] == 0 and fallback_prev_close > 0 and snapshot["current_price"] > 0:
            snapshot["price_diff"] = snapshot["current_price"] - fallback_prev_close
        if (snapshot["change_rate"] in ("", "0", "0.0", "0.00")) and fallback_prev_close > 0:
            try:
                diff = snapshot["current_price"] - fallback_prev_close
                snapshot["change_rate"] = f"{(diff / fallback_prev_close) * 100:.2f}"
            except Exception:
                pass

        # 호가 TR 실패 시, 화면 표시용 가짜 호가(잔량 0). 돼지 판정에는 쓰지 않음.
        if not snapshot["orderbook"] and snapshot["current_price"] > 0:
            tick = self._calc_tick_size(snapshot["current_price"])
            base = snapshot["current_price"]
            for i in range(1, 4):
                snapshot["orderbook"].append(
                    {
                        "level": i,
                        "ask_price": base + (tick * i),
                        "ask_qty": 0,
                        "bid_price": max(0, base - (tick * i)),
                        "bid_qty": 0,
                    }
                )
            snapshot["orderbook_live"] = False
            logger.warning(f"[SNAPSHOT_ORDERBOOK_FALLBACK] stock_code={code}, reason=empty_orderbook_from_tr")

        warnings = []
        if fallback_price is not None:
            warnings.append("ka10006 unavailable; current_price from fallback")
        if not quote_resp.get("success"):
            warnings.append(f"ka10004 unavailable: {quote_resp.get('error', 'unknown error')}")
        elif not orderbook_live:
            warnings.append("ka10004 returned empty orderbook")
        if warnings:
            snapshot["warnings"] = warnings

        logger.info(
            f"[SNAPSHOT_DONE] stock_code={code}, stock_name={snapshot.get('stock_name','')}, "
            f"current_price={snapshot.get('current_price',0)}, price_diff={snapshot.get('price_diff',0)}, "
            f"change_rate={snapshot.get('change_rate','0')}, volume={snapshot.get('volume',0)}, "
            f"orderbook_rows={len(snapshot.get('orderbook', []))}, "
            f"orderbook_live={snapshot.get('orderbook_live')}, warnings={snapshot.get('warnings', [])}"
        )
        return {"success": True, "snapshot": snapshot}

    async def get_stock_institution_foreign_net(self, stock_code: str) -> Dict:
        """종목별 기관·외인 일별 순매매 (ka10009, 필요 시 ka10045 보정).

        Returns:
            success, foreign_net, institution_net, date, source, raw
            순매매 수량은 양수=순매수, 음수=순매도.
        """
        code = self.normalize_stock_code(stock_code)
        if not code:
            return {"success": False, "error": "종목코드가 비어 있습니다."}

        def _to_int(v) -> int:
            try:
                s = str(v or "").replace(",", "").replace("+", "").strip()
                if not s or s in ("-", "--"):
                    return 0
                return int(float(s))
            except (TypeError, ValueError):
                return 0

        # 1) ka10009 — 주식기관요청 (종목 1건, 기관·외인 순매매)
        resp = await self._request_stockinfo_tr("ka10009", {"stk_cd": code})
        if resp.get("success"):
            data = resp.get("data") or {}
            rows = data.get("stk_orgn") or data.get("output") or data.get("data")
            row = None
            if isinstance(rows, list) and rows:
                row = rows[0] if isinstance(rows[0], dict) else None
            elif isinstance(data, dict):
                # 단일 객체 응답
                if data.get("orgn_daly_nettrde") is not None or data.get("frgnr_daly_nettrde") is not None:
                    row = data
            if row:
                foreign = _to_int(
                    row.get("frgnr_daly_nettrde")
                    or row.get("frgnr_daly_nettrde_qty")
                    or row.get("for_daly_nettrde_qty")
                )
                institution = _to_int(
                    row.get("orgn_daly_nettrde")
                    or row.get("orgn_daly_nettrde_qty")
                    or row.get("orgn_daly_nettrde")
                )
                # 빈 문자열만 온 경우(모의/장마감 전)는 fallback
                has_any = any(
                    str(row.get(k) or "").strip() not in ("",)
                    for k in (
                        "orgn_daly_nettrde",
                        "frgnr_daly_nettrde",
                        "orgn_daly_nettrde_qty",
                        "frgnr_daly_nettrde_qty",
                        "for_daly_nettrde_qty",
                    )
                )
                if has_any:
                    return {
                        "success": True,
                        "stock_code": code,
                        "foreign_net": foreign,
                        "institution_net": institution,
                        "date": self._pick_str(row, ["date", "dt"], ""),
                        "source": "ka10009",
                        "raw": row,
                    }

        # 2) ka10045 — 종목별기관매매추이 (당일 구간)
        from utils.datetime_kst import kst_today

        today = kst_today().strftime("%Y%m%d")
        resp45 = await self._request_stockinfo_tr(
            "ka10045",
            {
                "stk_cd": code,
                "strt_dt": today,
                "end_dt": today,
                "orgn_prsm_unp_tp": "1",
                "for_prsm_unp_tp": "1",
            },
        )
        if resp45.get("success"):
            data = resp45.get("data") or {}
            rows = (
                data.get("stk_orgn_trde_trnsn")
                or data.get("output")
                or data.get("data")
                or []
            )
            if isinstance(rows, dict):
                rows = [rows]
            if isinstance(rows, list) and rows:
                # 최신 일자 우선
                def _dt_key(r):
                    return str((r or {}).get("dt") or "")

                row = sorted(
                    [r for r in rows if isinstance(r, dict)],
                    key=_dt_key,
                    reverse=True,
                )[0]
                foreign = _to_int(
                    row.get("for_daly_nettrde_qty")
                    or row.get("frgnr_daly_nettrde")
                    or row.get("for_dt_acc")
                )
                institution = _to_int(
                    row.get("orgn_daly_nettrde_qty")
                    or row.get("orgn_daly_nettrde")
                    or row.get("orgn_dt_acc")
                )
                return {
                    "success": True,
                    "stock_code": code,
                    "foreign_net": foreign,
                    "institution_net": institution,
                    "date": self._pick_str(row, ["dt", "date"], today),
                    "source": "ka10045",
                    "raw": row,
                }

        err = (resp45.get("error") if not resp45.get("success") else None) or (
            resp.get("error") if not resp.get("success") else "기관/외인 순매매 없음"
        )
        return {"success": False, "error": err, "stock_code": code}

    async def get_stock_institution_foreign_history(
        self, stock_code: str, days: int = 20
    ) -> Dict:
        """종목별 최근 거래일 기관·외국인 순매매 수량."""
        code = self.normalize_stock_code(stock_code)
        if not code:
            return {"success": False, "error": "종목코드가 비어 있습니다."}

        from utils.datetime_kst import kst_today

        end_date = kst_today()
        start_date = end_date - timedelta(days=40)
        response = await self._request_stockinfo_tr(
            "ka10045",
            {
                "stk_cd": code,
                "strt_dt": start_date.strftime("%Y%m%d"),
                "end_dt": end_date.strftime("%Y%m%d"),
                "orgn_prsm_unp_tp": "1",
                "for_prsm_unp_tp": "1",
            },
        )
        if not response.get("success"):
            return {
                "success": False,
                "error": response.get("error") or "종목별 수급 조회에 실패했습니다.",
                "stock_code": code,
            }

        data = response.get("data") or {}
        rows = (
            data.get("stk_orgn_trde_trnsn")
            or data.get("output")
            or data.get("data")
            or []
        )
        if isinstance(rows, dict):
            rows = [rows]

        def to_int(value: object) -> Optional[int]:
            text = str(value if value is not None else "").replace(",", "").replace("+", "").strip()
            if not text or text in ("-", "--"):
                return None
            try:
                return int(float(text))
            except (TypeError, ValueError):
                return None

        def first_value(row: Dict, keys: List[str]):
            return next(
                (row[key] for key in keys if row.get(key) not in (None, "")),
                None,
            )

        history = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            date = self._pick_str(row, ["dt", "date"], "")
            foreign = to_int(
                first_value(
                    row,
                    ["for_daly_nettrde_qty", "frgnr_daly_nettrde", "for_dt_acc"],
                )
            )
            institution = to_int(
                first_value(
                    row,
                    ["orgn_daly_nettrde_qty", "orgn_daly_nettrde", "orgn_dt_acc"],
                )
            )
            if date and (foreign is not None or institution is not None):
                history.append(
                    {
                        "date": date,
                        "foreign_net": foreign,
                        "institution_net": institution,
                    }
                )

        history.sort(key=lambda row: row["date"])
        if not history:
            return {
                "success": False,
                "error": "조회 가능한 일별 수급 내역이 없습니다.",
                "stock_code": code,
            }

        return {
            "success": True,
            "stock_code": code,
            "unit": "주",
            "source": "ka10045",
            "rows": history[-max(1, days):],
        }

    async def get_stock_program_net(self, stock_code: str) -> Dict:
        """종목별 프로그램 순매수 (장중 가능).

        Primary: ka90013 종목일별프로그램매매추이 (`/api/dostk/mrkcond`)
        Fallback: ka90008 종목시간별 최신 봉

        Returns:
            success, net_qty, buy_qty, sell_qty, net_amt, date, source, raw
            양수=프로그램 순매수.
        """
        code = self.normalize_stock_code(stock_code)
        if not code:
            return {"success": False, "error": "종목코드가 비어 있습니다."}

        def _to_int(v) -> int:
            try:
                s = str(v or "").replace(",", "").replace("+", "").strip()
                if not s or s in ("-", "--"):
                    return 0
                return int(float(s))
            except (TypeError, ValueError):
                return 0

        from utils.datetime_kst import kst_today

        today = kst_today().strftime("%Y%m%d")

        # 1) ka90013 — 일별(당일 누적 포함, 장중에도 값 있음)
        resp = await self._request_stockinfo_tr(
            "ka90013",
            {"amt_qty_tp": "2", "stk_cd": code},
        )
        if resp.get("success"):
            data = resp.get("data") or {}
            rows = (
                data.get("stk_daly_prm_trde_trnsn")
                or data.get("output")
                or data.get("data")
                or []
            )
            if isinstance(rows, dict):
                rows = [rows]
            if isinstance(rows, list) and rows:
                dict_rows = [r for r in rows if isinstance(r, dict)]

                def _dt_key(r):
                    return str(r.get("dt") or r.get("date") or "")

                today_rows = [r for r in dict_rows if _dt_key(r) == today]
                row = (today_rows or sorted(dict_rows, key=_dt_key, reverse=True) or [None])[0]
                if row:
                    net_qty = _to_int(
                        row.get("prm_netprps_qty") or row.get("prm_netprps_amt")
                    )
                    return {
                        "success": True,
                        "stock_code": code,
                        "net_qty": net_qty,
                        "buy_qty": _to_int(row.get("prm_buy_qty")),
                        "sell_qty": _to_int(row.get("prm_sell_qty")),
                        "net_amt": _to_int(row.get("prm_netprps_amt")),
                        "buy_amt": _to_int(row.get("prm_buy_amt")),
                        "sell_amt": _to_int(row.get("prm_sell_amt")),
                        "date": _dt_key(row) or today,
                        "source": "ka90013",
                        "raw": row,
                    }

        # 2) ka90008 — 시간별 최신 구간
        series = await self.get_stock_program_time_series(code, date=today)
        rows = series.get("rows") or []
        if series.get("success") and rows:
            row = rows[-1]
            return {
                "success": True,
                "stock_code": code,
                "net_qty": int(row.get("net_qty") or 0),
                "buy_qty": int(row.get("buy_qty") or 0),
                "sell_qty": int(row.get("sell_qty") or 0),
                "net_amt": int(row.get("net_amt") or 0),
                "buy_amt": int(row.get("buy_amt") or 0),
                "sell_amt": int(row.get("sell_amt") or 0),
                "date": today,
                "time": str(row.get("tm") or ""),
                "source": "ka90008",
                "raw": row.get("raw") or row,
            }

        err = series.get("error") or (
            resp.get("error") if not resp.get("success") else "프로그램 매매 없음"
        )
        return {"success": False, "error": err, "stock_code": code}

    async def get_stock_program_time_series(
        self, stock_code: str, date: Optional[str] = None
    ) -> Dict:
        """종목시간별 프로그램매매 (ka90008). 행은 시간 오름차순.

        한 칸은 보통 1분. 형성 중 현재 분은 호출측에서 제외한다.
        """
        from utils.datetime_kst import kst_today
        from utils.program_net_continuation import program_qty_int

        code = self.normalize_stock_code(stock_code)
        if not code:
            return {"success": False, "error": "종목코드가 비어 있습니다.", "rows": []}
        day = (date or kst_today().strftime("%Y%m%d")).replace("-", "")[:8]
        resp = await self._request_stockinfo_tr(
            "ka90008",
            {"amt_qty_tp": "2", "stk_cd": code, "date": day},
        )
        if not resp.get("success"):
            return {
                "success": False,
                "error": resp.get("error") or "프로그램 시간대 조회 실패",
                "stock_code": code,
                "rows": [],
            }
        data = resp.get("data") or {}
        raw_rows = (
            data.get("stk_tm_prm_trde_trnsn")
            or data.get("output")
            or data.get("data")
            or []
        )
        if isinstance(raw_rows, dict):
            raw_rows = [raw_rows]
        parsed = []
        for row in raw_rows or []:
            if not isinstance(row, dict):
                continue
            parsed.append({
                "tm": str(row.get("tm") or row.get("time") or ""),
                "net_qty": program_qty_int(
                    row.get("prm_netprps_qty")
                    if row.get("prm_netprps_qty") not in (None, "")
                    else row.get("prm_netprps_amt")
                ),
                "buy_qty": program_qty_int(row.get("prm_buy_qty")),
                "sell_qty": program_qty_int(row.get("prm_sell_qty")),
                "net_amt": program_qty_int(row.get("prm_netprps_amt")),
                "buy_amt": program_qty_int(row.get("prm_buy_amt")),
                "sell_amt": program_qty_int(row.get("prm_sell_amt")),
                "raw": row,
            })
        parsed.sort(key=lambda r: str(r.get("tm") or ""))
        return {
            "success": True,
            "stock_code": code,
            "date": day,
            "source": "ka90008",
            "rows": parsed,
        }

    @staticmethod
    def _calc_tick_size(price: int) -> int:
        """국내주식 호가 단위(대략) 계산."""
        p = abs(int(price or 0))
        if p < 2000:
            return 1
        if p < 5000:
            return 5
        if p < 20000:
            return 10
        if p < 50000:
            return 50
        if p < 200000:
            return 100
        if p < 500000:
            return 500
        return 1000

    def _parse_kiwoom_chart_data(
        self,
        api_response: dict,
        stock_code: str,
        *,
        trade_yyyymmdd: Optional[str] = None,
        verbose: bool = True,
    ) -> list:
        """키움 API 응답을 차트 데이터로 변환.

        trade_yyyymmdd: ka10080 date(기준일자)와 맞춰 해당 일자 봉만 파싱 (검증 차트용).
        """
        chart_data = []
        
        try:
            # 분봉 데이터 (ka10080)
            minute_chart_list = api_response.get('stk_min_pole_chart_qry', [])
            # 일봉(ka10081) · 주봉(ka10082) · 월봉(ka10083) 데이터 — 행 형식 동일
            daily_chart_list = (
                api_response.get('stk_dt_pole_chart_qry')
                or api_response.get('stk_stk_pole_chart_qry')
                or api_response.get('stk_mth_pole_chart_qry')
                or []
            )
            if not minute_chart_list and not daily_chart_list:
                daily_chart_list = next(
                    (
                        v for k, v in api_response.items()
                        if str(k).endswith('_pole_chart_qry') and isinstance(v, list) and v
                    ),
                    [],
                )
            
            if minute_chart_list:
                if verbose:
                    logger.debug(f"📊 [CHART] 분봉 파싱: raw={len(minute_chart_list)}")
                
                for item in minute_chart_list:
                    cntr_tm = str(item.get('cntr_tm', '') or '')
                    if trade_yyyymmdd and len(cntr_tm) >= 8 and cntr_tm[:8] != trade_yyyymmdd:
                        continue
                    open_price = abs(_parse_kiwoom_int(item.get('open_pric', 0)))
                    high_price = abs(_parse_kiwoom_int(item.get('high_pric', 0)))
                    low_price = abs(_parse_kiwoom_int(item.get('low_pric', 0)))
                    close_price = abs(_parse_kiwoom_int(item.get('cur_prc', 0)))
                    # 일봉과 동일: 부호·제로패딩 문자열 → 절대 거래량
                    volume = abs(_parse_kiwoom_int(item.get('trde_qty', 0)))
                    
                    if len(cntr_tm) >= 12:
                        formatted_date = f"{cntr_tm[:4]}-{cntr_tm[4:6]}-{cntr_tm[6:8]} {cntr_tm[8:10]}:{cntr_tm[10:12]}:00"
                    else:
                        formatted_date = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                    
                    chart_data.append({
                        "timestamp": formatted_date,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": volume,
                    })
                    
            elif daily_chart_list:
                if verbose:
                    logger.debug(f"📊 [CHART] 일봉 파싱: {len(daily_chart_list)}개")
                for item in daily_chart_list:
                    dt = item.get('dt', '')
                    open_price = abs(_parse_kiwoom_int(item.get('open_pric', 0)))
                    high_price = abs(_parse_kiwoom_int(item.get('high_pric', 0)))
                    low_price = abs(_parse_kiwoom_int(item.get('low_pric', 0)))
                    close_price = abs(_parse_kiwoom_int(item.get('cur_prc', 0)))
                    volume = abs(_parse_kiwoom_int(item.get('trde_qty', 0)))
                    
                    # 날짜 형식 변환: YYYYMMDD → YYYY-MM-DD 15:30:00 (장마감 기준)
                    if len(dt) == 8:
                        formatted_date = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} 15:30:00"
                    else:
                        formatted_date = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                    
                    chart_data.append({
                        "timestamp": formatted_date,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": volume
                    })
            
            chart_data.sort(key=lambda x: x['timestamp'])
            
            if verbose:
                logger.debug(f"차트 파싱 완료: {stock_code}, {len(chart_data)}봉")
            return chart_data
            
        except Exception as e:
            logger.error(f"차트 데이터 파싱 중 오류: {e}")
            return []
