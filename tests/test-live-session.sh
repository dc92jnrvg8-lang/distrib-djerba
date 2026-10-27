#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — tests de la session live : démarre l'ISO (UEFI, sans écran),
# se connecte sur la console série et exécute tests/live-session-checks.py.
#
# Usage : tests/test-live-session.sh <image.iso> [--network]
#
# Sans --network, la carte réseau de la VM est isolée (QEMU « restrict=on ») :
# contrôles hors ligne seulement. --network donne à la VM l'accès Internet de
# l'hôte (runner CI) : DNS, HTTPS, Firefox et mode anonyme via Tor.
# Variables : DJERBA_QEMU_MEMORY (Mio, défaut 6144).
set -euo pipefail

ISO="${1:-}"
[[ -n "${ISO}" && -f "${ISO}" ]] || { echo "Usage : $0 <image.iso> [--network]" >&2; exit 2; }
ISO="$(readlink -f "${ISO}")"
NETWORK="false"
if [[ "${2:-}" == "--network" ]]; then NETWORK="true"; fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/tests/out"
mkdir -p "${OUT}"
MEMORY="${DJERBA_QEMU_MEMORY:-6144}"

case "$(basename "${ISO}")" in
    *-arm64.iso) echo "Tests de session : x86_64 uniquement (KVM requis pour une session Plasma réactive)" >&2; exit 2 ;;
esac

ACCEL="tcg"; CPU="max"
if [[ -r /dev/kvm && -w /dev/kvm ]]; then ACCEL="kvm"; CPU="host"; fi
CODE="/usr/share/OVMF/OVMF_CODE_4M.fd"
VARS="${OUT}/efivars-live.fd"
cp -f /usr/share/OVMF/OVMF_VARS_4M.fd "${VARS}"
SOCKET="${OUT}/live-serial.sock"
rm -f "${SOCKET}"

NIC="user,model=virtio-net-pci,restrict=on"
if [[ "${NETWORK}" == "true" ]]; then NIC="user,model=virtio-net-pci"; fi

echo "[live] accélération ${ACCEL}, réseau : ${NETWORK}"
qemu-system-x86_64 \
    -machine "q35,accel=${ACCEL}" -cpu "${CPU}" -smp 4 -m "${MEMORY}" \
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=${CODE}" \
    -drive "if=pflash,format=raw,unit=1,file=${VARS}" \
    -device virtio-scsi-pci,id=scsi0 \
    -drive "if=none,id=live,media=cdrom,readonly=on,format=raw,file=${ISO}" \
    -device scsi-cd,bus=scsi0.0,drive=live,bootindex=0 \
    -nic "${NIC}" -device virtio-rng-pci -device virtio-vga -display none \
    -serial "unix:${SOCKET},server=on,wait=off" -monitor none -no-reboot \
    > "${OUT}/qemu-live.log" 2>&1 &
QEMU_PID=$!
# shellcheck disable=SC2317,SC2329 # fonction appelée via « trap EXIT »
cleanup() { kill "${QEMU_PID}" 2>/dev/null || true; }
trap cleanup EXIT

ARGS=("${SOCKET}" "${OUT}/serial-live.txt")
if [[ "${NETWORK}" == "true" ]]; then ARGS+=(--network); fi
python3 "${ROOT}/tests/live-session-checks.py" "${ARGS[@]}"
