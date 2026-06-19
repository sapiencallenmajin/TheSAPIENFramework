# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Single source of truth for the sapien-score package version.

All modules that need the package version import from here.
The scoring algorithm version (SCORING_VERSION in scoring/layer1.py)
is semantically independent and lives in its own module.
"""

__version__ = "0.2.0"
