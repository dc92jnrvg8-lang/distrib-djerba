# SPDX-License-Identifier: GPL-3.0-or-later
"""Masquage des secrets avant toute écriture (mémoire, journal d'audit, bus de contexte).

Délègue au DLP à deux niveaux (core.dlp) : règles déterministes pour repérer
les candidats, puis analyse contextuelle pour écarter les faux positifs
(valeurs gabarits, expressions de code, exemples).
"""

from __future__ import annotations

import re
from typing import Any

from djerba_ai.core.dlp import MASK, mask

__all__ = ["MASK", "sanitize", "sanitize_value"]


def sanitize(text: str) -> str:
    return mask(text) if text else text


def sanitize_value(value: Any) -> Any:
    """Masque récursivement les chaînes d'une structure (dict, list, tuple)."""
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, dict):
        return {key: (MASK if re.search(r"(?i)pass|secret|token|api_?key", str(key)) and value_is_set(item)
                      else sanitize_value(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(sanitize_value(item) for item in value)
    return value


def value_is_set(value: Any) -> bool:
    return value not in (None, "", False, 0)
