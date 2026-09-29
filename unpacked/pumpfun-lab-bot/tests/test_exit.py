from datetime import datetime, timedelta, timezone

from src.exits import ExitEngine


class Settings:
    reactive_exit_drop_pct = 25
    trailing_stop_pct = 20
    time_stop_minutes = 30
    take_profit_enabled = True
    tp_ladder = [[1.5, 0.25], [2.0, 0.25], [3.0, 0.25]]


def test_take_profit():
    engine = ExitEngine(Settings())
    position = {
        "entry_price_sol": 1,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "peak_multiple": 1,
        "tp_markers": [],
    }
    decision = engine.decide(position, 1.5)
    assert decision.should_exit
    assert decision.reason.startswith("TP")


def test_time_stop():
    engine = ExitEngine(Settings())
    position = {
        "entry_price_sol": 1,
        "opened_at": (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(),
        "peak_multiple": 1,
        "tp_markers": [],
    }
    decision = engine.decide(position, 1)
    assert decision.should_exit
    assert "time stop" in decision.reason
