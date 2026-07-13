"""청산 포지션 중 sell_orders(COMPLETED) 누락 건 보정."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import get_db
from utils.position_sell_backfill import repair_missing_sell_orders


def main() -> int:
    for db in get_db():
        n = repair_missing_sell_orders(db)
        print(f"보정 완료: {n}건")
        break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
