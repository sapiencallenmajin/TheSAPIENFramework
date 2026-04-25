# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
"""Scan-event types and a synchronous EventBus.

Producers emit events from ``commands/scan_orchestration`` (and the
turn / driver layers it calls). Subscribers — the Rich live display
or any future consumer — receive them via ``EventBus.subscribe(type,
callback)``.

Synchronous dispatch by design: events fire from inside the scan
loop's main thread, the rich.live.Live renderer is also single-thread,
and adding a queue would buy nothing but complexity. If a subscriber
ever blocks the loop badly, that's a bug in the subscriber.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ─── Event types ────────────────────────────────────────────────────────────

@dataclass
class ScanStarted:
    """Emitted once at the top of the scan, after setup_engine resolves."""
    model: str
    domain: Optional[str]
    scenario_count: int
    scoring_mode: str  # "council" | "single"
    council_size: Optional[int]


@dataclass
class ScenarioStarted:
    """Emitted before each scenario's conversation loop begins."""
    scenario_id: str
    title: str
    domain: str
    turn_count: int
    scenario_number: int  # 1-indexed
    total_scenarios: int


@dataclass
class TurnScored:
    """Emitted after each turn's scoring completes.

    For council mode, ``council_seats_responded`` and
    ``council_seats_total`` carry the per-turn vote progress so the
    live display can render the seat ticker (✓✓✓✗✓). Single-judge
    runs leave both fields as None.
    """
    scenario_id: str
    turn_number: int
    total_turns: int
    council_seats_responded: Optional[int]
    council_seats_total: Optional[int]


@dataclass
class ScenarioCompleted:
    """Emitted after each scenario's verdict is determined."""
    scenario_id: str
    title: str
    verdict: str  # HELD | DRIFTED | RECOVERED | CAPITULATED
    health_score: float
    scenario_number: int
    total_scenarios: int


@dataclass
class ScanCompleted:
    """Emitted once at finalize, with run-level aggregates."""
    total_scenarios: int
    completed: int
    failed: int
    mean_health: float
    risk_band: str
    total_cost: Optional[float]
    elapsed_seconds: float


# ─── EventBus ───────────────────────────────────────────────────────────────

class EventBus:
    """Synchronous typed pub/sub.

    Callers ``subscribe(EventType, callback)`` and ``emit(event)``.
    Dispatch is type-keyed: only callbacks registered for the exact
    event class fire (no MRO walking — the dataclasses don't inherit).

    Subscriber exceptions are caught and logged at WARNING — a broken
    display callback must never abort a scan that's already costing
    real API spend.
    """

    def __init__(self) -> None:
        self._subscribers: dict[type, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: type, callback: Callable) -> None:
        """Register ``callback`` for ``event_type``.

        Multiple subscribers per type are supported (registered in
        subscription order, called in that order at emit time).
        """
        self._subscribers[event_type].append(callback)

    def emit(self, event: object) -> None:
        """Dispatch ``event`` to every subscriber of its exact type.

        No-op when nothing is subscribed for the type. A subscriber
        raising is logged and swallowed so one broken handler can't
        cascade-fail the rest of the chain.
        """
        for callback in self._subscribers.get(type(event), ()):
            try:
                callback(event)
            except Exception as exc:
                logger.warning(
                    "EventBus subscriber %r raised on %s: %s",
                    callback, type(event).__name__, exc,
                )

    def clear(self) -> None:
        """Drop every subscriber. Useful in tests."""
        self._subscribers.clear()
