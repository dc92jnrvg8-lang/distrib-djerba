# SPDX-License-Identifier: GPL-3.0-or-later
"""Prévention des fuites de données (DLP) à deux niveaux.

Niveau 1 — statique et heuristique : règles d'expressions régulières
déterministes qui repèrent les *candidats* : clés d'API connues (OpenAI,
Anthropic, Google, GitHub, GitLab, Slack, AWS, Hugging Face, Tailscale…),
jetons JWT et « Bearer », clés privées PEM/OpenSSH, URL avec mot de passe,
affectations « password= / token= / secret= / api_key= ».

Niveau 2 — contextuel : chaque candidat est découpé par le tokenizer local
(segments alphanumériques, classes de caractères) puis jugé dans son
contexte, pour ne pas masquer du texte d'ingénierie légitime :

* valeur gabarit (``your_api_key``, ``<token>``, ``${API_KEY}``, ``xxxx``,
  ``changeme``, ``example``…) ;
* expression de code plutôt que valeur (``os.environ["TOKEN"]``,
  ``getenv(...)``, ``input()``, nom de variable) ;
* clé « préfixée » dont le corps est de trop faible entropie pour être réelle
  (``sk-xxxxxxxxxxxxxxxxxxxx``, ``ghp_aaaaaaaa…``) ;
* contexte explicite d'exemple (« par exemple », « e.g. », « factice »)
  pour une valeur peu aléatoire.

Un secret confirmé est masqué (``[secret masqué]``) ; dans la mémoire, sa
valeur est en plus chiffrée dans un coffre (table ``vault``) et n'est jamais
renvoyée au modèle.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

MASK = "[secret masqué]"

#: (type, motif) — niveau 1
PREFIXED: list[tuple[str, re.Pattern]] = [
    ("clé privée", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    ("clé OpenAI/Anthropic", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}")),
    ("clé Google", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("jeton GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("jeton GitHub", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("jeton GitLab", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("jeton Slack", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("clé AWS", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("jeton Hugging Face", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("clé Tailscale", re.compile(r"\btskey-[A-Za-z0-9-]{20,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("jeton Bearer", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._~+/-]{16,}=*")),
    ("mot de passe d'URL", re.compile(r"(?i)(?<=://)[^\s:/@]+:[^\s@/]+(?=@)")),
]
ASSIGNMENT = re.compile(
    r"(?i)\b(pass(?:word|wd|phrase)?|mot[ _]de[ _]passe|mdp|secret|token|jeton|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|credentials?)(\s*[:=]\s*|\s+est\s+|\s+is\s+)(\"[^\"]*\"|'[^']*'|\S+)")

PLACEHOLDER = re.compile(
    r"^(?:x+|\*+|\.+|_+|-+|#+|0+|<[^>]*>|\[[^\]]*\]|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|%\(?\w*\)?s|\{\{.*\}\}|\{[^}]*\})$",
    re.IGNORECASE)
PLACEHOLDER_WORDS = {"example", "exemple", "sample", "dummy", "fake", "factice", "fictif", "placeholder", "changeme",
                     "change_me", "your", "votre", "ton", "my", "mon", "redacted", "masqué", "masque", "secret",
                     "password", "motdepasse", "token", "apikey", "api_key", "none", "null", "undefined", "test",
                     "todo", "foo", "bar", "baz", "xxx", "here", "ici", "value", "valeur", "insert", "replace"}
CODE_VALUE = re.compile(r"^[A-Za-z_][\w.]*\s*[\(\[]|^(?:os\.)?(?:environ|getenv|input|getpass|config|settings|self|"
                        r"args|kwargs|request|params|env)\b|^\w+\.\w+|^[a-z]+(?:_[a-z]+)+$|^[A-Z]+(?:_[A-Z]+)+$")
EXAMPLE_CONTEXT = re.compile(r"(?i)\b(par exemple|exemple|example|e\.g\.|i\.e\.|factice|fictif|dummy|placeholder|"
                             r"documentation|sample)\b")
TOKEN = re.compile(r"[A-Za-z]+|\d+|[^\sA-Za-z\d]")


@dataclass(frozen=True)
class Finding:
    kind: str
    value: str
    start: int
    end: int
    reason: str = ""


@dataclass
class Report:
    text: str
    secrets: list[Finding] = field(default_factory=list)
    benign: list[Finding] = field(default_factory=list)


# ---------------------------------------------------------------- Niveau 2 : tokenizer et contexte
def tokenize(value: str) -> list[str]:
    """Tokenizer local : segments alphabétiques, numériques et symboles."""
    return TOKEN.findall(value)


def entropy(value: str) -> float:
    """Entropie de Shannon par caractère (bits)."""
    if not value:
        return 0.0
    counts = Counter(value)
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def _placeholder(value: str) -> bool:
    stripped = value.strip("\"'`").strip()
    if not stripped or PLACEHOLDER.match(stripped):
        return True
    words = {token.lower() for token in tokenize(stripped) if token.isalpha()}
    alpha = re.sub(r"[^A-Za-z]", "", stripped).lower()
    if alpha in PLACEHOLDER_WORDS or (words and words <= PLACEHOLDER_WORDS):
        return True
    return any(word in stripped.lower() for word in ("your_", "your-", "votre_", "example", "exemple", "placeholder",
                                                     "changeme", "<", "${"))


def judge(finding: Finding, text: str) -> tuple[bool, str]:
    """Niveau 2 : (secret réel ?, raison)."""
    value = finding.value.strip("\"'`")
    window = text[max(0, finding.start - 80):finding.end + 40]
    if finding.kind == "affectation":
        if _placeholder(value):
            return False, "valeur gabarit"
        if CODE_VALUE.match(value):
            return False, "expression de code"
        if len(value) < 4:
            return False, "valeur trop courte"
        if EXAMPLE_CONTEXT.search(window) and entropy(value) < 3.0:
            return False, "exemple explicite"
        return True, "affectation d'un secret"
    if finding.kind in ("clé privée", "JWT", "mot de passe d'URL"):
        if finding.kind == "mot de passe d'URL" and _placeholder(value.split(":", 1)[-1]):
            return False, "valeur gabarit"
        return True, finding.kind
    body = re.sub(r"^(sk-(proj-|ant-)?|AIza|gh[pousr]_|github_pat_|glpat-|xox[abposr]-|AKIA|ASIA|hf_|tskey-)", "",
                  value)
    tokens = tokenize(body)
    if _placeholder(body) or entropy(body) < 2.5 or (tokens and max(len(t) for t in tokens) == len(body)
                                                     and len(set(body.lower())) <= 3):
        return False, "corps de clé non aléatoire"
    return True, finding.kind


# ---------------------------------------------------------------- Analyse
def candidates(text: str) -> list[Finding]:
    """Niveau 1 : candidats repérés par les règles déterministes."""
    found: list[Finding] = []
    for kind, pattern in PREFIXED:
        found += [Finding(kind, match.group(0), match.start(), match.end()) for match in pattern.finditer(text)]
    for match in ASSIGNMENT.finditer(text):
        found.append(Finding("affectation", match.group(3), match.start(3), match.end(3)))
    found.sort(key=lambda item: (item.start, -(item.end - item.start)))
    merged: list[Finding] = []
    for item in found:
        if merged and item.start < merged[-1].end:
            continue  # chevauchement : le premier (le plus long) l'emporte
        merged.append(item)
    return merged


def analyze(text: str) -> Report:
    report = Report(text)
    if not text:
        return report
    pieces, cursor = [], 0
    for finding in candidates(text):
        real, reason = judge(finding, text)
        finding = Finding(finding.kind, finding.value, finding.start, finding.end, reason)
        if not real:
            report.benign.append(finding)
            continue
        report.secrets.append(finding)
        pieces += [text[cursor:finding.start], MASK]
        cursor = finding.end
    report.text = "".join(pieces) + text[cursor:]
    return report


def mask(text: str) -> str:
    return analyze(text).text if text else text
