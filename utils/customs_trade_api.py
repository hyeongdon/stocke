"""관세청 공공데이터포털 수출입 OpenAPI 클라이언트."""
from __future__ import annotations

import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from core.config import Config

logger = logging.getLogger(__name__)

BASE = "https://apis.data.go.kr/1220000"
# 동작 확인된 경로 (포털 표기 nintemtrade 는 오타)
NITEM_PATH = f"{BASE}/nitemtrade/getNitemtradeList"
ITEM_PATH = f"{BASE}/Itemtrade/getItemtradeList"

# Itemtrade 403 등은 프로세스 동안 재시도하지 않음
_ITEMTRADE_UNAVAILABLE: Optional[bool] = None


def _service_key() -> str:
    return (Config.DATA_GO_KR_SERVICE_KEY or "").strip()


def _http_get(url: str, timeout: float = 25.0) -> Tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "stocke-trade-batch/1.0", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return int(e.code), body
    except Exception as e:
        raise RuntimeError(f"HTTP 요청 실패: {e}") from e


def _parse_items(xml_text: str) -> Tuple[str, str, List[Dict[str, str]]]:
    root = ET.fromstring(xml_text)
    header = root.find("header")
    code = (header.findtext("resultCode") if header is not None else None) or ""
    msg = (header.findtext("resultMsg") if header is not None else None) or ""
    items: List[Dict[str, str]] = []
    body = root.find("body")
    if body is None:
        return code, msg, items
    for el in body.findall(".//item"):
        row = {child.tag: (child.text or "").strip() for child in el}
        items.append(row)
    return code, msg, items


def _num(v: Any) -> float:
    try:
        s = str(v or "").replace(",", "").strip()
        if not s or s == "-":
            return 0.0
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def fetch_nitemtrade(
    *,
    strt_yymm: str,
    end_yymm: str,
    hs_sgn: str,
    cnty_cd: str,
    num_of_rows: int = 500,
) -> List[Dict[str, Any]]:
    """품목별 국가별 수출입. 기간은 1년 이내 권장."""
    key = _service_key()
    if not key:
        raise RuntimeError("DATA_GO_KR_SERVICE_KEY 미설정")
    enc = urllib.parse.quote(key, safe="")
    params = (
        f"serviceKey={enc}"
        f"&strtYymm={strt_yymm}&endYymm={end_yymm}"
        f"&hsSgn={hs_sgn}&cntyCd={cnty_cd}"
        f"&numOfRows={num_of_rows}&pageNo=1"
    )
    status, text = _http_get(f"{NITEM_PATH}?{params}")
    if status != 200:
        raise RuntimeError(f"nitemtrade HTTP {status}: {text[:200]}")
    code, msg, items = _parse_items(text)
    if code not in ("00", "0", ""):
        raise RuntimeError(f"nitemtrade API 오류 {code}: {msg}")
    out: List[Dict[str, Any]] = []
    for it in items:
        year = str(it.get("year") or "").strip()
        if not year or year in ("총계", "합계", "-"):
            continue
        # "2025.01" → 202501
        period = year.replace(".", "").replace("-", "")[:6]
        if len(period) != 6 or not period.isdigit():
            continue
        hs = str(it.get("hsCd") or hs_sgn).strip().replace("-", "")
        if not hs:
            continue
        out.append(
            {
                "period_yyyymm": period,
                "hs_code": hs,
                "exp_usd": _num(it.get("expDlr")),
                "imp_usd": _num(it.get("impDlr")),
                "exp_wgt": _num(it.get("expWgt")),
                "imp_wgt": _num(it.get("impWgt")),
                "cnty_cd": cnty_cd,
            }
        )
    return out


