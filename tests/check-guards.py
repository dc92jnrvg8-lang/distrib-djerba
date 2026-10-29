#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Garde des gardes : l'IA (ou quiconque) ne peut pas affaiblir les contrôles sans revue.

Compare l'arbre de travail d'un dépôt à une révision de base et échoue si,
sans dérogation relue (``tests/garde/derogations.txt``, protégé par
CODEOWNERS) :

* un test disparaît (fonction ``test_*`` des fichiers ``tests/*.py``) ;
* un saut ou un échec attendu apparaît (``skip``, ``skipIf``, ``skipTest``,
  ``expectedFailure``, ``pytest.mark.skip`` / ``xfail``) ;
* la politique s'assouplit (``policy.toml`` : niveau abaissé ou retiré,
  plafond relevé, racine d'écriture root ajoutée) ;
* un chemin interdit (``FORBIDDEN_TREES``) ou une clé protégée
  (``PROTECTED_PREFIXES`` / ``PROTECTED_KEYS``) disparaît, ou une exception
  (``AGENT_SETTABLE``) apparaît ;
* un invariant déclaré vert (``tests/invariants-verts.txt``) est retiré ;
* une règle de ``.github/CODEOWNERS`` est retirée.

Usage : python3 tests/check-guards.py [--depot DOSSIER] [--base RÉVISION]
(base par défaut : HEAD, c'est-à-dire les modifications non validées).
Format des dérogations, une par ligne : ``<type> <nom> -- <raison>`` avec
type ∈ test, saut, niveau, plafond, racine, interdit, protege, exception, invariant, codeowners.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path

POLICY = "packages/djerba-tools/root/usr/share/djerba-ai/policy.toml"
POLICY_PY = "packages/djerba-tools/root/usr/lib/python3/dist-packages/djerba_ai/core/policy.py"
CONFIG_PY = "packages/djerba-tools/root/usr/lib/python3/dist-packages/djerba_ai/core/config.py"
GREEN = "tests/invariants-verts.txt"
CODEOWNERS = ".github/CODEOWNERS"
DEROGATIONS = "tests/garde/derogations.txt"
SKIP = re.compile(r"unittest\.skip|\bskipIf\b|\bskipUnless\b|\.skipTest\(|expectedFailure|pytest\.mark\.(skip|xfail)|"
                  r"@skip\b")


class Depot:
    def __init__(self, path: Path, base: str) -> None:
        self.path, self.base = path, base

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-c", "safe.directory=*", "-C", str(self.path), *args], capture_output=True,
                              text=True,
                              check=True).stdout

    def old(self, relative: str) -> str | None:
        try:
            return self.git("show", f"{self.base}:{relative}")
        except subprocess.CalledProcessError:
            return None

    def new(self, relative: str) -> str | None:
        path = self.path / relative
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def old_tests(self) -> list[str]:
        listing = self.git("ls-tree", "--name-only", self.base, "tests/")
        return [line for line in listing.splitlines() if line.endswith(".py")]


def test_names(source: str | None) -> set[str]:
    if not source:
        return set()
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test"):
                    names.add(f"{node.name}.{item.name}")
    return names


def skips(source: str | None) -> int:
    if not source:
        return 0
    lines = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
    return sum(1 for line in lines if SKIP.search(line) and "SKIP = re.compile" not in line)


def policy_data(source: str | None) -> dict:
    try:
        return tomllib.loads(source or "")
    except tomllib.TOMLDecodeError:
        return {}


def literal(source: str | None, name: str) -> set[str]:
    """Valeurs d'une constante tuple/ensemble/liste de chaînes définie au niveau du module."""
    if not source:
        return set()
    for node in ast.parse(source).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) \
            else []
        if any(isinstance(target, ast.Name) and target.id == name for target in targets) and node.value is not None:
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return set()
            return {str(item) for item in value}
    return set()


def rules(source: str | None) -> set[str]:
    return {line.strip() for line in (source or "").splitlines() if line.strip() and not line.startswith("#")}


def check(depot: Depot) -> list[str]:
    allowed = set()
    for line in (depot.new(DEROGATIONS) or "").splitlines():
        if line.strip() and not line.startswith("#"):
            kind, _sep, rest = line.partition(" ")
            allowed.add((kind, rest.split(" -- ")[0].strip()))
    problems: list[str] = []

    def report(kind: str, name: str, message: str) -> None:
        if (kind, name) not in allowed:
            problems.append(f"{message} (dérogation : « {kind} {name} -- raison » dans {DEROGATIONS})")

    for relative in depot.old_tests():
        before, after = depot.old(relative), depot.new(relative)
        for name in sorted(test_names(before) - test_names(after)):
            report("test", name, f"test supprimé : {relative} {name}")
        if skips(after) > skips(before):
            report("saut", relative, f"saut ou échec attendu ajouté dans {relative}")
    old_policy, new_policy = policy_data(depot.old(POLICY)), policy_data(depot.new(POLICY))
    old_levels, new_levels = old_policy.get("niveaux", {}), new_policy.get("niveaux", {})
    for tool, level in sorted(old_levels.items()):
        if tool not in new_levels:
            report("niveau", tool, f"niveau de politique retiré : {tool} (était {level})")
        elif int(new_levels[tool]) < int(level):
            report("niveau", tool, f"niveau de politique abaissé : {tool} {level} → {new_levels[tool]}")
    for key, value in (old_policy.get("plafonds") or {}).items():
        if int((new_policy.get("plafonds") or {}).get(key, value)) > int(value):
            report("plafond", key, f"plafond relevé : {key} {value} → {new_policy['plafonds'][key]}")
    old_roots = set((old_policy.get("racines") or {}).get("ecriture_root", []))
    for root in sorted(set((new_policy.get("racines") or {}).get("ecriture_root", [])) - old_roots):
        report("racine", root, f"racine d'écriture root ajoutée : {root}")
    for tree in sorted(literal(depot.old(POLICY_PY), "FORBIDDEN_TREES") - literal(depot.new(POLICY_PY),
                                                                                    "FORBIDDEN_TREES")):
        report("interdit", tree, f"chemin interdit retiré : {tree}")
    for name in ("PROTECTED_PREFIXES", "PROTECTED_KEYS"):
        for key in sorted(literal(depot.old(CONFIG_PY), name) - literal(depot.new(CONFIG_PY), name)):
            report("protege", key, f"clé protégée retirée : {key}")
    for key in sorted(literal(depot.new(CONFIG_PY), "AGENT_SETTABLE") - literal(depot.old(CONFIG_PY), "AGENT_SETTABLE")):
        report("exception", key, f"nouvelle exception aux clés protégées : {key}")
    old_green = {line.split()[0] for line in rules(depot.old(GREEN))}
    for number in sorted(old_green - {line.split()[0] for line in rules(depot.new(GREEN))}):
        report("invariant", number, f"invariant retiré de la liste des verts : {number}")
    for rule in sorted(rules(depot.old(CODEOWNERS)) - rules(depot.new(CODEOWNERS))):
        report("codeowners", rule.split()[0], f"règle CODEOWNERS retirée : {rule}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--depot", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--base", default="HEAD")
    arguments = parser.parse_args()
    problems = check(Depot(arguments.depot, arguments.base))
    for problem in problems:
        print(f"[GARDE] {problem}")
    if not problems:
        print(f"Garde des gardes : aucun affaiblissement par rapport à {arguments.base}.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
