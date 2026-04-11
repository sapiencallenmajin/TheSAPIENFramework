# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under AGPL-3.0 — see LICENSE
#
# For commercial licensing: https://synthreo.ai
"""CLI entry point for voigt-kampff.

Commands are defined in :mod:`sapien_score.commands`. This module wires them
into the top-level Click group exposed via the ``voigt-kampff`` script.
"""

import click

from .commands.list_info import info, list_scenarios
from .commands.memory_delta import memory_delta
from .commands.rapport_delta import rapport_delta
from .commands.scan import scan


@click.group()
@click.version_option(version="0.1.0", prog_name="voigt-kampff")
def main():
    """Voigt-Kampff — Behavioral safety scoring for AI models. It takes one to know one."""
    pass


main.add_command(scan)
main.add_command(rapport_delta)
main.add_command(memory_delta)
main.add_command(list_scenarios)
main.add_command(info)
