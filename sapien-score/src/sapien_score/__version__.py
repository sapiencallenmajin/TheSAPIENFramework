"""Single source of truth for the sapien-score package version.

All modules that need the package version import from here.
The scoring algorithm version (SCORING_VERSION in scoring/layer1.py)
is semantically independent and lives in its own module.
"""

__version__ = "0.1.0"
