"""수출입 업종/테마 집계 저장·조회."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from core.models import MtiHsMap, TagMtiMap, TradeHsMonthly, TradeIndustryMonthly
from utils.datetime_kst import utc_now_naive

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASKETS_PATH = os.path.join(PROJECT_ROOT, "config", "trade_industry_baskets.json")


def load_baskets_config(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or BASKETS_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def seed_maps_from_baskets(session: Session, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    cfg = cfg or load_baskets_config()
    version = str(cfg.get("version") or "manual_v1")
    n_mti = n_tag = 0
    for b in cfg.get("baskets") or []:
        tag = str(b.get("tag") or "").strip()
        mti = str(b.get("mti_code") or "").strip()
        if not tag or not mti:
            continue
        existing = (
            session.query(TagMtiMap)
            .filter(TagMtiMap.tag_name == tag, TagMtiMap.mti_code == mti)
            .first()
        )
        if existing:
            existing.note = b.get("note")
            existing.updated_at = utc_now_naive()
        else:
            session.add(
                TagMtiMap(
                    tag_name=tag,
                    mti_code=mti,
                    weight=1.0,
                    note=b.get("note"),
                    updated_at=utc_now_naive(),
                )
            )
            n_tag += 1
        for hs in b.get("hs_codes") or []:
            hs_c = str(hs).strip()
            if not hs_c:
                continue
            row = (
                session.query(MtiHsMap)
                .filter(
                    MtiHsMap.mti_code == mti,
                    MtiHsMap.hs_code == hs_c,
                    MtiHsMap.mti_version == version,
                )
                .first()
            )
            if row:
                row.mti_name = str(b.get("mti_name") or tag)
                row.updated_at = utc_now_naive()
            else:
                session.add(
                    MtiHsMap(
                        mti_code=mti,
                        mti_name=str(b.get("mti_name") or tag),
                        hs_code=hs_c,
                        mti_version=version,
                        effective_from="2024-01",
                        updated_at=utc_now_naive(),
                    )
                )
                n_mti += 1
        # tag note에 산업명 힌트
        if existing:
            existing.note = str(b.get("mti_name") or b.get("note") or "")
            existing.updated_at = utc_now_naive()
    session.commit()
    return {"tag_mti_upserted": n_tag, "mti_hs_upserted": n_mti}


def upsert_hs_monthly(
    session: Session,
    rows: Sequence[Dict[str, Any]],
    *,
    source: str,
) -> int:
    n = 0
    now = utc_now_naive()
    for r in rows:
        period = str(r["period_yyyymm"])[:6]
        hs = str(r["hs_code"]).strip()
        # 4자리 prefix로 정규화해 바스켓 합산에 쓰기 쉽게 — 원본도 유지하되
        # 동일 period+hs+source면 합산 upsert
        existing = (
            session.query(TradeHsMonthly)
            .filter(
                TradeHsMonthly.period_yyyymm == period,
                TradeHsMonthly.hs_code == hs,
                TradeHsMonthly.source == source,
            )
            .first()
        )
        exp = float(r.get("exp_usd") or 0)
        imp = float(r.get("imp_usd") or 0)
        if existing:
            existing.exp_usd = exp
            existing.imp_usd = imp
            existing.exp_wgt = float(r.get("exp_wgt") or 0) or None
            existing.imp_wgt = float(r.get("imp_wgt") or 0) or None
            existing.fetched_at = now
        else:
            session.add(
                TradeHsMonthly(
                    period_yyyymm=period,
                    hs_code=hs,
                    exp_usd=exp,
                    imp_usd=imp,
                    exp_wgt=float(r.get("exp_wgt") or 0) or None,
                    imp_wgt=float(r.get("imp_wgt") or 0) or None,
                    source=source,
                    fetched_at=now,
                )
            )
        n += 1
    session.commit()
    return n


def _shift_yyyymm(yyyymm: str, months: int) -> str:
    y = int(yyyymm[:4])
    m = int(yyyymm[4:6])
    m += months
    while m <= 0:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return f"{y:04d}{m:02d}"


def _pct(cur: float, base: float) -> Optional[float]:
    if base is None or abs(base) < 1e-9:
        return None
    return round((cur - base) / abs(base) * 100.0, 2)


def recompute_industry_monthly(
    session: Session,
    cfg: Optional[Dict[str, Any]] = None,
    *,
    source: str = "data.go.kr",
) -> Dict[str, int]:
    """HS 원시 → MTI/tag/hs 집계 + YoY/MoM.

    - mti: 바스켓 1행(세분 산업코드)
    - tag: 동일 tag에 속한 mti 합산
    - hs: 바스켓 HS 4자리 prefix별
    """
    cfg = cfg or load_baskets_config()
    version = str(cfg.get("version") or "manual_v1")
    now = utc_now_naive()

    all_hs = session.query(TradeHsMonthly).filter(TradeHsMonthly.source == source).all()
    detailed: Dict[Tuple[str, str], Dict[str, float]] = {}
    has_child: Dict[Tuple[str, str], bool] = {}
    for row in all_hs:
        hs = row.hs_code or ""
        period = row.period_yyyymm
        prefix = hs[:4]
        if len(hs) > 4:
            has_child[(period, prefix)] = True
        key = (period, hs)
        d = detailed.setdefault(key, {"exp": 0.0, "imp": 0.0})
        d["exp"] += float(row.exp_usd or 0)
        d["imp"] += float(row.imp_usd or 0)

    def prefix_sum(period: str, prefix: str) -> Tuple[float, float]:
        exp = imp = 0.0
        if has_child.get((period, prefix)):
            for (p, hs), vals in detailed.items():
                if p == period and hs.startswith(prefix) and len(hs) > 4:
                    exp += vals["exp"]
                    imp += vals["imp"]
        else:
            for (p, hs), vals in detailed.items():
                if p == period and (hs == prefix or hs.startswith(prefix)):
                    exp += vals["exp"]
                    imp += vals["imp"]
        return exp, imp

    def series_for_prefixes(prefixes: List[str]) -> Dict[str, Tuple[float, float]]:
        periods = sorted({p for (p, _) in detailed.keys()})
        out: Dict[str, Tuple[float, float]] = {}
        for period in periods:
            exp = imp = 0.0
            for pref in prefixes:
                e, i = prefix_sum(period, pref)
                exp += e
                imp += i
            out[period] = (exp, imp)
        return out

    def write_grain(
        grain: str,
        grain_key: str,
        series: Dict[str, Tuple[float, float]],
        meta: Optional[Dict[str, Any]],
    ) -> int:
        n = 0
        for period, (exp, imp) in series.items():
            prev_m = series.get(_shift_yyyymm(period, -1))
            prev_y = series.get(_shift_yyyymm(period, -12))
            _upsert_industry(
                session,
                period=period,
                grain=grain,
                grain_key=grain_key,
                exp=exp,
                imp=imp,
                exp_mom=_pct(exp, prev_m[0]) if prev_m else None,
                imp_mom=_pct(imp, prev_m[1]) if prev_m else None,
                exp_yoy=_pct(exp, prev_y[0]) if prev_y else None,
                imp_yoy=_pct(imp, prev_y[1]) if prev_y else None,
                source=source,
                now=now,
                meta=meta,
            )
            n += 1
        return n

    written = 0
    tag_prefix_map: Dict[str, List[str]] = {}
    tag_mti_map: Dict[str, List[str]] = {}
    hs_meta: Dict[str, Dict[str, Any]] = {}

    # 바스켓 버전 변경 시 구 코드(SEMI 등) 잔존 방지
    session.query(TradeIndustryMonthly).filter(TradeIndustryMonthly.source == source).delete(
        synchronize_session=False
    )

    for b in cfg.get("baskets") or []:
        mti = str(b.get("mti_code") or "").strip()
        tag = str(b.get("tag") or "").strip()
        mti_name = str(b.get("mti_name") or tag).strip()
        prefixes = [str(x).strip()[:4] for x in (b.get("hs_codes") or []) if str(x).strip()]
        prefixes = list(dict.fromkeys(prefixes))
        if not mti or not prefixes:
            continue

        series = series_for_prefixes(prefixes)
        written += write_grain(
            "mti",
            mti,
            series,
            {
                "tag": tag,
                "mti_name": mti_name,
                "hs_prefixes": prefixes,
                "mti_version": version,
            },
        )

        tag_prefix_map.setdefault(tag, [])
        for pref in prefixes:
            if pref not in tag_prefix_map[tag]:
                tag_prefix_map[tag].append(pref)
        tag_mti_map.setdefault(tag, [])
        if mti not in tag_mti_map[tag]:
            tag_mti_map[tag].append(mti)

        for pref in prefixes:
            meta = hs_meta.setdefault(
                pref,
                {"tags": [], "mti_codes": [], "mti_names": [], "mti_version": version},
            )
            if tag and tag not in meta["tags"]:
                meta["tags"].append(tag)
            if mti and mti not in meta["mti_codes"]:
                meta["mti_codes"].append(mti)
            if mti_name and mti_name not in meta["mti_names"]:
                meta["mti_names"].append(mti_name)

    for tag, prefixes in tag_prefix_map.items():
        if not tag:
            continue
        series = series_for_prefixes(prefixes)
        written += write_grain(
            "tag",
            tag,
            series,
            {
                "mti_codes": tag_mti_map.get(tag) or [],
                "hs_prefixes": prefixes,
                "mti_version": version,
            },
        )

    for pref, meta in hs_meta.items():
        series = series_for_prefixes([pref])
        written += write_grain("hs", pref, series, meta)

    session.commit()
    return {"industry_rows": written}


def _upsert_industry(
    session: Session,
    *,
    period: str,
    grain: str,
    grain_key: str,
    exp: float,
    imp: float,
    exp_mom: Optional[float],
    imp_mom: Optional[float],
    exp_yoy: Optional[float],
    imp_yoy: Optional[float],
    source: str,
    now: datetime,
    meta: Optional[Dict[str, Any]],
) -> None:
    row = (
        session.query(TradeIndustryMonthly)
        .filter(
            TradeIndustryMonthly.period_yyyymm == period,
            TradeIndustryMonthly.grain == grain,
            TradeIndustryMonthly.grain_key == grain_key,
        )
        .first()
    )
    if row:
        row.exp_usd = exp
        row.imp_usd = imp
        row.exp_mom = exp_mom
        row.imp_mom = imp_mom
        row.exp_yoy = exp_yoy
        row.imp_yoy = imp_yoy
        row.source = source
        row.fetched_at = now
        row.meta_json = meta
    else:
        session.add(
            TradeIndustryMonthly(
                period_yyyymm=period,
                grain=grain,
                grain_key=grain_key,
                exp_usd=exp,
                imp_usd=imp,
                exp_mom=exp_mom,
                imp_mom=imp_mom,
                exp_yoy=exp_yoy,
                imp_yoy=imp_yoy,
                source=source,
                fetched_at=now,
                meta_json=meta,
            )
        )


def list_trade_monthly(
    session: Session,
    *,
    grain: str = "tag",
    grain_key: Optional[str] = None,
    period_from: Optional[str] = None,
    period_to: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    q = session.query(TradeIndustryMonthly).filter(TradeIndustryMonthly.grain == grain)
    if grain_key:
        q = q.filter(TradeIndustryMonthly.grain_key == grain_key)
    if period_from:
        q = q.filter(TradeIndustryMonthly.period_yyyymm >= period_from)
    if period_to:
        q = q.filter(TradeIndustryMonthly.period_yyyymm <= period_to)
    rows = (
        q.order_by(TradeIndustryMonthly.period_yyyymm.desc(), TradeIndustryMonthly.grain_key.asc())
        .limit(min(limit, 2000))
        .all()
    )
    return [
        {
            "period_yyyymm": r.period_yyyymm,
            "grain": r.grain,
            "grain_key": r.grain_key,
            "exp_usd": r.exp_usd,
            "imp_usd": r.imp_usd,
            "exp_yoy": r.exp_yoy,
            "imp_yoy": r.imp_yoy,
            "exp_mom": r.exp_mom,
            "imp_mom": r.imp_mom,
            "source": r.source,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
            "meta": r.meta_json,
        }
        for r in rows
    ]


def list_latest_by_grain(
    session: Session,
    *,
    grain: str = "tag",
    limit_keys: int = 50,
) -> Dict[str, Any]:
    """grain(tag|mti|hs)별 최신 월 스냅샷 + 시계열."""
    grain = (grain or "tag").strip().lower()
    if grain not in ("tag", "mti", "hs"):
        grain = "tag"
    keys = [
        r[0]
        for r in session.query(TradeIndustryMonthly.grain_key)
        .filter(TradeIndustryMonthly.grain == grain)
        .distinct()
        .all()
    ]
    keys = sorted(keys)
    latest_period = (
        session.query(TradeIndustryMonthly.period_yyyymm)
        .filter(TradeIndustryMonthly.grain == grain)
        .order_by(TradeIndustryMonthly.period_yyyymm.desc())
        .limit(1)
        .scalar()
    )
    items = []
    for key in keys:
        series = list_trade_monthly(session, grain=grain, grain_key=key, limit=36)
        series_asc = list(reversed(series))
        cur = next((x for x in series if x["period_yyyymm"] == latest_period), series[0] if series else None)
        meta = (cur or {}).get("meta") or {}
        label = key
        if grain == "mti":
            label = str(meta.get("mti_name") or key)
        elif grain == "hs":
            names = meta.get("mti_names") or []
            label = names[0] if names else f"HS {key}"
        elif grain == "tag":
            label = key
        items.append(
            {
                "grain": grain,
                "grain_key": key,
                "label": label,
                "tag": meta.get("tag") if grain == "mti" else (key if grain == "tag" else None),
                "tags": meta.get("tags"),
                "mti_code": key if grain == "mti" else None,
                "mti_name": meta.get("mti_name") if grain == "mti" else None,
                "hs_code": key if grain == "hs" else None,
                "latest": cur,
                "series": series_asc,
                "meta": meta,
            }
        )
    # 수출액 큰 순 (태그도 동일하게)
    items.sort(
        key=lambda it: float(((it.get("latest") or {}).get("exp_usd")) or 0),
        reverse=True,
    )
    items = items[: max(1, int(limit_keys))]
    return {"latest_period": latest_period, "grain": grain, "items": items}


def list_latest_by_tag(session: Session, *, limit_tags: int = 50) -> Dict[str, Any]:
    """하위 호환: 태그 grain."""
    return list_latest_by_grain(session, grain="tag", limit_keys=limit_tags)
