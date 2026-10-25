#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — orchestrateur de construction de l'ISO live hybride.
#
# Étapes :
#   1. contrôles préalables (root, outils, live-build moderne)
#   2. rafraîchissement APT avec reprise automatique (micro-coupures ARM64)
#   3. choix du noyau : lowlatency si publié pour l'architecture, sinon generic
#   4. lints et validation YAML/INI (tests/run_local_lints.sh)
#   5. génération de l'identité visuelle (assets_generator/generate_assets.py)
#   6. lb clean / lb config / lb build (reprise native par étapes en cas
#      d'échec réseau : live-build ne rejoue que les étapes non terminées)
#   7. publication de l'ISO et de sa somme SHA-256 dans artifacts/
#
# Usage : ./build.sh [options]
#   --arch amd64|arm64   architecture cible (défaut : celle de l'hôte)
#   --docker             construire l'image Docker puis s'y relancer
#   --skip-lints         ne pas relancer les lints (déjà faits en CI)
#   --skip-assets        ne pas régénérer les assets (déjà faits en CI)
#   --purge              vider aussi le cache de paquets live-build
#   --retries N          nombre de tentatives de « lb build » (défaut : 3)
#   -h, --help           afficher cette aide
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"

ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case "${ARCH}" in
    x86_64) ARCH="amd64" ;;
    aarch64) ARCH="arm64" ;;
esac
USE_DOCKER="false"
SKIP_LINTS="false"
SKIP_ASSETS="false"
PURGE="false"
RETRIES=3
ARTIFACTS_DIR="${ROOT}/artifacts"
DOCKER_IMAGE="${DJERBA_DOCKER_IMAGE:-djerba-os-builder:local}"
VERSION="${DJERBA_VERSION:-$(date -u +%Y.%m)}"

log() { printf '\033[1;36m[djerba]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[djerba]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[djerba]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '/^# Usage/,/^set -euo/p' "${BASH_SOURCE[0]}" | sed -e '/^set -euo/d' -e 's/^# \{0,1\}//'
}

# Exécute une commande jusqu'à succès, avec un nombre maximal d'essais et une
# attente croissante (5 s, 10 s, 15 s...).
retry() {
    local attempts="$1"
    shift
    local n=1
    until "$@"; do
        if [[ "${n}" -ge "${attempts}" ]]; then
            warn "Échec définitif après ${attempts} essais : $*"
            return 1
        fi
        warn "Échec (essai ${n}/${attempts}) : $* — nouvel essai dans $((n * 5)) s"
        sleep $((n * 5))
        n=$((n + 1))
    done
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --arch) ARCH="${2:?--arch attend amd64 ou arm64}"; shift 2 ;;
            --docker) USE_DOCKER="true"; shift ;;
            --skip-lints) SKIP_LINTS="true"; shift ;;
            --skip-assets) SKIP_ASSETS="true"; shift ;;
            --purge) PURGE="true"; shift ;;
            --retries) RETRIES="${2:?--retries attend un entier}"; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) die "Option inconnue : $1 (voir --help)" ;;
        esac
    done
    case "${ARCH}" in
        amd64|arm64) ;;
        *) die "Architecture non supportée : ${ARCH}" ;;
    esac
    [[ "${RETRIES}" =~ ^[1-9][0-9]*$ ]] || die "--retries doit être un entier positif"
}

run_in_docker() {
    command -v docker >/dev/null 2>&1 || die "Docker est introuvable."
    local host_arch
    host_arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
    case "${host_arch}" in x86_64) host_arch="amd64" ;; aarch64) host_arch="arm64" ;; esac
    [[ "${host_arch}" == "${ARCH}" ]] || die "Construction croisée non supportée : utilisez un hôte ${ARCH} natif."
    log "Construction de l'image Docker ${DOCKER_IMAGE}"
    retry 3 docker build --pull -t "${DOCKER_IMAGE}" "${ROOT}"
    local args=(--arch "${ARCH}" --retries "${RETRIES}")
    [[ "${SKIP_LINTS}" == "true" ]] && args+=(--skip-lints)
    [[ "${SKIP_ASSETS}" == "true" ]] && args+=(--skip-assets)
    [[ "${PURGE}" == "true" ]] && args+=(--purge)
    log "Relance de build.sh dans le conteneur (mode privilégié requis par debootstrap/mount)"
    exec docker run --rm --privileged \
        -e DJERBA_VERSION="${VERSION}" \
        -e DJERBA_LOCALE="${DJERBA_LOCALE:-fr_FR.UTF-8}" \
        -e DJERBA_KEYMAP="${DJERBA_KEYMAP:-fr}" \
        -e DJERBA_IN_DOCKER=1 \
        -v "${ROOT}:/build" -w /build \
        "${DOCKER_IMAGE}" ./build.sh "${args[@]}"
}

