from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ExitDecision:
    should_exit: bool
    fraction_pct: float = 0
    reason: str = ""


class ExitEngine:
    def __init__(self, settings):
        self.settings = settings

    def decide(self, position: dict, current_price_sol: float) -> ExitDecision:
        entry = float(position["entry_price_sol"])
        if entry <= 0:
            return ExitDecision(False)
        multiple = current_price_sol / entry
        peak = max(float(position.get("peak_multiple") or 1), multiple)

        # Reactive hard exit first.
        reactive = self.settings.reactive_exit_drop_pct
        if peak > 1 and multiple <= peak * (1 - reactive / 100):
            return ExitDecision(True, 100, f"reactive drawdown {reactive:.1f}% from peak")

        # Ladder.
        if self.settings.take_profit_enabled:
            sold_markers = {float(x) for x in position.get("tp_markers", [])}
            for target, sell_pct in self.settings.tp_ladder:
                target = float(target)
                if multiple >= target and target not in sold_markers:
                    return ExitDecision(True, float(sell_pct) * 100, f"TP {target:.2f}x")

        # Trailing stop.
        trail = self.settings.trailing_stop_pct
        if peak > 1 and multiple <= peak * (1 - trail / 100):
            return ExitDecision(True, 100, f"trailing stop {trail:.1f}%")

        opened = datetime.fromisoformat(position["opened_at"])
        age_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
        if age_min >= self.settings.time_stop_minutes:
            return ExitDecision(True, 100, f"time stop {self.settings.time_stop_minutes}m")

        return ExitDecision(False)
