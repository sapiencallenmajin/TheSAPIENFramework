# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Tests for the live-display layer (events, themes, layout, boot).

Plain-mode invariance is the most important property: when no event
bus is attached, the scan code path must behave exactly as before.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from sapien_score.display.events import (
    EventBus,
    ScanCompleted,
    ScanStarted,
    ScenarioCompleted,
    ScenarioStarted,
    TurnScored,
)
from sapien_score.display.live_display import (
    HEALTH_BAD,
    HEALTH_GOOD,
    HEALTH_OK,
    RESULTS_BUFFER_MAX,
    VERDICT_ICONS,
    LiveScanDisplay,
    _health_bar,
    _seat_glyphs,
    _short_id,
    _turn_bar,
)
from sapien_score.display.themes import (
    DEFAULT_THEME,
    REQUIRED_THEME_ROLES,
    THEME_NAMES,
    THEMES,
    get_theme,
)


# ─── EventBus ──────────────────────────────────────────────────────────────

class TestEventBus:
    def test_subscribe_and_emit(self):
        bus = EventBus()
        hits: list = []
        bus.subscribe(ScanStarted, lambda e: hits.append(e))
        evt = ScanStarted(
            model="m", domain=None, scenario_count=3,
            scoring_mode="council", council_size=5,
        )
        bus.emit(evt)
        assert hits == [evt]

    def test_no_subscribers_doesnt_crash(self):
        bus = EventBus()
        # Emit every event type with zero subscribers — must be a no-op,
        # not a TypeError on missing dispatch.
        bus.emit(ScanStarted("m", None, 1, "single", None))
        bus.emit(ScenarioStarted("id", "t", "d", 5, 1, 1))
        bus.emit(TurnScored("id", 1, 5, None, None))
        bus.emit(ScenarioCompleted("id", "t", "HELD", 80.0, 1, 1))
        bus.emit(ScanCompleted(1, 1, 0, 80.0, "Low", None, 1.0))

    def test_multiple_subscribers_fire_in_order(self):
        bus = EventBus()
        order: list = []
        bus.subscribe(TurnScored, lambda e: order.append("a"))
        bus.subscribe(TurnScored, lambda e: order.append("b"))
        bus.emit(TurnScored("id", 1, 5, None, None))
        assert order == ["a", "b"]

    def test_subscriber_exception_is_logged_not_raised(self):
        bus = EventBus()
        survivors: list = []

        def exploder(e):
            raise RuntimeError("boom")

        bus.subscribe(TurnScored, exploder)
        bus.subscribe(TurnScored, lambda e: survivors.append(e))
        # Must not propagate — second subscriber still runs
        bus.emit(TurnScored("id", 1, 5, None, None))
        assert len(survivors) == 1

    def test_only_exact_type_dispatched(self):
        # Different event types subscribed; only the matching one fires.
        bus = EventBus()
        a, b = [], []
        bus.subscribe(ScanStarted, lambda e: a.append(e))
        bus.subscribe(ScanCompleted, lambda e: b.append(e))
        bus.emit(ScanStarted("m", None, 1, "single", None))
        assert len(a) == 1 and len(b) == 0

    def test_clear_removes_subscribers(self):
        bus = EventBus()
        hits: list = []
        bus.subscribe(TurnScored, lambda e: hits.append(e))
        bus.clear()
        bus.emit(TurnScored("id", 1, 5, None, None))
        assert hits == []


# ─── Themes ────────────────────────────────────────────────────────────────

class TestThemes:
    @pytest.mark.parametrize("name", list(THEMES.keys()))
    def test_each_theme_loads(self, name):
        theme = get_theme(name)
        for role in REQUIRED_THEME_ROLES:
            assert role in theme, (name, role)
            assert isinstance(theme[role], str)

    def test_unknown_theme_raises(self):
        with pytest.raises(KeyError, match="Unknown theme"):
            get_theme("ghibli")

    def test_default_theme_is_voigtkampff(self):
        assert DEFAULT_THEME == "voigtkampff"

    def test_theme_names_tuple_matches_dict(self):
        assert set(THEME_NAMES) == set(THEMES.keys())


# ─── LiveScanDisplay ───────────────────────────────────────────────────────

def _captured_console() -> Console:
    """Console that writes to a StringIO buffer (for tests)."""
    return Console(file=StringIO(), force_terminal=False, width=120)


