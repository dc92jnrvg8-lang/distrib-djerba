#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — barrière de qualité exécutée avant toute compilation.
#
#   1. shellcheck sur TOUS les scripts Bash/sh du dépôt (détection par shebang)
#   2. présence de « set -euo pipefail » dans chaque script Bash
#   3. compilation stricte de tous les scripts Python (py_compile, -W error)
#   4. validation YAML/INI (tests/test-yaml-ini.py)
#   5. tests de l'agent IA (tests/test-djerba-ai.py)
#      et invariants de sécurité (tests/test-invariants.py --rapport, cliquet)
#   5. validation XML (politiques polkit), sudoers (visudo) et QML (qmllint
#      si disponible)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

FAILURES=0

pass() { printf '\033[32m[ OK ]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[FAIL]\033[0m %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
section() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

require() {
    local tool
    for tool in "$@"; do
        if ! command -v "${tool}" >/dev/null 2>&1; then
            printf 'Outil requis introuvable : %s\n' "${tool}" >&2
            exit 2
        fi
    done
}

require shellcheck python3 git

# Liste des fichiers suivis par git ou nouvellement ajoutés (hors artefacts
# de build ignorés par .gitignore).
list_files() {
    git ls-files --cached --others --exclude-standard -z
}

shebang_of() {
    head -n 1 "$1" 2>/dev/null | tr -d '\r' || true
}

SHELL_SCRIPTS=()
SOURCED_FRAGMENTS=()
PYTHON_SCRIPTS=()
while IFS= read -r -d '' file; do
    [[ -f "${file}" ]] || continue
    case "${file}" in
        *.py) PYTHON_SCRIPTS+=("${file}"); continue ;;
    esac
    case "${file}" in
        # Fragments shell sourcés (sans shebang) : environnement de session Plasma.
        */plasma-workspace/env/*.sh) SOURCED_FRAGMENTS+=("${file}"); continue ;;
    esac
    first_line="$(shebang_of "${file}")"
    case "${first_line}" in
        '#!'*bash*|'#!'*/sh|'#!/bin/sh'*) SHELL_SCRIPTS+=("${file}") ;;
        '#!'*python3*) PYTHON_SCRIPTS+=("${file}") ;;
    esac
done < <(list_files)

section "shellcheck (${#SHELL_SCRIPTS[@]} scripts)"
for script in "${SHELL_SCRIPTS[@]}"; do
    if shellcheck --external-sources --severity=style "${script}"; then
        pass "${script}"
    else
        fail "${script}"
    fi
done

section "shellcheck — fragments sourcés (${#SOURCED_FRAGMENTS[@]})"
# Sourcés par un processus parent : pas de « set -e » (il le contaminerait).
for fragment in "${SOURCED_FRAGMENTS[@]}"; do
    if shellcheck --shell=sh --severity=style "${fragment}" && ! grep -qE '^set -e' "${fragment}"; then
        pass "${fragment}"
    else
        fail "${fragment}"
    fi
done

