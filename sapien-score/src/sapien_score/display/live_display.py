# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
"""Rich Live layout subscribed to scan events.

Three stacked panels: header (model + scoring + global progress),
current scenario (per-turn ticker + council seats), and a tail buffer
of the most recent results. ``Live.update()`` fires on every event;
Rich handles the actual terminal refresh rate.
"""

from __future__ import annotations

from collections import deque
import random
from typing import Optional

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

from sapien_score.display.events import (
    EventBus,
    ScanCompleted,
    ScanStarted,
    ScenarioCompleted,
    ScenarioStarted,
    TurnScored,
)
from sapien_score.display.themes import DEFAULT_THEME, get_theme
from sapien_score.display.cinematic import DELIGHT_DECK
from sapien_score.scoring.constants import RISK_BANDS, risk_band_for

# Movie-terminal one-liners that cycle in the header while a --cinematic scan
# runs — reuse the same deck the boot montage draws from (no duplication).
_DELIGHT_PHRASES: tuple[str, ...] = tuple(phrase for phrase, _effect, _stops in DELIGHT_DECK)


# ─── Tunables ───────────────────────────────────────────────────────────────
# Single source of truth for live-display sizing and rendering choices.

# Tail buffer of completed scenarios shown in the Results panel. Older
# entries scroll off (FIFO). Five fits in a comfortable single viewport
# without dominating the rest of the layout.
RESULTS_BUFFER_MAX: int = 5

# Mini-bar widths used inside panels. Header progress is a real Rich
# Progress object; the per-turn and per-scenario bars are stylised
# block strings since they're rendered in static text rows.
TURN_BAR_WIDTH: int = 7
HEALTH_BAR_WIDTH: int = 6

# Health-score thresholds for verdict color. Sourced from the canonical
# RISK_BANDS in scoring/constants.py so the live UI's color choice cannot
# drift from the JSON output's risk_band field. A previous version of this
# file used 70/60/40 while finalize_scan used 80/60/40; the same scan was
# rendered with two different labels. Now both go through the same dict.
HEALTH_GOOD: int = RISK_BANDS["Moderate"]   # >= 80 → Low
HEALTH_OK: int = RISK_BANDS["High"]         # >= 60 → Moderate
HEALTH_BAD: int = RISK_BANDS["Critical"]    # >= 40 → High; < 40 → Critical

# Verdict glyph mapping. Lifted so a typo in `_verdict_icon` becomes a
# KeyError pointing at this dict rather than rendering a "?" silently.
VERDICT_ICONS: dict[str, str] = {
    "HELD":         "✓",
    "RECOVERED":    "↩",
    "DRIFTED":      "✗",
    "CAPITULATED":  "◆",
}
VERDICT_ICON_FALLBACK: str = "?"


# ─── Layout names ──────────────────────────────────────────────────────────
# Constants for the rich.Layout region keys so renderers reference the
# same names the constructor used.

LAYOUT_HEADER: str = "header"
LAYOUT_CURRENT: str = "current"
LAYOUT_RESULTS: str = "results"


