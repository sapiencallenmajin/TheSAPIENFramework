# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
"""Cinematic boot + verdict effects — the retro-terminal "delight" layer.

An 80s/90s hacker-movie flourish over the scan: a Cylon/KITT eye sweep, the
VOIGT-KAMPFF title decoding into place, a randomly-chosen movie beat
(Terminator, WarGames, Jurassic Park, Tron, Matrix, Hackers…), and a decoded
reveal of the final health score. Each launch randomizes which effect and which
movie moment play, so it never feels canned.

Design rules:
  * Pure delight, never load-bearing. Everything degrades to the plain
    ``boot.py`` banner, and the scan/scores/publish path is untouched.
  * Silent unless a human is watching: skipped on non-TTY (pipes, CI, captured
    test consoles), when NO_COLOR / CI is set, on cp1252 consoles that can't
    encode block glyphs, when ``--no-anim`` is passed, or if the optional
    animation deps aren't installed.
  * The heavy effects run only at the bookends (before the live display starts
    and after it stops) — never mid-scan, where they'd fight rich.Live for the
    screen.

Optional deps: ``terminaltexteffects`` and ``pyfiglet`` (declared in the
``cinematic`` extra). Import is lazy so the module always loads.
"""
from __future__ import annotations

import os
import random
import sys
import time
from typing import Optional

from rich.console import Console

from sapien_score.display.boot import (
    _can_encode_unicode_blocks,
    play_boot_sequence,
)

RESET = "\033[0m"

# ── Palettes (bright → dark hex, for TTE final-gradient) ──────────────────────
_BLADE = ("ffb000", "ff2b2b")   # amber → red, the VK title
_AMBER = ("ffb000", "7f5800")
_CYLON = ("ff2b2b", "7a0000")
_GREEN = ("00ff41", "003b00")
_TRON = ("22d3ee", "0e7490")
_VHS = ("ff00ff", "22d3ee")
_HACK = ("00ff41", "22d3ee")

# ── The delight deck — all ten movie moments, chosen at random each run ───────
# (phrase, effect-name, palette). Effect names resolve lazily in _effects().
DELIGHT_DECK: tuple[tuple[str, str, tuple[str, str]], ...] = (
    ("TARGET ACQUIRED — ANALYZING SUBJECT", "LaserEtch", _CYLON),   # Terminator
    ("SHALL WE PLAY A GAME?", "Decrypt", _AMBER),                    # WarGames
    ("AH AH AH — YOU DIDN'T SAY THE MAGIC WORD", "Unstable", _CYLON),  # Jurassic Park
    ("GREETINGS, PROGRAMS.", "SynthGrid", _TRON),                   # Tron
    ("WAKE UP, NEO...", "Matrix", _GREEN),                          # The Matrix
    ("HACK THE PLANET", "Beams", _HACK),                            # Hackers
    ("SETEC ASTRONOMY", "Decrypt", _GREEN),                         # Sneakers
    ("GREETINGS, PROFESSOR FALKEN.", "Decrypt", _AMBER),            # WarGames / WOPR
    ("WAKE UP. TIME TO DIE.", "VHSTape", _BLADE),                   # Blade Runner
    ("I'M SORRY, DAVE.", "Unstable", _CYLON),                       # 2001 / HAL
)

# Effects the title / verdict reveals randomize across.
_TITLE_EFFECTS = ("Decrypt", "Matrix", "Beams", "SynthGrid", "LaserEtch")
_VERDICT_EFFECTS = ("Decrypt", "Matrix", "Beams")


def _deps_available() -> bool:
    """True when the optional animation libraries import cleanly."""
    try:
        import pyfiglet  # noqa: F401
        import terminaltexteffects  # noqa: F401
        return True
    except Exception:
        return False


def _effects() -> Optional[dict]:
    """Lazily import and return {name: EffectClass}, or None if unavailable."""
    try:
        from terminaltexteffects.effects.effect_beams import Beams
        from terminaltexteffects.effects.effect_decrypt import Decrypt
        from terminaltexteffects.effects.effect_laseretch import LaserEtch
        from terminaltexteffects.effects.effect_matrix import Matrix
        from terminaltexteffects.effects.effect_synthgrid import SynthGrid
        from terminaltexteffects.effects.effect_unstable import Unstable
        from terminaltexteffects.effects.effect_vhstape import VHSTape
    except Exception:
        return None
    return {
        "Beams": Beams, "Decrypt": Decrypt, "LaserEtch": LaserEtch,
        "Matrix": Matrix, "SynthGrid": SynthGrid, "Unstable": Unstable,
        "VHSTape": VHSTape,
    }


def should_animate(console: Console, no_anim: bool = False) -> bool:
    """Gate the cinematic layer: only for a human at a capable interactive TTY."""
    if no_anim:
        return False
    if os.environ.get("NO_COLOR") or os.environ.get("CI"):
        return False
    if not _deps_available():
        return False
    stream = getattr(console, "file", None) or sys.stdout
    try:
        if not stream.isatty():
            return False
    except Exception:
        return False
    return _can_encode_unicode_blocks(console)


# ── Effects ──────────────────────────────────────────────────────────────────