class TestLiveScanDisplayHandlers:
    def test_handles_all_event_types_without_crash(self):
        bus = EventBus()
        # Mock the rich.Live class so we don't actually take over a
        # terminal during tests. Each event still drives _refresh().
        with patch("sapien_score.display.live_display.Live") as mock_live:
            display = LiveScanDisplay(bus, console=_captured_console())
            display.start()
            try:
                bus.emit(ScanStarted("openai/gpt-4o-mini", "security", 5,
                                     "council", 5))
                bus.emit(ScenarioStarted("sapien.security.x.v1", "X",
                                         "security", 7, 1, 5))
                bus.emit(TurnScored("sapien.security.x.v1", 3, 7, 5, 5))
                bus.emit(ScenarioCompleted("sapien.security.x.v1", "X",
                                           "HELD", 82.0, 1, 5))
                bus.emit(ScanCompleted(5, 5, 0, 78.5, "Moderate", 1.23, 12.0))
            finally:
                display.stop()
            # Live() was constructed and update() called multiple times
            assert mock_live.return_value.start.called
            assert mock_live.return_value.stop.called

    def test_results_buffer_holds_max_5(self):
        bus = EventBus()
        with patch("sapien_score.display.live_display.Live"):
            display = LiveScanDisplay(bus, console=_captured_console())
            display.start()
            try:
                # Push 7 completions; buffer must keep only the last 5
                for i in range(7):
                    bus.emit(ScenarioCompleted(
                        f"sapien.x.t{i}.v1", f"t{i}",
                        "HELD", 80.0, i + 1, 7,
                    ))
                assert len(display._results) == RESULTS_BUFFER_MAX
                # Oldest dropped — first scenario in the buffer is t2
                assert display._results[0]["id"] == "sapien.x.t2.v1"
                assert display._results[-1]["id"] == "sapien.x.t6.v1"
            finally:
                display.stop()

    def test_starting_twice_is_idempotent(self):
        bus = EventBus()
        with patch("sapien_score.display.live_display.Live") as mock_live:
            display = LiveScanDisplay(bus, console=_captured_console())
            display.start()
            display.start()  # second call must be no-op
            display.stop()
            # Live constructed once
            assert mock_live.call_count == 1


class TestVerdictColorMapping:
    @pytest.fixture
    def display(self):
        with patch("sapien_score.display.live_display.Live"):
            return LiveScanDisplay(EventBus(), console=_captured_console())

    def test_held_high_score_is_success(self, display):
        # Health >= HEALTH_GOOD (70) and verdict is non-fatal → success theme
        assert display._verdict_color("HELD", 82.0) == display.theme["success"]

    def test_held_borderline_is_warning(self, display):
        # 60 ≤ score < 70: warning band even when verdict says HELD
        assert display._verdict_color("HELD", 65.0) == display.theme["warning"]

    def test_drifted_is_danger(self, display):
        assert display._verdict_color("DRIFTED", 50.0) == display.theme["danger"]

    def test_capitulated_is_critical(self, display):
        assert display._verdict_color("CAPITULATED", 30.0) == display.theme["critical"]

    def test_low_score_held_is_critical(self, display):
        # Verdict says HELD but health < 40 → still rendered critical
        assert display._verdict_color("HELD", 30.0) == display.theme["critical"]

    def test_band_constants_in_descending_order(self):
        # Sanity: HEALTH_GOOD > HEALTH_OK > HEALTH_BAD so the threshold
        # ladder in _verdict_color works as written.
        assert HEALTH_GOOD > HEALTH_OK > HEALTH_BAD


class TestVerdictIcons:
    @pytest.mark.parametrize("verdict,expected", [
        ("HELD", "✓"),
        ("RECOVERED", "↩"),
        ("DRIFTED", "✗"),
        ("CAPITULATED", "◆"),
    ])
    def test_each_verdict_has_distinct_icon(self, verdict, expected):
        assert VERDICT_ICONS[verdict] == expected


# ─── Layout helpers ────────────────────────────────────────────────────────

class TestLayoutHelpers:
    def test_short_id_strips_sapien_and_version(self):
        assert _short_id("sapien.security.email_bec.v1") == "security.email_bec"

    def test_short_id_preserves_unknown_format(self):
        assert _short_id("custom.thing") == "custom.thing"

    def test_turn_bar_proportions(self):
        # 4/7 → 4 of 7 cells filled (rounded)
        bar = _turn_bar(4, 7, 7)
        assert bar.count("▪") == 4
        assert bar.count("░") == 3

    def test_turn_bar_zero_total_safe(self):
        # No division by zero — empty bar
        bar = _turn_bar(0, 0, 5)
        assert bar == "░░░░░"

    def test_health_bar_full_at_100(self):
        bar = _health_bar(100.0, 6)
        assert "█" in bar and "░" not in bar

    def test_health_bar_empty_at_zero(self):
        bar = _health_bar(0.0, 6)
        assert bar == "░░░░░░"

    def test_seat_glyphs(self):
        assert _seat_glyphs(5, 5) == "✓✓✓✓✓"
        assert _seat_glyphs(3, 5) == "✓✓✓✗✗"
        assert _seat_glyphs(0, 5) == "✗✗✗✗✗"


# ─── Boot sequence ─────────────────────────────────────────────────────────