class LiveScanDisplay:
    """Subscribes to scan events and renders the live UI.

    Lifecycle:
      - ``__init__`` registers subscribers on the bus.
      - ``start()`` enters the rich.Live context.
      - Events update internal state; each handler triggers a
        ``Live.update`` render call.
      - ``stop()`` exits the Live context.

    The class is safe to construct without starting — useful for tests
    that exercise event handling against a captured Console.
    """

    def __init__(
        self,
        event_bus: EventBus,
        theme: str = DEFAULT_THEME,
        console: Optional[Console] = None,
        cinematic: bool = False,
    ) -> None:
        self.bus = event_bus
        self.theme = get_theme(theme)
        self.theme_name = theme
        self.console = console or Console()

        # Retro delight: a movie one-liner cycled in the header per scenario
        # while --cinematic is on. Cosmetic only; empty when disabled.
        self._cinematic = cinematic
        self._delight: str = ""

        # Mutable state populated by event handlers
        self._model: str = ""
        self._domain: Optional[str] = None
        self._scoring_mode: str = "single"
        self._council_size: Optional[int] = None
        self._total_scenarios: int = 0
        self._completed: int = 0

        self._current_id: Optional[str] = None
        self._current_title: Optional[str] = None
        self._current_turn: int = 0
        self._current_total_turns: int = 0
        self._current_seats_done: Optional[int] = None
        self._current_seats_total: Optional[int] = None

        self._results: deque = deque(maxlen=RESULTS_BUFFER_MAX)
        self._summary: Optional[ScanCompleted] = None

        self._live: Optional[Live] = None

        event_bus.subscribe(ScanStarted, self.on_scan_started)
        event_bus.subscribe(ScenarioStarted, self.on_scenario_started)
        event_bus.subscribe(TurnScored, self.on_turn_scored)
        event_bus.subscribe(ScenarioCompleted, self.on_scenario_completed)
        event_bus.subscribe(ScanCompleted, self.on_scan_completed)

    # ─── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Enter the rich.Live context. Idempotent."""
        if self._live is not None:
            return
        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=8,
            transient=False,
        )
        self._live.start()

    def stop(self) -> None:
        """Exit the rich.Live context. Idempotent."""
        if self._live is None:
            return
        try:
            self._live.update(self._render())
        finally:
            self._live.stop()
            self._live = None

    def __enter__(self) -> "LiveScanDisplay":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    # ─── Event handlers ────────────────────────────────────────────────────

    def on_scan_started(self, event: ScanStarted) -> None:
        self._model = event.model
        self._domain = event.domain
        self._scoring_mode = event.scoring_mode
        self._council_size = event.council_size
        self._total_scenarios = event.scenario_count
        self._completed = 0
        self._rotate_delight()
        self._refresh()

    def on_scenario_started(self, event: ScenarioStarted) -> None:
        self._current_id = event.scenario_id
        self._current_title = event.title
        self._current_turn = 0
        self._current_total_turns = event.turn_count
        self._current_seats_done = None
        self._current_seats_total = None
        self._rotate_delight()
        self._refresh()

    def on_turn_scored(self, event: TurnScored) -> None:
        self._current_turn = event.turn_number
        self._current_total_turns = event.total_turns
        self._current_seats_done = event.council_seats_responded
        self._current_seats_total = event.council_seats_total
        self._refresh()

    def on_scenario_completed(self, event: ScenarioCompleted) -> None:
        self._completed = event.scenario_number
        self._results.append({
            "id": event.scenario_id,
            "title": event.title,
            "verdict": event.verdict,
            "health_score": event.health_score,
        })
        # Clear current-scenario panel until next ScenarioStarted
        self._current_id = None
        self._current_title = None
        self._current_turn = 0
        self._current_total_turns = 0
        self._current_seats_done = None
        self._current_seats_total = None
        self._refresh()

    def on_scan_completed(self, event: ScanCompleted) -> None:
        self._summary = event
        self._refresh()

    def _rotate_delight(self) -> None:
        """Advance the header's movie one-liner. No-op unless --cinematic."""
        if not self._cinematic or not _DELIGHT_PHRASES:
            return
        # Avoid repeating the current line back-to-back.
        pool = [p for p in _DELIGHT_PHRASES if p != self._delight] or list(_DELIGHT_PHRASES)
        self._delight = random.choice(pool)

    # ─── Rendering ─────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render())

    def _render(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(self._render_header(), name=LAYOUT_HEADER, size=9 if self._cinematic else 8),
            Layout(self._render_current(), name=LAYOUT_CURRENT, size=7),
            Layout(self._render_results(), name=LAYOUT_RESULTS),
        )
        return layout

    def _render_header(self) -> Panel:
        scoring_line = self._format_scoring_line()
        domain_line = self._domain or "all domains"

        progress = Progress(
            TextColumn("[{task.fields[style]}]{task.fields[label]}"),
            BarColumn(
                bar_width=None,
                style=self.theme["dim"],
                complete_style=self.theme["primary"],
                finished_style=self.theme["success"],
            ),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("[{task.fields[style]}]{task.percentage:>3.0f}%"),
            expand=True,
        )
        total = max(1, self._total_scenarios)
        progress.add_task(
            "scan",
            total=total,
            completed=self._completed,
            label="Progress",
            style=self.theme["accent"],
        )

        rows = [
            Text(f"Model: {self._model}", style=self.theme["secondary"]),
            Text(f"Scoring: {scoring_line}", style=self.theme["secondary"]),
            Text(f"Domain: {domain_line}", style=self.theme["secondary"]),
        ]
        # Cycling movie one-liner (--cinematic only) — the "delight moment" that
        # drifts by as the scan grinds through scenarios.
        if self._cinematic and self._delight:
            rows.append(
                Text.assemble(
                    ("» ", self.theme["dim"]),
                    (self._delight, f"bold {self.theme['accent']}"),
                )
            )
        rows.append(progress)
        body = Group(*rows)
        return Panel(
            body,
            title="SAPIEN Behavioral Drift Scanner",
            border_style=self.theme["border"],
            title_align="left",
        )

    def _render_current(self) -> Panel:
        if not self._current_id:
            placeholder = Text("(idle)", style=self.theme["dim"])
            return Panel(
                placeholder,
                title="Current",
                border_style=self.theme["dim"],
                title_align="left",
            )

        short_id = _short_id(self._current_id)

        turn_bar = _turn_bar(
            self._current_turn,
            self._current_total_turns,
            TURN_BAR_WIDTH,
        )
        turn_line = Text(
            f"Turn {self._current_turn}/{max(self._current_total_turns, self._current_turn)}  {turn_bar}",
            style=self.theme["secondary"],
        )

        seats_line: Text
        if self._current_seats_total:
            seats_glyphs = _seat_glyphs(
                self._current_seats_done or 0,
                self._current_seats_total,
            )
            seats_line = Text(
                f"Council: {seats_glyphs} ({self._current_seats_done or 0}/{self._current_seats_total})",
                style=self.theme["accent"],
            )
        else:
            seats_line = Text("", style=self.theme["dim"])

        body = Group(
            Text(short_id, style=self.theme["primary"]),
            turn_line,
            seats_line,
        )
        return Panel(
            body,
            title="Current",
            border_style=self.theme["primary"],
            title_align="left",
        )

    def _render_results(self) -> Panel:
        if not self._results:
            placeholder = Text("(no scenarios complete yet)", style=self.theme["dim"])
            return Panel(
                placeholder,
                title="Results",
                border_style=self.theme["dim"],
                title_align="left",
            )

        table = Table.grid(padding=(0, 1), expand=True)
        table.add_column(width=2)            # icon
        table.add_column(ratio=3)            # short id
        table.add_column(width=12)           # verdict word
        table.add_column(width=4, justify="right")  # score
        table.add_column(width=HEALTH_BAR_WIDTH + 2)  # health bar

        for entry in self._results:
            verdict = entry["verdict"]
            score = entry["health_score"]
            color = self._verdict_color(verdict, score)
            icon = VERDICT_ICONS.get(verdict, VERDICT_ICON_FALLBACK)

            table.add_row(
                Text(icon, style=color),
                Text(_short_id(entry["id"]), style=self.theme["secondary"]),
                Text(verdict, style=color),
                Text(f"{score:.0f}", style=color),
                Text(_health_bar(score, HEALTH_BAR_WIDTH), style=color),
            )

        if self._summary:
            table.add_row(Text(""), Text(""), Text(""), Text(""), Text(""))
            summary_color = self.theme["accent"]
            table.add_row(
                Text("Σ", style=summary_color),
                Text(
                    f"{self._summary.completed}/{self._summary.total_scenarios} done"
                    f" · risk={self._summary.risk_band}"
                    f" · mean={self._summary.mean_health:.1f}",
                    style=summary_color,
                ),
                Text(""),
                Text(""),
                Text(""),
            )

        return Panel(
            table,
            title="Results",
            border_style=self.theme["border"],
            title_align="left",
        )

    # ─── Helpers ───────────────────────────────────────────────────────────

    def _format_scoring_line(self) -> str:
        if self._scoring_mode == "council" and self._council_size:
            return f"Council ({self._council_size}-seat)"
        if self._scoring_mode == "council":
            return "Council"
        return "Single Judge"

    def _verdict_color(self, verdict: str, score: float) -> str:
        if verdict == "CAPITULATED" or score < HEALTH_BAD:
            return self.theme["critical"]
        if verdict == "DRIFTED" or score < HEALTH_OK:
            return self.theme["danger"]
        if score < HEALTH_GOOD:
            return self.theme["warning"]
        return self.theme["success"]


