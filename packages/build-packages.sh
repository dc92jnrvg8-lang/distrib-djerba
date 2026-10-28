#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — construction des paquets Debian propres à la distribution.
#
# Chaque répertoire packages/<nom>/ contient :
#   DEBIAN/control.in        métadonnées (@VERSION@ remplacé à la construction)
#   DEBIAN/{preinst,postinst,prerm,postrm,conffiles}   facultatifs
#   root/                    arborescence installée telle quelle
# Pour djerba-branding, l'identité visuelle générée par
# assets_generator/generate_assets.py (build/djerba-branding/) est ajoutée.
#
# Les .deb produits sont déposés dans config/packages.chroot/, où live-build
# les installe nativement dans le système live (et donc sur la cible).
#
# Usage : packages/build-packages.sh [--version V] [--output DIR]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${DJERBA_VERSION:-$(date -u +%Y.%m)}"
OUTPUT="${ROOT}/config/packages.chroot"
GENERATED="${ROOT}/build/djerba-branding"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version) VERSION="${2:?--version attend une valeur}"; shift 2 ;;
        --output) OUTPUT="${2:?--output attend un répertoire}"; shift 2 ;;
        -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Option inconnue : $1" >&2; exit 2 ;;
    esac
done

for tool in dpkg-deb fakeroot; do
    command -v "${tool}" >/dev/null 2>&1 || { echo "Outil requis manquant : ${tool}" >&2; exit 2; }
done

# Version Debian valide : « 2026.10-dev » devient « 2026.10~dev » (le tilde
# trie avant la version finale, le tiret est réservé à la révision).
DEB_VERSION="${VERSION//-/\~}"
[[ "${DEB_VERSION}" =~ ^[0-9][A-Za-z0-9.+~]*$ ]] || { echo "Version invalide : ${VERSION}" >&2; exit 2; }

mkdir -p "${OUTPUT}"
rm -f "${OUTPUT}"/djerba-*.deb

STAGING="$(mktemp -d)"
trap 'rm -rf "${STAGING}"' EXIT

build_package() {
    local name="$1"
    local source="${ROOT}/packages/${name}"
    local stage="${STAGING}/${name}"

    mkdir -p "${stage}/DEBIAN"
    if [[ -d "${source}/root" ]]; then
        cp -a "${source}/root/." "${stage}/"
        # Caches Python d'exécutions locales : jamais dans le paquet.
        find "${stage}" -name __pycache__ -type d -prune -exec rm -rf {} +
    fi
    if [[ "${name}" == "djerba-branding" ]]; then
        [[ -d "${GENERATED}" ]] || { echo "Identité visuelle absente : lancez assets_generator/generate_assets.py" >&2; exit 1; }
        cp -a "${GENERATED}/." "${stage}/"
    fi

    sed -e "s/@VERSION@/${DEB_VERSION}/g" "${source}/DEBIAN/control.in" > "${stage}/DEBIAN/control"
    local script
    for script in preinst postinst prerm postrm conffiles triggers; do
        if [[ -f "${source}/DEBIAN/${script}" ]]; then
            # @VERSION@ aussi dans les scripts (dpkg-maintscript-helper : version de transition).
            sed -e "s/@VERSION@/${DEB_VERSION}/g" "${source}/DEBIAN/${script}" > "${stage}/DEBIAN/${script}"
            chmod 0755 "${stage}/DEBIAN/${script}"
        fi
    done
    if [[ -f "${stage}/DEBIAN/conffiles" ]]; then
        chmod 0644 "${stage}/DEBIAN/conffiles"
    fi

    # Droits normalisés : répertoires 0755, fichiers 0644, exécutables 0755.
    find "${stage}" -path "${stage}/DEBIAN" -prune -o -type d -exec chmod 0755 {} +
    find "${stage}" -path "${stage}/DEBIAN" -prune -o -type f -exec chmod 0644 {} +
    local executable
    while IFS= read -r -d '' executable; do
        chmod 0755 "${executable}"
    done < <(find "${stage}/usr/bin" "${stage}/usr/sbin" "${stage}/usr/libexec" "${stage}/usr/lib/djerba" \
                  -type f -print0 2>/dev/null)
    find "${stage}/etc/sudoers.d" -type f -exec chmod 0440 {} + 2>/dev/null || true

    # Manifeste signé des points d'entrée autorisés à solliciter le démon root
    # (core/trust.py) : empreintes SHA-256 calculées sur les fichiers livrés.
    if [[ -f "${stage}/usr/bin/djerba-ai" ]]; then
        mkdir -p "${stage}/usr/share/djerba-ai"
        (cd "${stage}" && sha256sum usr/bin/djerba-ai) | sed 's|  usr/|  /usr/|' \
            > "${stage}/usr/share/djerba-ai/trusted-callers.sha256"
        chmod 0644 "${stage}/usr/share/djerba-ai/trusted-callers.sha256"
    fi

    local installed_size
    installed_size="$(du -sk --exclude=DEBIAN "${stage}" | cut -f1)"
    echo "Installed-Size: ${installed_size}" >> "${stage}/DEBIAN/control"

    local deb="${OUTPUT}/${name}_${DEB_VERSION}_all.deb"
    fakeroot dpkg-deb --root-owner-group -Zxz --build "${stage}" "${deb}" >/dev/null
    dpkg-deb --info "${deb}" >/dev/null
    printf '[paquets] %s (%s)\n' "$(basename "${deb}")" "$(du -h "${deb}" | cut -f1)"
}

for package_dir in "${ROOT}"/packages/*/; do
    name="$(basename "${package_dir}")"
    [[ -f "${package_dir}/DEBIAN/control.in" ]] || continue
    build_package "${name}"
done
