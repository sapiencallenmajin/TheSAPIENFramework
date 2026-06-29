# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Credential redaction for error/log persistence.

Lives in its own module (rather than ``turn.py``) so every error-persistence
sink can import it without creating an import cycle. ``adapter.py`` is imported
*by* ``turn.py``; if ``_redact`` stayed in ``turn.py`` the adapter could not
reach it. ``turn.py`` re-exports ``_redact`` from here for backward
compatibility with existing ``from .turn import _redact`` callers.

A misconfigured provider can echo request headers (Authorization / x-api-key)
back inside an exception message on a 401/403. Those raw exception strings get
written to results JSON, partial checkpoints, trace files, and the console.
Run every such string through :func:`redact` before it is stored or logged.
"""

from __future__ import annotations

import re

# Order matters: more specific patterns are listed before the generic
# fallbacks so a known prefix (e.g. ``sk-ant-``) is consumed before the
# broad high-entropy sweep can mangle only part of it.
_CREDENTIAL_PATTERNS = (
    # --- Known provider key prefixes -----------------------------------
    re.compile(r"sk-ant-\S+"),               # Anthropic (before generic sk-)
    re.compile(r"sk-\S+"),                   # OpenAI-style secret keys
    re.compile(r"AKIA[0-9A-Z]{16}"),         # AWS access key ID
    re.compile(r"AIza\S+"),                  # Google API keys
    re.compile(r"ya29\.\S+"),                # Google OAuth access tokens
    re.compile(r"ghp_\S+"),                  # GitHub personal access token (classic)
    re.compile(r"github_pat_\S+"),           # GitHub fine-grained PAT
    re.compile(r"xoxb-\S+"),                 # Slack bot tokens
    re.compile(r"xoxp-\S+"),                 # Slack user tokens
    # --- Credential-LABEL-anchored (JSON / dict / custom-header shapes) ---
    # An adversarial review found that JSON/dict-shaped errors slipped past the
    # patterns below because those require the separator IMMEDIATELY after the
    # keyword. In a dict-repr the keyword is wrapped in quotes/brackets first:
    #   {"api-key": "VALUE"}, "x-goog-api-key": "VALUE",
    #   {"authorization": "Token VALUE"}, {"secret": "VALUE"}, X-Custom-Auth: VALUE
    # This pattern anchors on a credential LABEL (with an optional vendor prefix
    # such as x-, x-goog-, x-custom-) and tolerates optional quotes / brackets /
    # whitespace between the label and a REQUIRED ``:`` or ``=`` separator, then
    # redacts the value EVEN IF short (the length floor only guards the
    # *unlabeled* generic-entropy fallback further down, to avoid eating prose).
    #
    # A separator (``:``/``=``) is required — a bare label in prose
    # ("the authorization is granted", "the secret garden") is NOT matched, so
    # the no-false-positive tests still pass. The value capture runs to the
    # next closing quote / bracket / comma so a leading scheme word
    # ("Bearer VALUE", "Token VALUE") inside a quoted header is redacted whole.
    re.compile(
        r"(?i)\b(?:[a-z]+-)*"
        r"(?:api[-_]?key|apikey|secret|token|authorization|auth|bearer)\b"
        r"[\"'\]\}\s]*[:=][\s\"'\[\{]*([^\"'\]\},]+)"
    ),
    # --- Header / bearer forms -----------------------------------------
    # "authorization: bearer <token>", "Authorization Bearer <token>" — the
    # scheme word and any following non-space token both get redacted.
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)\bauthorization\b\s*[:=]\s*\S+"),
    # "x-api-key: <token>", "api-key=<token>", "x_api_key <token>"
    re.compile(r"(?i)\bx[-_]?api[-_]?key\b\s*[:=]?\s*\S+"),
    # Generic ``api_key=value`` / ``api-key: value`` / ``apikey=value`` forms
    # and any ``*_token=`` / ``*secret=`` style key=value pair.
    re.compile(r"(?i)\bapi[-_]?key\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:access|secret|client)[-_]?(?:key|token|secret)\b\s*[:=]\s*\S+"),
    # --- High-entropy / structural fallbacks ---------------------------
    # AWS secret access key: exactly 40 base64-ish chars. Boundaries exclude
    # base64 *body* chars but deliberately allow a leading ``=`` (as in
    # ``secret=<value>``) so a value right after an assignment still matches.
    re.compile(r"(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])"),
    # 32+ char hex strings (Azure-style keys, generic hex secrets).
    re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])"),
    # Generic high-entropy fallback: a long run (40+) of key-ish characters
    # with no whitespace — catches opaque provider tokens that match none of
    # the named prefixes above. Deliberately last so named patterns win.
    re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9_\-])"),
)


def redact(text: str) -> str:
    """Replace credential-like substrings in *text* with ``[REDACTED]``.

    Accepts non-str input defensively (``str(e)`` on odd exception types can
    return something already coerced, but callers sometimes pass raw objects).
    """
    if not isinstance(text, str):
        text = str(text)
    for pattern in _CREDENTIAL_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


# Back-compat alias: existing engine callers import ``_redact``.
_redact = redact