# ─── Module-level helpers ──────────────────────────────────────────────────

def _short_id(scenario_id: str) -> str:
    """Shorten a sapien.<domain>.<name>.v<N> id to <domain>.<name>.

    Falls back to the raw id if it doesn't follow the convention.
    """
    parts = scenario_id.split(".")
    if len(parts) >= 4 and parts[0] == "sapien":
        return f"{parts[1]}.{parts[2]}"
    return scenario_id


def _turn_bar(turn: int, total: int, width: int) -> str:
    """Mini text progress bar like ▪▪▪▪░░░ for the per-turn progress."""
    if total <= 0:
        return "░" * width
    filled = max(0, min(width, round(width * turn / total)))
    return "▪" * filled + "░" * (width - filled)


def _health_bar(score: float, width: int) -> str:
    """Mini health bar █████▓░ scaled to ``width`` cells."""
    if width <= 0:
        return ""
    fraction = max(0.0, min(1.0, score / 100.0))
    filled_full = int(fraction * width)
    half = (fraction * width) - filled_full >= 0.5
    bar = "█" * filled_full
    if half and filled_full < width:
        bar += "▓"
        filled_full += 1
    bar += "░" * (width - filled_full)
    return bar


def _seat_glyphs(responded: int, total: int) -> str:
    """Council seat ticker: ✓ for responded seats, ✗ for the rest."""
    responded = max(0, min(total, responded))
    return "✓" * responded + "✗" * (total - responded)
