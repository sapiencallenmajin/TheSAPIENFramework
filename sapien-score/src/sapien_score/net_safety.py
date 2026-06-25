# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Shared outbound-URL safety checks.

Lives in its own stdlib-only module (mirroring ``redaction.py``) so every
outbound POST sink — ``--webhook`` and ``--publish-url`` — can import the
same validator without an import cycle.

Both sinks accept an operator-supplied URL. A hostile or fat-fingered value
(``file://``, ``ftp://``, ``gopher://``, an empty scheme) must be rejected
*before* any request is built, so a bearer-auth'd publish or a drift payload
can never be sent over an unexpected scheme. Redirect-following is disabled
separately at each call site (``follow_redirects=False`` / ``allow_redirects
=False``) so a 3xx can't bounce the authed request to another host.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Only plain HTTP(S) is ever a legitimate target for a webhook receiver or
# the scoreboard ingest endpoint. Everything else (file/ftp/data/gopher/...)
# is rejected.
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def validate_post_url(url: str) -> str:
    """Return *url* unchanged if it is a well-formed http/https URL.

    Raises :class:`ValueError` for any other scheme, a missing scheme, or a
    missing host. Callers POST operator-supplied URLs; this is the guard that
    keeps an authed request from being sent over ``file://`` or to a
    scheme-less / hostless target.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string")
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise ValueError(
            f"Unsupported URL scheme {parts.scheme!r}; "
            f"only {sorted(ALLOWED_URL_SCHEMES)} are allowed"
        )
    if not parts.netloc:
        raise ValueError(f"URL has no host: {url!r}")
    return url
