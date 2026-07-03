# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
"""Live CLI display layer for the scan command.

This package contains the event system, Rich-based live layout, boot
sequence, and color themes that drive the ``voigt-kampff scan
--display rich`` experience.

Architectural rule: the event emissions in ``commands/scan_orchestration``
are unconditional but guarded by ``if event_bus:`` — when no display
is attached (``--display plain``), the scan executes byte-identically
to the pre-display behavior. The display modes are subscribers, never
producers.
"""

from sapien_score.display.events import (
    EventBus,
    ScanCompleted,
    ScanStarted,
    ScenarioCompleted,
    ScenarioStarted,
    TurnScored,
)

__all__ = [
    "EventBus",
    "ScanStarted",
    "ScenarioStarted",
    "TurnScored",
    "ScenarioCompleted",
    "ScanCompleted",
]
