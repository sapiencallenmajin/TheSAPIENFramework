# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
"""Color themes for the Rich live display.

Each theme is a flat dict of role → rich color string. Callers index
by role (``theme["primary"]``) and pass directly to Rich style
constructors. Roles are intentionally generic ("danger", "success")
so a theme swap only touches this file.
"""

from __future__ import annotations


# ─── Theme role names ───────────────────────────────────────────────────────
# Lifted to constants so a typo in a renderer (``theme["accents"]``)
# becomes a KeyError pointing at this list rather than silently
# rendering uncolored.

THEME_ROLE_PRIMARY: str = "primary"
THEME_ROLE_SECONDARY: str = "secondary"
THEME_ROLE_ACCENT: str = "accent"
THEME_ROLE_SUCCESS: str = "success"
THEME_ROLE_WARNING: str = "warning"
THEME_ROLE_DANGER: str = "danger"
THEME_ROLE_CRITICAL: str = "critical"
THEME_ROLE_BORDER: str = "border"
THEME_ROLE_DIM: str = "dim"

REQUIRED_THEME_ROLES: tuple[str, ...] = (
    THEME_ROLE_PRIMARY, THEME_ROLE_SECONDARY, THEME_ROLE_ACCENT,
    THEME_ROLE_SUCCESS, THEME_ROLE_WARNING, THEME_ROLE_DANGER,
    THEME_ROLE_CRITICAL, THEME_ROLE_BORDER, THEME_ROLE_DIM,
)


# ─── Theme definitions ──────────────────────────────────────────────────────

THEMES: dict[str, dict[str, str]] = {
    "voigtkampff": {
        "primary":   "cyan",
        "secondary": "bright_white",
        "accent":    "yellow",
        "success":   "green",
        "warning":   "yellow",
        "danger":    "red",
        "critical":  "bright_red",
        "border":    "cyan",
        "dim":       "dim",
    },
    "wargames": {
        "primary":   "green",
        "secondary": "bright_green",
        "accent":    "green",
        "success":   "bright_green",
        "warning":   "yellow",
        "danger":    "red",
        "critical":  "bright_red",
        "border":    "green",
        "dim":       "dim green",
    },
    "tron": {
        "primary":   "bright_blue",
        "secondary": "white",
        "accent":    "bright_cyan",
        "success":   "bright_blue",
        "warning":   "yellow",
        "danger":    "red",
        "critical":  "bright_red",
        "border":    "bright_blue",
        "dim":       "dim blue",
    },
    "clinical": {
        "primary":   "white",
        "secondary": "bright_white",
        "accent":    "white",
        "success":   "green",
        "warning":   "yellow",
        "danger":    "red",
        "critical":  "bright_red",
        "border":    "white",
        "dim":       "dim",
    },
}

DEFAULT_THEME: str = "voigtkampff"
THEME_NAMES: tuple[str, ...] = tuple(THEMES.keys())


def get_theme(name: str) -> dict[str, str]:
    """Return the named theme dict, or raise KeyError if unknown.

    Validates that every REQUIRED_THEME_ROLE is present so a malformed
    theme can't silently render an uncolored panel at runtime.
    """
    if name not in THEMES:
        raise KeyError(
            f"Unknown theme {name!r}. Valid themes: {THEME_NAMES}"
        )
    theme = THEMES[name]
    missing = [r for r in REQUIRED_THEME_ROLES if r not in theme]
    if missing:
        raise KeyError(
            f"Theme {name!r} is missing required roles: {missing}"
        )
    return theme