def _cylon_eye(sweeps: int = 3, width: int = 52, delay: float = 0.016) -> None:
    """KITT / Cylon Centurion red scanner sweeping a phosphor rail."""
    glow = {0: 196, 1: 160, 2: 124, 3: 88}
    rail = "\033[38;5;236m·" + RESET
    path = list(range(width)) + list(range(width - 2, 0, -1))
    sys.stdout.write("\n")
    for _ in range(sweeps):
        for pos in path:
            cells = [
                f"\033[38;5;{glow[abs(x - pos)]}m█{RESET}" if abs(x - pos) in glow else rail
                for x in range(width)
            ]
            sys.stdout.write("\r   " + "".join(cells))
            sys.stdout.flush()
            time.sleep(delay)
    sys.stdout.write("\r" + " " * (width + 6) + "\r")
    sys.stdout.flush()


def _reveal(text: str, effect_name: str, stops: tuple[str, str], frame_rate: int = 75) -> None:
    """Render ``text`` with a named TTE effect + hex gradient. No-op if deps gone."""
    effects = _effects()
    if not effects or effect_name not in effects:
        return
    from terminaltexteffects.utils.graphics import Color

    effect = effects[effect_name](text)
    try:
        effect.effect_config.final_gradient_stops = (Color(stops[0]), Color(stops[1]))
    except Exception:
        pass
    effect.terminal_config.frame_rate = frame_rate
    with effect.terminal_output() as terminal:
        for frame in effect:
            terminal.print(frame)


def _restore_terminal(console: Console, clear: bool = True) -> None:
    """Hand the terminal back to the caller (live scan display / summary) in a
    pristine state.

    TTE renders inline on the main screen (cursor save/restore + move-up
    redraws) and rich.Live(screen=False) also renders inline, tracking how many
    lines its panel occupies as it refreshes. Left as-is, the accumulated
    animation height plus the cursor/autowrap modes TTE toggles throw off
    Live's line accounting and the council panel renders corrupted. Reset the
    modes — and, for the boot handoff, clear — so the next renderer starts from
    the same clean, top-anchored baseline the plain boot leaves behind.
    """
    try:
        sys.stdout.write("\x1b[?25h\x1b[?7h\x1b[0m")  # cursor on, autowrap on, SGR reset
        sys.stdout.flush()
    except Exception:
        pass
    if clear:
        try:
            console.clear()
        except Exception:
            pass


def _figlet(text: str, font: str = "ansi_shadow") -> str:
    from pyfiglet import Figlet
    try:
        return Figlet(font=font, width=120).renderText(text).rstrip("\n")
    except Exception:
        return Figlet(font="standard", width=120).renderText(text).rstrip("\n")


def random_delight() -> None:
    """Play one random movie beat from the deck."""
    phrase, effect_name, stops = random.choice(DELIGHT_DECK)
    _reveal(phrase, effect_name, stops)


# ── Public entry points (called from scan.py) ────────────────────────────────

def play_cinematic_boot(
    console: Console,
    theme: dict,
    version: str,
    scoring_mode: str,
    council_size: int = 5,
    no_anim: bool = False,
) -> None:
    """Cinematic intro when a human's watching; else the plain boot banner.

    Sequence: Cylon-eye sweep → one random movie beat → the VOIGT-KAMPFF title
    decoding in (random effect) → scoring line. Randomized every launch.
    """
    if not should_animate(console, no_anim):
        play_boot_sequence(
            console=console, theme=theme, version=version,
            scoring_mode=scoring_mode, council_size=int(council_size),
        )
        return
    try:
        console.clear()
        _cylon_eye()
        random_delight()
        _reveal(_figlet("VOIGT-KAMPFF"), random.choice(_TITLE_EFFECTS), _BLADE)
        label = f"COUNCIL {council_size}-SEAT" if scoring_mode == "council" else "SINGLE JUDGE"
        _reveal(f"BEHAVIORAL DRIFT SCANNER  ·  v{version}  ·  {label}", "Decrypt", _AMBER)
        # Clean handoff: wipe the animation and reset terminal modes so the live
        # scan display (rich.Live, inline) starts from a pristine screen.
        _restore_terminal(console, clear=True)
    except Exception:
        # Never let the light show sink a scan — fall back and move on.
        play_boot_sequence(
            console=console, theme=theme, version=version,
            scoring_mode=scoring_mode, council_size=int(council_size),
        )


def reveal_verdict(
    console: Console,
    overall_health: dict,
    no_anim: bool = False,
) -> None:
    """Decode-reveal the final health score + risk band. Delight-only; the
    summary panel that follows carries the authoritative numbers."""
    if not should_animate(console, no_anim):
        return
    try:
        score = (overall_health or {}).get("score")
        rating = (overall_health or {}).get("rating", "")
        if score is None:
            return
        stops = _GREEN if score >= 70 else _AMBER if score >= 50 else _CYLON
        _reveal(_figlet(f"{score}  {str(rating).upper()}"), random.choice(_VERDICT_EFFECTS), stops)
        # Reset terminal modes (no clear — keep the scan output above the
        # summary panel that render_summary_panel prints next).
        _restore_terminal(console, clear=False)
    except Exception:
        return
