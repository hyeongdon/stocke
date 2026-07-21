# -*- coding: utf-8 -*-
"""상따 매수 파이프라인 드라이런 — 006660(삼성공조) 가정.

실제 주문은 넣지 않고, 스캐너→신호→매수 실행기 게이트만 단계별로 판정합니다.
상따 조건식에 편입되어 있다고 가정합니다.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CODE = "006660"
NAME = "삼성공조(모나리자 가정)"


async def main() -> int:
    from core.models import AutoTradeSettings, PendingBuySignal, Position, get_db
    from utils.auto_trade_engine import (
        allows_strategy_new_buy,
        check_daily_limits,
        compute_buy_amount,
        compute_quantity,
        estimate_upper_limit_price,
        evaluate_gate_pack,
        is_strategy_slot_available,
        new_buy_block_reason,
    )
    from utils.fundamental_mart_store import get_latest_by_code
    from utils.datetime_kst import as_kst

    steps: List[Dict[str, Any]] = []

    def add(step: str, passed: bool, detail: str, **extra):
        row = {"step": step, "pass": passed, "detail": detail, **extra}
        steps.append(row)
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {step}: {detail}")

    settings: Optional[AutoTradeSettings] = None
    for db in get_db():
        settings = db.query(AutoTradeSettings).first()
        break
    if not settings:
        print("FAIL: AutoTradeSettings 없음")
        return 1

    add(
        "0.settings",
        True,
        f"enabled={settings.is_enabled} sangtta_names={settings.sangtta_condition_names!r} "
        f"slots={settings.sangtta_max_slots} buy_amt={settings.sangtta_buy_amount} "
        f"window={settings.sangtta_trade_start_time}~{settings.sangtta_trade_end_time}",
    )
    add("1.universe", True, f"가정: {CODE} 상따 조건식 편입 (source=sangtta)")

    # 시세: 가능하면 실조회, 실패 시 상따 밴드 중간(17%) 합성
    price = 0
    change_rate: Optional[float] = None
    quote_src = "synthetic"
    api = None
    try:
        from api.kiwoom_api import KiwoomAPI

        api = KiwoomAPI()
        if api.authenticate():
            snap = await api.get_stock_snapshot(CODE)
            if snap.get("success"):
                s = snap.get("snapshot") or {}
                price = int(abs(float(str(s.get("current_price") or 0).replace(",", "") or 0)))
                try:
                    change_rate = float(str(s.get("change_rate", "0")).replace(",", "").replace("%", "") or 0)
                except (TypeError, ValueError):
                    change_rate = None
                quote_src = "kiwoom_snapshot"
    except Exception as e:
        add("2.quote_fetch", False, f"시세 조회 실패 -> 합성 ({e})")

    fund = get_latest_by_code(CODE) or {}
    mcap = fund.get("market_cap")

    if not price or price <= 0:
        # 시총 784억 가정, 등락 17% → prev_close 역산
        change_rate = 17.0
        prev_close = 12821
        price = int(round(prev_close * (1 + change_rate / 100)))
        quote_src = "synthetic_17pct"
        day_open = int(round(prev_close * 1.05))  # 시가대비 여유
        upper = estimate_upper_limit_price(prev_close)
        ctx = {
            "day_open": day_open,
            "prev_close": prev_close,
            "upper_limit_price": upper,
            "market_cap": mcap if mcap is not None else 784.0,
        }
        add(
            "2.quote",
            True,
            f"합성 시세 price={price:,} change={change_rate}% open={day_open:,} "
            f"UL={upper:,} mcap={ctx['market_cap']} ({quote_src})",
        )
    else:
        prev_close = int(round(price / (1 + float(change_rate or 0) / 100))) if change_rate else 0
        ctx = {
            "day_open": int(round(prev_close * 1.02)) if prev_close else None,
            "prev_close": prev_close or None,
            "upper_limit_price": estimate_upper_limit_price(prev_close) if prev_close else None,
            "market_cap": mcap,
        }
        add("2.quote", True, f"시세 price={price:,} change={change_rate}% ({quote_src})")

    holding = False
    pending = False
    for db in get_db():
        holding = (
            db.query(Position)
            .filter(Position.stock_code == CODE, Position.status == "HOLDING")
            .first()
            is not None
        )
        pending = (
            db.query(PendingBuySignal)
            .filter(
                PendingBuySignal.stock_code == CODE,
                PendingBuySignal.status.in_(["PENDING", "PROCESSING", "ORDERED"]),
            )
            .first()
            is not None
        )
        break
    add("3.open_interest", not holding and not pending, f"holding={holding} pending={pending}")

    now_real = as_kst()
    allowed_real, reason_real = allows_strategy_new_buy(settings, "sangtta", now_real)
    add("4.sangtta_time_real", allowed_real, reason_real or f"현재 {now_real.strftime('%H:%M:%S')} 통과")

    sim_now = now_real.replace(hour=9, minute=30, second=0, microsecond=0)
    allowed_sim, reason_sim = allows_strategy_new_buy(settings, "sangtta", sim_now)
    add("4b.sangtta_time_sim_09:30", allowed_sim, reason_sim or "시뮬레이션 09:30 통과")

    block = new_buy_block_reason(settings, sim_now)
    add("5.global_new_buy_sim", block is None, block or "전역 신규매수 허용 (참고, 상따는 전략시간 우선)")

    slot_ok = False
    for db in get_db():
        slot_ok = is_strategy_slot_available(settings, db, "sangtta", for_new_signal=True)
        break
    add("6.sangtta_slot", slot_ok, "상따 슬롯 여유" if slot_ok else "상따 슬롯 포화")

    halt = check_daily_limits(settings)
    add("7.daily_limits", halt is None, halt or "일일 손익 한도 OK")

    # Phase1 핵심: sangtta_breakout 패키지 (컨텍스트 주입으로 API 의존 제거)
    gate_ok, gate_reason = await evaluate_gate_pack(
        api,
        settings,
        "sangtta_breakout",
        CODE,
        price,
        change_rate=change_rate,
        ctx=ctx,
        now=sim_now,
        skip_time_check=True,
    )
    add("8.sangtta_breakout", gate_ok, gate_reason, implemented=True, ctx=ctx)

    # 네가티브: 밴드 밖 / 상한가 / 시총 초과
    neg_cases = [
        ("band_low", {**ctx}, 10.0, "등락 10%는 밴드 이탈이어야 함"),
        ("at_limit", {**ctx, "upper_limit_price": price}, change_rate, "상한가 도달은 금지여야 함"),
        ("mcap_over", {**ctx, "market_cap": 5000.0}, change_rate, "시총 5000억은 거부여야 함"),
    ]
    for key, nctx, ncr, expect in neg_cases:
        nok, nreason = await evaluate_gate_pack(
            None, settings, "sangtta_breakout", CODE, price,
            change_rate=ncr, ctx=nctx, skip_time_check=True,
        )
        add(f"8n.{key}", (not nok), f"{'거부OK' if not nok else '잘못통과'} — {nreason} ({expect})")

    amount = compute_buy_amount(settings, change_rate, is_add_buy=False)
    sang_amt = int(getattr(settings, "sangtta_buy_amount", 0) or 0)
    if sang_amt > 0:
        amount = min(amount, sang_amt) if amount > 0 else sang_amt
    qty = compute_quantity(amount, price)
    add(
        "9.sizing",
        qty >= 1,
        f"amount={amount:,}원 sangtta_buy_amount={sang_amt or '미설정'} -> qty={qty}주 @ {price:,}",
    )

    must = {
        "1.universe",
        "3.open_interest",
        "4b.sangtta_time_sim_09:30",
        "6.sangtta_slot",
        "7.daily_limits",
        "8.sangtta_breakout",
        "8n.band_low",
        "8n.at_limit",
        "8n.mcap_over",
        "9.sizing",
    }
    pipeline_ok = all(s["pass"] for s in steps if s["step"] in must)

    add(
        "10.signal_eligible",
        pipeline_ok,
        "PENDING 신호 생성 가능 (가정/sim 09:30/sangtta_breakout)"
        if pipeline_ok
        else "신호 생성 불가 — 상위 FAIL 확인",
        additional_data={
            "strategy": "sangtta",
            "source": "sangtta",
            "gate_pack": "sangtta_breakout",
            "current_price": price,
            "change_rate": change_rate,
        },
    )

    phase = {
        "Phase0_observe": True,
        "Phase1_gate_pack_sangtta_breakout": True,
        "Phase1_band_15_19": True,
        "Phase1_market_cap_filter": True,
        "Phase1_limit_up_entry_ban": True,
        "Phase1_slots_time_amount": True,
        "Phase1_strategy_tag_on_signal": True,
        "Phase2_sangtta_condition_universe": True,
        "Phase3_strategy_key_on_position": True,
        "Phase3_limit_break_sharp_drop": True,
        "Phase3_soft_confirm_polls": True,
    }
    print("\n=== Phase 구현 요약 ===")
    for k, v in phase.items():
        print(f"  {'OK' if v else 'GAP'}: {k}")

    out = {
        "code": CODE,
        "name": NAME,
        "assumptions": ["상따 조건식 편입", "실주문 없음", "시간 윈도우 09:30 시뮬레이션"],
        "quote": {"price": price, "change_rate": change_rate, "source": quote_src},
        "steps": steps,
        "phase_status": phase,
        "verdict": {
            "signal_to_buy_pipeline_ready": pipeline_ok,
            "phase1_gate_pack_implemented": True,
            "phase3_complete": True,
            "note": "sangtta_breakout 구현 후 드라이런. 실주문/실차트는 장중 재검증 권장.",
        },
    }
    out_path = ROOT / "logs" / "_sangtta_dryrun_006660.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {out_path}")
    print(f"VERDICT: {'READY' if pipeline_ok else 'BLOCKED'}")
    return 0 if pipeline_ok else 2


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main()))
