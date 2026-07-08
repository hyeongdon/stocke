"""detected_at KST→UTC 일회성 마이그레이션."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.models import get_db
from utils.datetime_kst import migrate_pending_detected_at_kst_to_utc

if __name__ == "__main__":
    for db in get_db():
        n = migrate_pending_detected_at_kst_to_utc(db)
        print(f"migrated detected_at rows: {n}")
        break