def fetch_itemtrade(
    *,
    strt_yymm: str,
    end_yymm: str,
    hs_sgn: str,
    num_of_rows: int = 500,
) -> List[Dict[str, Any]]:
    """품목별(전세계) — 활용신청 필요. 미승인 시 403."""
    key = _service_key()
    if not key:
        raise RuntimeError("DATA_GO_KR_SERVICE_KEY 미설정")
    enc = urllib.parse.quote(key, safe="")
    params = (
        f"serviceKey={enc}"
        f"&strtYymm={strt_yymm}&endYymm={end_yymm}"
        f"&hsSgn={hs_sgn}"
        f"&numOfRows={num_of_rows}&pageNo=1"
    )
    status, text = _http_get(f"{ITEM_PATH}?{params}")
    if status == 403:
        raise PermissionError("Itemtrade 403 — 품목별 API 활용신청 필요")
    if status != 200:
        raise RuntimeError(f"Itemtrade HTTP {status}: {text[:200]}")
    code, msg, items = _parse_items(text)
    if code not in ("00", "0", ""):
        raise RuntimeError(f"Itemtrade API 오류 {code}: {msg}")
    out: List[Dict[str, Any]] = []
    for it in items:
        year = str(it.get("year") or it.get("statKor") or "").strip()
        period = ""
        for key_name in ("year", "yymm", "period", "date"):
            raw = str(it.get(key_name) or "").replace(".", "").replace("-", "")
            if len(raw) >= 6 and raw[:6].isdigit():
                period = raw[:6]
                break
        if not period:
            continue
        hs = str(it.get("hsCd") or it.get("hsSgn") or hs_sgn).strip()
        out.append(
            {
                "period_yyyymm": period,
                "hs_code": hs,
                "exp_usd": _num(it.get("expDlr") or it.get("expUsd")),
                "imp_usd": _num(it.get("impDlr") or it.get("impUsd")),
                "exp_wgt": _num(it.get("expWgt")),
                "imp_wgt": _num(it.get("impWgt")),
                "cnty_cd": "ALL",
            }
        )
    return out


def fetch_hs_monthly_world(
    *,
    hs_sgn: str,
    strt_yymm: str,
    end_yymm: str,
    countries: List[str],
    sleep_sec: float = 0.15,
    prefer_itemtrade: bool = True,
) -> Tuple[List[Dict[str, Any]], str]:
    """관심 HS 월별 전세계(또는 주요국 합) 수출입.

    Returns:
        (rows, source_label)
    """
    global _ITEMTRADE_UNAVAILABLE
    if prefer_itemtrade and _ITEMTRADE_UNAVAILABLE is not True:
        try:
            rows = fetch_itemtrade(strt_yymm=strt_yymm, end_yymm=end_yymm, hs_sgn=hs_sgn)
            if rows:
                _ITEMTRADE_UNAVAILABLE = False
                return rows, "data.go.kr/Itemtrade"
        except PermissionError:
            _ITEMTRADE_UNAVAILABLE = True
            logger.info("Itemtrade 미승인 — nitemtrade 주요국 합산으로 대체")
        except Exception as e:
            _ITEMTRADE_UNAVAILABLE = True
            logger.warning("Itemtrade 실패(%s) — nitemtrade fallback", e)

    # 주요국 합산
    bucket: Dict[Tuple[str, str], Dict[str, float]] = {}
    for cnty in countries:
        try:
            rows = fetch_nitemtrade(
                strt_yymm=strt_yymm,
                end_yymm=end_yymm,
                hs_sgn=hs_sgn,
                cnty_cd=cnty,
            )
        except Exception as e:
            logger.warning("nitemtrade 실패 hs=%s cnty=%s: %s", hs_sgn, cnty, e)
            time.sleep(sleep_sec)
            continue
        for r in rows:
            key = (r["period_yyyymm"], str(r["hs_code"]))
            agg = bucket.setdefault(
                key,
                {"exp_usd": 0.0, "imp_usd": 0.0, "exp_wgt": 0.0, "imp_wgt": 0.0},
            )
            agg["exp_usd"] += float(r["exp_usd"])
            agg["imp_usd"] += float(r["imp_usd"])
            agg["exp_wgt"] += float(r.get("exp_wgt") or 0)
            agg["imp_wgt"] += float(r.get("imp_wgt") or 0)
        time.sleep(sleep_sec)

    out = [
        {
            "period_yyyymm": period,
            "hs_code": hs,
            "exp_usd": vals["exp_usd"],
            "imp_usd": vals["imp_usd"],
            "exp_wgt": vals["exp_wgt"],
            "imp_wgt": vals["imp_wgt"],
            "cnty_cd": "PARTNERS",
        }
        for (period, hs), vals in sorted(bucket.items())
    ]
    return out, "data.go.kr/nitemtrade+partners"
