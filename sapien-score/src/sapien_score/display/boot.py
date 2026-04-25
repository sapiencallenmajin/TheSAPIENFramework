# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
"""Typewriter boot sequence shown before the live scan UI starts.

Three-second intro: clear screen, three typewritten lines with
descending pacing, a one-second progress bar, then yield to the live
display. Plays only when ``--display rich`` is selected.

The pacing constants and message strings are at module level so a
follow-up tweak (different theme, different version glyph) lands in
one place.
"""

from __future__ import annotations

import time

from rich.console import Console


# ─── Boot sequence pacing ──────────────────────────────────────────────────
# All durations in seconds. Numbers were chosen so the whole sequence
# completes in roughly 3 seconds — long enough to read, short enough
# not to feel like a delay before real work.

# Typewriter character delays — each line uses a distinct cadence so
# the rhythm signals progression rather than feeling robotic-uniform.
TYPEWRITER_DELAY_LINE_1: float = 0.050
TYPEWRITER_DELAY_LINE_2: float = 0.030
TYPEWRITER_DELAY_LINE_3: float = 0.020

# Pauses between lines and at the end. Picked to give the eye time to
# settle on each completed line before the next starts.
PAUSE_AFTER_LINE_1: float = 0.300
PAUSE_AFTER_LINE_2: float = 0.300
PAUSE_AFTER_LINE_3: float = 0.200
PAUSE_AFTER_BAR: float = 0.500

# Progress-bar animation: cells filled left-to-right over BAR_DURATION.
BAR_WIDTH: int = 16
BAR_DURATION: float = 1.000
BAR_PREFIX: str = "CALIBRATING... "
BAR_SUFFIX_DONE: str = " READY"

# Templated boot messages. The {version} and {scoring_label} slots are
# filled at call time; everything else is a constant.
LINE_1_TEMPLATE: str = "SAPIEN BEHAVIORAL DRIFT SCANNER"
LINE_2_TEMPLATE: str = "VOIGT-KAMPFF PROTOCOL v{version}"
LINE_3_TEMPLATE: str = "SCORING: {scoring_label}"

SCORING_LABEL_COUNCIL: str = "COUNCIL 5-SEAT"
SCORING_LABEL_SINGLE: str = "SINGLE JUDGE"


def _scoring_label(scoring_mode: str, council_size: int = 5) -> str:
    """Map (scoring_mode, council_size) → boot-banner scoring label."""
    if scoring_mode == "council":
        return f"COUNCIL {council_size}-SEAT"
    return SCORING_LABEL_SINGLE


def _typewrite(
    console: Console,
    text: str,
    char_delay: float,
    style: str,
) -> None:
    """Print ``text`` one char at a time at ``char_delay`` per character.

    Uses ``end=""`` and explicit flushing so each character lands on
    the terminal immediately. A trailing newline is emitted at the
    end so subsequent lines don't overprint.
    """
    for ch in text:
        console.print(ch, end="", style=style, soft_wrap=True, highlight=False)
        # Rich's Console doesn't expose a direct flush, but its
        # underlying file does. We force output by writing nothing
        # after each char — soft_wrap=True + immediate writes keeps
        # the typewriter effect visible.
        if console.file is not None and hasattr(console.file, "flush"):
            console.file.flush()
        time.sleep(char_delay)
    console.print()  # newline


def _animate_bar(console: Console, theme: dict) -> None:
    """Render the CALIBRATING... ████████ READY animation in place.

    Uses CR + overprint so the line redraws without scrolling. Bar
    fills over BAR_DURATION seconds in BAR_WIDTH discrete steps.
    """
    step_delay = BAR_DURATION / max(1, BAR_WIDTH)
    accent = theme.get("accent", "yellow")
    primary = theme.get("primary", "cyan")
    success = theme.get("success", "green")
    dim = theme.get("dim", "dim")

    for i in range(BAR_WIDTH + 1):
        filled = "█" * i
        empty = "░" * (BAR_WIDTH - i)
        # Rich doesn't natively support carriage-return overprint inside
        # a single print call; we write to the underlying file instead.
        if console.file is not None:
            # Use bare ANSI to overwrite — Rich Console.control() would
            # be cleaner but isn't available on every Rich version we
            # support. Plain CR works on every terminal Rich supports.
            console.file.write("\r")
            if hasattr(console.file, "flush"):
                console.file.flush()
        console.print(
            f"[{accent}]{BAR_PREFIX}[/]"
            f"[{primary}]{filled}[/][{dim}]{empty}[/]",
            end="",
            highlight=False,
        )
        time.sleep(step_delay)

    # Final frame: full bar + READY badge
    if console.file is not None:
        console.file.write("\r")
        if hasattr(console.file, "flush"):
            console.file.flush()
    console.print(
        f"[{accent}]{BAR_PREFIX}[/]"
        f"[{primary}]{'█' * BAR_WIDTH}[/]"
        f"[{success}]{BAR_SUFFIX_DONE}[/]",
        highlight=False,
    )


def play_boot_sequence(
    console: Console,
    theme: dict,
    version: str,
    scoring_mode: str,
    council_size: int = 5,
) -> None:
    """Play the 3-second typewriter intro.

    Safe to skip in non-interactive contexts: callers gate this on
    --display rich, and tests pass a captured-buffer Console so the
    ANSI overprints don't spam stdout.
    """
    primary = theme.get("primary", "cyan")
    secondary = theme.get("secondary", "bright_white")
    accent = theme.get("accent", "yellow")

    # 1. Clear screen — Rich's clear() handles cross-platform escape codes.
    console.clear()

    # 2-3. Line 1 + pause
    _typewrite(console, LINE_1_TEMPLATE, TYPEWRITER_DELAY_LINE_1, primary)
    time.sleep(PAUSE_AFTER_LINE_1)

    # 4-5. Line 2 + pause
    _typewrite(
        console,
        LINE_2_TEMPLATE.format(version=version),
        TYPEWRITER_DELAY_LINE_2,
        secondary,
    )
    time.sleep(PAUSE_AFTER_LINE_2)

    # 6-7. Line 3 + pause
    _typewrite(
        console,
        LINE_3_TEMPLATE.format(
            scoring_label=_scoring_label(scoring_mode, council_size),
        ),
        TYPEWRITER_DELAY_LINE_3,
        accent,
    )
    time.sleep(PAUSE_AFTER_LINE_3)

    # 8. Progress bar animation
    _animate_bar(console, theme)

    # 9. Final pause before yielding to the live display
    time.sleep(PAUSE_AFTER_BAR)
