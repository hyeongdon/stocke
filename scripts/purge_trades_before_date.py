"""지정일(KST) 이전 매매 데이터 삭제."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from core.models import get_db, init_db

KST = timezone(timedelta(hours=9))


def _kst_date_of(v) -> date | None:
    if not v:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "")[:26])
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(KST).date()


def purge_before(keep_from: date) -> None:
    init_db()
    pos_ids: list[int] = []
    signal_ids: list[int] = []

    for db in get_db():
        session = db
        positions = session.execute(text("SELECT id, buy_time FROM positions")).fetchall()
        for pid, buy_time in positions:
            d = _kst_date_of(buy_time)
            if d is None or d < keep_from:
                pos_ids.append(int(pid))

        signals = session.execute(
            text("SELECT id, detected_at FROM pending_buy_signals")
        ).fetchall()
        for sid, detected_at in signals:
            d = _kst_date_of(detected_at)
            if d is None or d < keep_from:
                signal_ids.append(int(sid))

        if pos_ids:
            placeholders = ",".join(str(i) for i in pos_ids)
            session.execute(text(f"DELETE FROM sell_orders WHERE position_id IN ({placeholders})"))
            session.execute(text(f"DELETE FROM position_buy_fills WHERE position_id IN ({placeholders})"))
            session.execute(text(f"DELETE FROM positions WHERE id IN ({placeholders})"))

        if signal_ids:
            placeholders = ",".join(str(i) for i in signal_ids)
            session.execute(text(f"DELETE FROM pending_buy_signals WHERE id IN ({placeholders})"))

        session.commit()

        remaining_pos = session.execute(text("SELECT COUNT(*) FROM positions")).scalar()
        remaining_sell = session.execute(text("SELECT COUNT(*) FROM sell_orders")).scalar()
        remaining_sig = session.execute(text("SELECT COUNT(*) FROM pending_buy_signals")).scalar()
        remaining_fills = session.execute(text("SELECT COUNT(*) FROM position_buy_fills")).scalar()

        print(f"기준일(KST): {keep_from} 이후만 유지")
        print(f"삭제 포지션: {len(pos_ids)}건 {pos_ids}")
        print(f"삭제 신호: {len(signal_ids)}건")
        print(f"잔여 positions: {remaining_pos}, sell_orders: {remaining_sell}, signals: {remaining_sig}, buy_fills: {remaining_fills}")
        break


if __name__ == "__main__":
    # 6월 30일(含) 이후 유지 → 6월 30일 이전 삭제
    purge_before(date(2026, 6, 30))
