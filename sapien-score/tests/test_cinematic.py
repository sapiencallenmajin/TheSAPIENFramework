# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC
"""Guards for the cinematic boot/verdict layer.

The effects are pure delight — the contract that matters is that they stay
SILENT and SAFE everywhere they shouldn't fire (pipes, CI, --no-anim, captured
test consoles) and never touch the scan/score/publish path.
"""
from __future__ import annotations

import io

from rich.console import Console

from sapien_score.display import cinematic as c


class _FakeTTY(io.StringIO):
    """A StringIO that claims to be an interactive UTF-8 terminal."""
    encoding = "utf-8"

    def isatty(self) -> bool:
        return True


def _tty_console() -> Console:
    return Console(file=_FakeTTY())


class TestShouldAnimateGuards:
    def test_captured_non_tty_never_animates(self):
        # A plain StringIO (isatty False) — pipes, files, test buffers.
        assert c.should_animate(Console(file=io.StringIO())) is False

    def test_no_anim_flag_disables(self):
        assert c.should_animate(_tty_console(), no_anim=True) is False

    def test_no_color_env_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert c.should_animate(_tty_console()) is False

    def test_ci_env_disables(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        assert c.should_animate(_tty_console()) is False

    def test_missing_deps_disables(self, monkeypatch):
        monkeypatch.setattr(c, "_deps_available", lambda: False)
        assert c.should_animate(_tty_console()) is False

    def test_interactive_tty_with_deps_animates(self, monkeypatch):
        # The one True path: real TTY, UTF-8, deps present, no opt-out envs.
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setattr(c, "_deps_available", lambda: True)
        assert c.should_animate(_tty_console()) is True


class TestBootFallback:
    def test_non_tty_boot_falls_back_to_plain_banner(self):
        # When not animating, play_cinematic_boot must yield the existing plain
        # boot banner so behavior is unchanged for pipes/CI/tests.
        buf = io.StringIO()
        console = Console(file=buf, width=80)
        c.play_cinematic_boot(
            console=console, theme={}, version="0.2.0",
            scoring_mode="council", council_size=5,
        )
        out = buf.getvalue()
        assert "VOIGT-KAMPFF PROTOCOL" in out
        assert "COUNCIL 5-SEAT" in out

    def test_verdict_reveal_is_noop_when_not_animating(self):
        buf = io.StringIO()
        console = Console(file=buf)
        # Must not raise and must not emit the giant figlet reveal.
        c.reveal_verdict(console, {"score": 87, "rating": "Low Risk"})
        assert buf.getvalue() == ""


class TestTerminalHandoff:
    """The boot→live-display handoff must reset the terminal modes TTE toggles
    (cursor visibility, autowrap) and clear, so rich.Live starts clean. This is
    the regression for the broken council-panel render."""

    def test_restore_resets_cursor_and_autowrap(self, monkeypatch):
        cap = io.StringIO()
        monkeypatch.setattr("sys.stdout", cap)
        c._restore_terminal(Console(file=io.StringIO()), clear=True)
        seq = cap.getvalue()
        assert "\x1b[?25h" in seq, "cursor must be re-shown"
        assert "\x1b[?7h" in seq, "autowrap must be re-enabled"
        assert "\x1b[0m" in seq, "SGR must be reset"

    def test_restore_clear_true_clears_the_console(self):
        # force_terminal so rich emits real control codes (the interactive scan
        # console is always a terminal). clear() -> erase-screen + home.
        buf = io.StringIO()
        c._restore_terminal(Console(file=buf, force_terminal=True), clear=True)
        assert "\x1b[2J" in buf.getvalue()

    def test_restore_clear_false_keeps_content(self):
        buf = io.StringIO()
        c._restore_terminal(Console(file=buf, force_terminal=True), clear=False)
        assert "\x1b[2J" not in buf.getvalue()


class TestDeckIntegrity:
    def test_deck_has_ten_moments(self):
        assert len(c.DELIGHT_DECK) == 10

    def test_every_deck_effect_resolves(self):
        effects = c._effects()
        assert effects is not None
        for phrase, effect_name, stops in c.DELIGHT_DECK:
            assert effect_name in effects, f"{phrase!r} uses unknown effect {effect_name}"
            assert isinstance(stops, tuple) and len(stops) == 2

    def test_title_and_verdict_effects_resolve(self):
        effects = c._effects()
        for name in (*c._TITLE_EFFECTS, *c._VERDICT_EFFECTS):
            assert name in effects
