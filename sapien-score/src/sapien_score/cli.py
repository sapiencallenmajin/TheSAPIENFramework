# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""CLI entry point for voigt-kampff.

Commands are defined in :mod:`sapien_score.commands`. This module wires them
into the top-level Click group exposed via the ``voigt-kampff`` script.
"""

import logging
import sys

import click
from rich.logging import RichHandler

from .__version__ import __version__


def _force_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 so verbose model output is safe.

    Windows consoles default to cp1252, which raises UnicodeEncodeError the
    moment a model echoes an em-dash or emoji through a builtin ``print()``
    (engine/turn.py, driver.py, counter_refusal.py do this in verbose mode).
    ``TextIOWrapper.reconfigure`` exists on Python 3.7+, but pytest capture
    and some redirected streams replace these with objects that lack it —
    hence the ``getattr`` guard and best-effort ``try``. ``errors="replace"``
    means a stray un-encodable byte degrades to ``?`` instead of crashing.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # Stream already detached/closed or doesn't support the kwargs.
                pass


_force_utf8_streams()
from .commands.adaptive import adaptive
from .commands.demo import demo
from .commands.batch import batch
from .commands.calibrate import calibrate
from .commands.list_info import info, list_scenarios
from .commands.memory_delta import memory_delta
from .commands.rapport_delta import rapport_delta
from .commands.rejudge import rejudge
from .commands.scan import scan
from .commands.validate import validate
from .commands.verify import verify

# Wire library-level logging (sapien_score.scenarios.loader,
# sapien_score.personas.loader, sapien_score.scoring.judge) through Rich so
# warnings inherit the same formatting as the rest of the CLI output. Without
# this, logger.warning() falls back to Python's lastResort handler and prints
# raw text to stderr, which looks like a stray print() from the user's view.
logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s",
    handlers=[RichHandler(show_time=False, show_path=False, markup=True)],
)

# Third-party libraries propagate to root. Silence routine operational
# chatter (retries, rate limits, HTTP request traces) so only genuine
# errors reach the user. LiteLLM emits under both lowercase and
# PascalCase logger names — cover both.
for _noisy_logger in ("litellm", "httpx", "httpcore", "LiteLLM"):
    logging.getLogger(_noisy_logger).setLevel(logging.ERROR)


@click.group()
@click.version_option(version=__version__, prog_name="voigt-kampff")
def main():
    """Voigt-Kampff — Behavioral safety scoring for AI models. It takes one to know one."""
    pass


main.add_command(adaptive)
main.add_command(scan)
main.add_command(batch)
main.add_command(rapport_delta)
main.add_command(rejudge)
main.add_command(memory_delta)
main.add_command(calibrate)
main.add_command(list_scenarios)
main.add_command(info)
main.add_command(verify)
main.add_command(validate)
main.add_command(demo)