section "Gestion stricte des erreurs Bash"
for script in "${SHELL_SCRIPTS[@]}"; do
    case "$(shebang_of "${script}")" in
        *bash*)
            if grep -qE '^set -euo pipefail$' "${script}"; then
                pass "${script}"
            else
                fail "${script} : 'set -euo pipefail' absent"
            fi
            ;;
        *)
            case "${script}" in
                */DEBIAN/*)
                    # Scripts de mainteneur Debian : convention « set -e »
                    # (variables positionnelles de dpkg facultatives).
                    if grep -qE '^set -e$' "${script}"; then
                        pass "${script} (script de mainteneur : set -e)"
                    else
                        fail "${script} : 'set -e' absent"
                    fi
                    ;;
                *)
                    if grep -qE '^set -eu$|^set -eux$' "${script}"; then
                        pass "${script} (POSIX sh : set -eu)"
                    else
                        fail "${script} : 'set -eu' absent"
                    fi
                    ;;
            esac
            ;;
    esac
    if [[ ! -x "${script}" ]]; then
        fail "${script} : bit exécutable manquant"
    fi
done

section "Python — compilation stricte (${#PYTHON_SCRIPTS[@]} fichiers)"
for script in "${PYTHON_SCRIPTS[@]}"; do
    if python3 -W error -c '
import ast, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    source = handle.read()
compile(ast.parse(source, filename=path), path, "exec")
' "${script}"; then
        pass "${script}"
    else
        fail "${script}"
    fi
done

section "YAML / INI"
if python3 tests/test-yaml-ini.py; then
    pass "tests/test-yaml-ini.py"
else
    fail "tests/test-yaml-ini.py"
fi

section "Agent IA (djerba_ai)"
if ! python3 -c 'import cryptography, pydantic, sys; sys.exit(int(pydantic.VERSION.split(".")[0]) < 2)' 2>/dev/null; then
    # Hôte sans pydantic 2 / cryptography (ex. Ubuntu 24.04) : la suite tourne dans
    # l'image Debian 13 (étape « Agent tests » de la CI, ./build.sh).
    printf '[SKIP] tests/test-djerba-ai.py : pydantic 2 et python3-cryptography requis (exécuté dans l'\''image Debian 13)\n'
elif python3 tests/test-djerba-ai.py >"${TMPDIR:-/tmp}/djerba-ai-tests.log" 2>&1; then
    pass "tests/test-djerba-ai.py ($(grep -Eo 'Ran [0-9]+ tests' "${TMPDIR:-/tmp}/djerba-ai-tests.log"))"
else
    tail -n 40 "${TMPDIR:-/tmp}/djerba-ai-tests.log"
    fail "tests/test-djerba-ai.py"
fi

section "Invariants de sécurité (docs/INVARIANTS.md, cliquet tests/invariants-verts.txt)"
if ! python3 -c 'import cryptography, pydantic, sys; sys.exit(int(pydantic.VERSION.split(".")[0]) < 2)' 2>/dev/null; then
    printf '[SKIP] tests/test-invariants.py : pydantic 2 et python3-cryptography requis (exécuté dans l'\''image Debian 13)\n'
elif python3 tests/test-invariants.py --rapport >"${TMPDIR:-/tmp}/djerba-invariants.log" 2>&1; then
    pass "tests/test-invariants.py ($(head -n 1 "${TMPDIR:-/tmp}/djerba-invariants.log"))"
else
    tail -n 60 "${TMPDIR:-/tmp}/djerba-invariants.log"
    fail "tests/test-invariants.py (régression ou vert non déclaré)"
fi

section "XML (polkit)"
while IFS= read -r -d '' policy; do
    if python3 -c 'import sys, xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])' "${policy}"; then
        pass "${policy}"
    else
        fail "${policy}"
    fi
done < <(find config/includes.chroot packages -name '*.policy' -print0)

section "sudoers"
if command -v visudo >/dev/null 2>&1; then
    if visudo -cqf config/includes.chroot/etc/sudoers.d/live-user; then
        pass "config/includes.chroot/etc/sudoers.d/live-user"
    else
        fail "config/includes.chroot/etc/sudoers.d/live-user"
    fi
else
    printf 'visudo indisponible : vérification sudoers ignorée sur cet hôte.\n'
fi

section "QML"
QMLLINT=""
for candidate in /usr/lib/qt6/bin/qmllint qmllint6 qmllint; do
    if command -v "${candidate}" >/dev/null 2>&1; then
        QMLLINT="${candidate}"
        break
    fi
done
if [[ -n "${QMLLINT}" ]]; then
    while IFS= read -r -d '' qml; do
        if "${QMLLINT}" --disable-plugins "${qml}" >/dev/null 2>&1 || "${QMLLINT}" "${qml}"; then
            pass "${qml}"
        else
            fail "${qml}"
        fi
    done < <(find config/includes.chroot packages -name '*.qml' -print0)
else
    printf 'qmllint indisponible : le QML est validé au chargement par test-features.sh.\n'
fi

printf '\n'
if [[ "${FAILURES}" -gt 0 ]]; then
    printf '\033[31m%d contrôle(s) en échec.\033[0m\n' "${FAILURES}"
    exit 1
fi
printf '\033[32mTous les contrôles sont passés.\033[0m\n'