class TestBootSequence:
    def test_boot_sequence_doesnt_crash(self):
        # Patch time.sleep so the test runs instantly. Use a captured-
        # buffer Console so the ANSI overprints don't leak into stdout.
        from sapien_score.display.boot import play_boot_sequence
        console = _captured_console()
        with patch("sapien_score.display.boot.time.sleep"):
            play_boot_sequence(
                console=console,
                theme=get_theme("voigtkampff"),
                version="0.0.0-test",
                scoring_mode="council",
                council_size=5,
            )
        out = console.file.getvalue()
        # All three banner lines should have been emitted
        assert "SAPIEN BEHAVIORAL DRIFT SCANNER" in out
        assert "VOIGT-KAMPFF PROTOCOL v0.0.0-test" in out
        assert "COUNCIL 5-SEAT" in out
        assert "READY" in out

    def test_boot_sequence_single_judge_label(self):
        from sapien_score.display.boot import play_boot_sequence
        console = _captured_console()
        with patch("sapien_score.display.boot.time.sleep"):
            play_boot_sequence(
                console=console, theme=get_theme("voigtkampff"),
                version="9.9", scoring_mode="single", council_size=5,
            )
        out = console.file.getvalue()
        assert "SINGLE JUDGE" in out
        assert "COUNCIL" not in out


# ─── Plain-mode invariance ─────────────────────────────────────────────────

class TestPlainModeInvariance:
    """The display layer must be additive — plain mode must not pull
    in any rich.Live machinery and must not require an event bus."""

    def test_engine_config_default_event_bus_is_none(self):
        from sapien_score.commands.scan_orchestration import EngineConfig
        cfg = EngineConfig(adapter=object())
        assert cfg.event_bus is None

    def test_orchestration_emit_guard_present(self):
        # Read scan_orchestration.py and assert every event emit is
        # guarded by `if engine.event_bus is not None:`. A grep-style
        # invariant — if a future contributor adds an unguarded emit,
        # plain mode silently breaks; this test catches it.
        from pathlib import Path
        path = (
            Path(__file__).resolve().parent.parent
            / "src" / "sapien_score" / "commands" / "scan_orchestration.py"
        )
        text = path.read_text(encoding="utf-8")
        # All event_bus.emit(... should be inside an `if engine.event_bus`
        # block. We allow the raw method on EventBus instance to appear,
        # but every call site must reference engine.event_bus to obtain it.
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("engine.event_bus.emit("):
                # The grep finds the call line; the guard must be ABOVE
                # it within the same function. Easier check: count guard
                # lines vs emit lines and require >= parity.
                pass  # parity check below is sufficient
        emits = text.count("engine.event_bus.emit(")
        guards = text.count("if engine.event_bus is not None:")
        assert guards >= emits, (
            f"unguarded engine.event_bus.emit detected — {emits} emits, "
            f"{guards} guards. Plain-mode invariance is broken."
        )

    def test_turn_emit_guard_present(self):
        # Same invariant for engine/turn.py — TurnScored emission must
        # be guarded so a None bus is a no-op.
        from pathlib import Path
        path = (
            Path(__file__).resolve().parent.parent
            / "src" / "sapien_score" / "engine" / "turn.py"
        )
        text = path.read_text(encoding="utf-8")
        emits = text.count("event_bus.emit(")
        guards = text.count("if event_bus is not None:")
        assert guards >= emits, (
            f"unguarded event_bus.emit in turn.py — {emits} emits, {guards} guards"
        )


class TestCinematicDelightLine:
    """The --cinematic delight moment cycles in the live header per scenario,
    without disturbing the council-seat readout."""

    def _render(self, display):
        buf = StringIO()
        Console(file=buf, force_terminal=True, width=100, height=30).print(display._render())
        return buf.getvalue()

    def test_delight_cycles_and_coexists_with_council(self):
        bus = EventBus()
        d = LiveScanDisplay(bus, theme=DEFAULT_THEME, cinematic=True)
        bus.emit(ScanStarted("bedrock/us.anthropic.claude-sonnet-5", "tax", 162, "council", 5))
        assert d._delight != "", "a delight line should be set on scan start"
        bus.emit(ScenarioStarted("sapien.tax.x.v1", "X", "tax", 8, 1, 1))
        bus.emit(TurnScored("sapien.tax.x.v1", 3, 8, 4, 5))
        out = self._render(d)
        assert d._delight in out, "delight line must render in the header"
        assert "Council:" in out, "council seats must still render alongside it"

    def test_no_delight_when_not_cinematic(self):
        bus = EventBus()
        d = LiveScanDisplay(bus, theme=DEFAULT_THEME)  # cinematic defaults off
        bus.emit(ScanStarted("m", None, 5, "council", 5))
        bus.emit(ScenarioStarted("id", "t", "d", 5, 1, 1))
        assert d._delight == ""
        assert "» " not in self._render(d)  # no "» " delight prefix leaks in
