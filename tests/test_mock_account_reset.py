import json
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import utils.mock_account_reset as mar
from core.config import Config
from core.models import AccountBalanceSnapshot, Base, PendingBuySignal, Position, PositionBuyFill, SellOrder


@pytest.fixture
def env(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(mar, "STATE_PATH", logs / "_mock_account_state.json")
    monkeypatch.setattr(mar, "BACKUP_ROOT", tmp_path / "backups")
    ledger = logs / "_ma1592_ledger_activity.jsonl"
    ledger.write_text('{"m": 1}\n', encoding="utf-8")
    monkeypatch.setattr(mar, "_MOVE_FILES", [ledger])
    rwm = logs / "_rwm_bowl_state.json"
    rwm.write_text(json.dumps({"watch": {"005930": {}}, "exits": {"1": {"x": 1}}}), encoding="utf-8")
    monkeypatch.setattr(mar, "_RWM_STATE_PATH", rwm)

    monkeypatch.setattr(Config, "KIWOOM_USE_MOCK_ACCOUNT", True)
    monkeypatch.setattr(Config, "KIWOOM_MOCK_APP_KEY", "old-key")
    monkeypatch.setattr(Config, "KIWOOM_MOCK_APP_SECRET", "old-secret")
    monkeypatch.setattr(Config, "KIWOOM_MOCK_ACCOUNT_NUMBER", "11112222")

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime(2026, 9, 1, 1, 0)
    for pid, mode in ((1, "mock"), (2, "real")):
        db.add(Position(id=pid, stock_code="005930", stock_name="삼성전자", buy_price=1000,
                        buy_quantity=1, buy_amount=1000, buy_time=now, account_mode=mode))
        db.add(SellOrder(position_id=pid, stock_code="005930", stock_name="삼성전자", sell_price=1100,
                         sell_quantity=1, sell_amount=1100, sell_reason="TAKE_PROFIT"))
        db.add(PositionBuyFill(position_id=pid, stock_code="005930", stock_name="삼성전자",
                               price=1000, quantity=1, amount=1000, filled_at=now))
        db.add(PendingBuySignal(condition_id=pid, stock_code="005930", stock_name="삼성전자",
                                detected_at=now, detected_date=date(2026, 9, 1), account_mode=mode))
    db.add(AccountBalanceSnapshot(as_of_date=date(2026, 9, 1), deposit_d0=1, deposit_d2=1,
                                  settlement_gap=0, account_type="모의투자"))
    db.add(AccountBalanceSnapshot(as_of_date=date(2026, 9, 2), deposit_d0=1, deposit_d2=1,
                                  settlement_gap=0, account_type="실계좌"))
    db.commit()
    yield db, tmp_path, ledger, rwm
    db.close()


def test_first_run_records_baseline_without_deleting(env):
    db, *_ = env
    report = mar.check_and_reset(db)
    assert report["action"] == "baseline"
    assert db.query(Position).count() == 2
    assert mar.load_state()["fingerprint"] == mar.current_identity()["fingerprint"]


def test_unchanged_identity_keeps_data(env):
    db, *_ = env
    mar.check_and_reset(db)
    assert mar.check_and_reset(db)["action"] == "unchanged"
    assert db.query(Position).count() == 2


def test_key_change_resets_only_mock_data(env, monkeypatch):
    db, tmp_path, ledger, rwm = env
    mar.check_and_reset(db)
    monkeypatch.setattr(Config, "KIWOOM_MOCK_APP_SECRET", "new-secret")

    report = mar.check_and_reset(db)

    assert report["action"] == "reset"
    assert report["deleted"] == {
        "sell_orders": 1, "position_buy_fills": 1, "positions": 1,
        "pending_buy_signals": 1, "account_balance_snapshots": 1,
    }
    assert [p.account_mode for p in db.query(Position).all()] == ["real"]
    assert [s.position_id for s in db.query(SellOrder).all()] == [2]
    assert [f.position_id for f in db.query(PositionBuyFill).all()] == [2]
    assert [s.account_mode for s in db.query(PendingBuySignal).all()] == ["real"]
    assert [s.account_type for s in db.query(AccountBalanceSnapshot).all()] == ["실계좌"]

    backup = Path(report["backup_dir"])
    assert backup.parent == tmp_path / "backups"
    assert json.loads((backup / "positions.json").read_text(encoding="utf-8"))[0]["id"] == 1
    assert (backup / "_ma1592_ledger_activity.jsonl").exists()
    assert not ledger.exists()
    rwm_state = json.loads(rwm.read_text(encoding="utf-8"))
    assert rwm_state["exits"] == {} and "005930" in rwm_state["watch"]

    state = mar.load_state()
    assert state["fingerprint"] == mar.current_identity()["fingerprint"]
    assert len(state["resets"]) == 1
    assert mar.check_and_reset(db)["action"] == "unchanged"


def test_dry_run_deletes_nothing(env, monkeypatch):
    db, _, ledger, _ = env
    mar.check_and_reset(db)
    monkeypatch.setattr(Config, "KIWOOM_MOCK_ACCOUNT_NUMBER", "99998888")
    report = mar.check_and_reset(db, apply=False)
    assert report["action"] == "dry_run"
    assert report["deleted"]["positions"] == 1
    assert db.query(Position).count() == 2
    assert ledger.exists()
    assert mar.check_and_reset(db, apply=False)["action"] == "dry_run"


def test_real_mode_skips(env, monkeypatch):
    db, *_ = env
    monkeypatch.setattr(Config, "KIWOOM_USE_MOCK_ACCOUNT", False)
    assert mar.check_and_reset(db, force=True)["action"] == "skipped_real"
    assert db.query(Position).count() == 2
