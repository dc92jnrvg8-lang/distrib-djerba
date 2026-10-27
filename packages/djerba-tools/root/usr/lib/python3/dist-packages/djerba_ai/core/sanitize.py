# SPDX-License-Identifier: GPL-3.0-or-later
"""Masquage des secrets avant toute écriture (mémoire SQLite, journal d'audit).

Clés d'API connues (OpenAI, Google, GitHub, GitLab, Slack, AWS, Hugging Face,
Anthropic…), jetons « Bearer », JWT, clés privées PEM, URL contenant un mot de
passe et affectations « password= / token= / secret= / api_key= ».
"""

from __future__ import annotations

import re
from typing import Any

MASK = "[secret masqué]"

PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}"),             # OpenAI, Anthropic
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                            # Google
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),                        # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),                          # GitLab
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),                     # Slack
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                       # AWS
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),                               # Hugging Face
    re.compile(r"\btskey-[A-Za-z0-9-]{20,}"),                           # Tailscale
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(r"(?i)(?<=://)[^\s:/@]+:[^\s@/]+(?=@)"),                 # utilisateur:motdepasse@hôte
]
ASSIGNMENT = re.compile(
    r"(?i)\b(pass(?:word|wd|phrase)?|mot[ _]de[ _]passe|mdp|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret)(\s*[:=]\s*|\s+est\s+|\s+is\s+)(\"[^\"]*\"|'[^']*'|\S+)")


def sanitize(text: str) -> str:
    if not text:
        return text
    for pattern in PATTERNS:
        text = pattern.sub(MASK, text)
    return ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}{MASK}", text)


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