preflight() {
    [[ "${EUID}" -eq 0 ]] || die "build.sh doit être exécuté en root (ou via --docker)."
    local tool
    for tool in lb debootstrap xorriso mksquashfs mkfs.vfat mcopy python3 apt-get apt-cache rsync; do
        command -v "${tool}" >/dev/null 2>&1 || die "Outil manquant : ${tool} (utilisez le Dockerfile)."
    done
    local lb_version
    lb_version="$(dpkg-query -W -f='${Version}' live-build 2>/dev/null || true)"
    [[ "${lb_version}" == 1:* ]] || die "live-build moderne (1:2023xxxx+) requis, trouvé : ${lb_version:-aucun}."
    [[ -r /usr/share/keyrings/ubuntu-archive-keyring.gpg ]] || die "ubuntu-keyring manquant."
    log "live-build ${lb_version} — cible ${ARCH} — version ${VERSION}"
}

refresh_apt() {
    log "Rafraîchissement des index APT de l'hôte"
    local n=0
    until apt-get update; do
        n=$((n + 1))
        [[ "${n}" -lt 20 ]] || die "apt-get update échoue de façon persistante."
        warn "apt-get update a échoué, nouvel essai dans 5 s (${n}/20)"
        sleep 5
    done
}

select_kernel_flavour() {
    # L'hôte est un Ubuntu 24.04 de même architecture que la cible avec les
    # mêmes composantes : ses index APT reflètent donc le dépôt de l'ISO.
    if apt-cache show --no-all-versions linux-image-lowlatency >/dev/null 2>&1; then
        DJERBA_KERNEL_FLAVOUR="lowlatency"
    else
        warn "linux-image-lowlatency indisponible pour ${ARCH} : repli sur linux-image-generic."
        DJERBA_KERNEL_FLAVOUR="generic"
    fi
    export DJERBA_KERNEL_FLAVOUR
    log "Noyau retenu : linux-image-${DJERBA_KERNEL_FLAVOUR}"
}

run_lints() {
    if [[ "${SKIP_LINTS}" == "true" ]]; then
        log "Lints ignorés (--skip-lints)"
        return
    fi
    log "Lints et validation YAML/INI"
    "${ROOT}/tests/run_local_lints.sh"
}

generate_assets() {
    if [[ "${SKIP_ASSETS}" == "true" ]]; then
        log "Génération des assets ignorée (--skip-assets)"
    else
        log "Génération de l'identité visuelle"
        QT_QPA_PLATFORM=offscreen python3 "${ROOT}/assets_generator/generate_assets.py" --version "${VERSION}"
    fi
    [[ -f "${ROOT}/config/bootloaders/grub-pc/splash.png" ]] \
        || die "Assets absents : lancez assets_generator/generate_assets.py."
}

live_build() {
    export DJERBA_ARCH="${ARCH}" DJERBA_VERSION="${VERSION}"
    log "lb clean"
    if [[ "${PURGE}" == "true" ]]; then
        lb clean --purge
    else
        lb clean
    fi
    log "lb config"
    lb config
    local attempt=1
    until lb build; do
        if [[ "${attempt}" -ge "${RETRIES}" ]]; then
            die "lb build a échoué ${RETRIES} fois — voir build.log."
        fi
        warn "lb build a échoué (essai ${attempt}/${RETRIES}) ; reprise depuis la dernière étape terminée dans 30 s."
        sleep 30
        attempt=$((attempt + 1))
    done
}

publish() {
    local iso="${ROOT}/djerba-os-${ARCH}.hybrid.iso"
    [[ -f "${iso}" ]] || die "ISO introuvable : ${iso}"
    mkdir -p "${ARTIFACTS_DIR}"
    local target="${ARTIFACTS_DIR}/djerba-os-${VERSION}-${ARCH}.iso"
    mv -f "${iso}" "${target}"
    for extra in packages contents files; do
        if [[ -f "${ROOT}/djerba-os-${ARCH}.${extra}" ]]; then
            mv -f "${ROOT}/djerba-os-${ARCH}.${extra}" "${ARTIFACTS_DIR}/djerba-os-${VERSION}-${ARCH}.${extra}"
        fi
    done
    (cd "${ARTIFACTS_DIR}" && sha256sum "$(basename "${target}")" > "$(basename "${target}").sha256")
    if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
        chown -R "${SUDO_UID}:${SUDO_GID}" "${ARTIFACTS_DIR}"
    fi
    log "ISO prête : ${target} ($(du -h "${target}" | cut -f1))"
}

main() {
    parse_args "$@"
    if [[ "${USE_DOCKER}" == "true" && -z "${DJERBA_IN_DOCKER:-}" ]]; then
        run_in_docker
    fi
    preflight
    refresh_apt
    select_kernel_flavour
    run_lints
    generate_assets
    live_build
    publish
}

main "$@"
