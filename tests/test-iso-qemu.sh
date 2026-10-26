#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — test de démarrage de l'ISO dans QEMU, sans écran (headless).
#
# Usage : tests/test-iso-qemu.sh chemin/vers/djerba-os-<version>-<arch>.iso
#
# * Firmware UEFI natif : OVMF (x86_64, machine q35) ou AAVMF (arm64, virt).
# * L'ISO est attachée comme lecteur CD-ROM SCSI ; GRUB démarre l'entrée
#   par défaut après son délai, le noyau parle sur ttyS0 (ttyAMA0 en arm64).
# * Succès : l'invite « djerba login: » apparaît sur la console série,
#   preuve que GRUB EFI, le noyau, live-boot, systemd et live-config ont
#   fonctionné. Une capture d'écran de la session graphique est ensuite
#   prise via QMP pour les artefacts CI.
# * Échec : panique noyau, shell de secours initramfs, live-boot sans média
#   ou dépassement du délai (DJERBA_QEMU_TIMEOUT, 900 s par défaut).
#
# KVM est utilisé lorsque /dev/kvm est accessible, TCG sinon.
set -euo pipefail

ISO="${1:-}"
[[ -n "${ISO}" && -f "${ISO}" ]] || { echo "Usage : $0 <image.iso>" >&2; exit 2; }
ISO="$(readlink -f "${ISO}")"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/tests/out"
TIMEOUT="${DJERBA_QEMU_TIMEOUT:-900}"
DESKTOP_WAIT="${DJERBA_QEMU_DESKTOP_WAIT:-90}"
MEMORY="${DJERBA_QEMU_MEMORY:-4096}"
mkdir -p "${OUT}"

case "$(basename "${ISO}")" in
    *-arm64.iso) ARCH="arm64" ;;
    *) ARCH="amd64" ;;
esac

SERIAL_LOG="${OUT}/serial-${ARCH}.log"
SERIAL_TEXT="${OUT}/serial-${ARCH}.txt"
QEMU_LOG="${OUT}/qemu-${ARCH}.log"
QMP_SOCKET="${OUT}/qmp-${ARCH}.sock"
SCREENSHOT="${OUT}/desktop-${ARCH}.png"
VARS="${OUT}/efivars-${ARCH}.fd"
rm -f "${SERIAL_LOG}" "${SERIAL_TEXT}" "${QEMU_LOG}" "${QMP_SOCKET}" "${SCREENSHOT}"

ACCEL="tcg"
if [[ -r /dev/kvm && -w /dev/kvm ]]; then
    ACCEL="kvm"
fi

first_existing() {
    local candidate
    for candidate in "$@"; do
        if [[ -f "${candidate}" ]]; then
            echo "${candidate}"
            return 0
        fi
    done
    return 1
}

case "${ARCH}" in
    amd64)
        QEMU="qemu-system-x86_64"
        CODE="$(first_existing /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd)" \
            || { echo "OVMF introuvable (paquet ovmf)" >&2; exit 2; }
        VARS_TEMPLATE="$(first_existing /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd)"
        if [[ "${ACCEL}" == "kvm" ]]; then CPU="host"; else CPU="max"; fi
        MACHINE=(-machine "q35,accel=${ACCEL}" -cpu "${CPU}" -smp 4)
        VIDEO=(-device virtio-vga)
        ;;
    arm64)
        QEMU="qemu-system-aarch64"
        CODE="$(first_existing /usr/share/AAVMF/AAVMF_CODE.no-secboot.fd /usr/share/AAVMF/AAVMF_CODE.fd)" \
            || { echo "AAVMF introuvable (paquet qemu-efi-aarch64)" >&2; exit 2; }
        VARS_TEMPLATE="$(first_existing /usr/share/AAVMF/AAVMF_VARS.fd)"
        if [[ "${ACCEL}" == "kvm" ]]; then CPU="host"; else CPU="max"; fi
        MACHINE=(-machine "virt,accel=${ACCEL},gic-version=max" -cpu "${CPU}" -smp 4)
        VIDEO=(-device virtio-gpu-pci -device qemu-xhci -device usb-kbd -device usb-tablet)
        ;;
esac
command -v "${QEMU}" >/dev/null 2>&1 || { echo "${QEMU} introuvable" >&2; exit 2; }
cp -f "${VARS_TEMPLATE}" "${VARS}"

echo "[qemu] ${ARCH} — accélération ${ACCEL} — délai ${TIMEOUT} s"
echo "[qemu] firmware : ${CODE}"

"${QEMU}" \
    "${MACHINE[@]}" \
    -m "${MEMORY}" \
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=${CODE}" \
    -drive "if=pflash,format=raw,unit=1,file=${VARS}" \
    -device virtio-scsi-pci,id=scsi0 \
    -drive "if=none,id=live,media=cdrom,readonly=on,format=raw,file=${ISO}" \
    -device scsi-cd,bus=scsi0.0,drive=live,bootindex=0 \
    -nic user,model=virtio-net-pci \
    -device virtio-rng-pci \
    "${VIDEO[@]}" \
    -display none \
    -serial "file:${SERIAL_LOG}" \
    -qmp "unix:${QMP_SOCKET},server=on,wait=off" \
    -monitor none \
    -no-reboot \
    > "${QEMU_LOG}" 2>&1 &
QEMU_PID=$!

qmp() {
    python3 - "${QMP_SOCKET}" "$@" <<'PY'
import json
import socket
import sys

path, command = sys.argv[1], sys.argv[2]
arguments = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client.settimeout(30)
client.connect(path)
stream = client.makefile("rw")
json.loads(stream.readline())  # bannière QMP
for message in ({"execute": "qmp_capabilities"}, {"execute": command, "arguments": arguments}):
    stream.write(json.dumps(message) + "\n")
    stream.flush()
    while True:
        reply = json.loads(stream.readline())
        if "event" in reply:
            continue
        if "error" in reply:
            print(reply["error"], file=sys.stderr)
            sys.exit(1)
        break
PY
}

# shellcheck disable=SC2317,SC2329 # fonction appelée via « trap EXIT »
stop_qemu() {
    if kill -0 "${QEMU_PID}" 2>/dev/null; then
        qmp quit >/dev/null 2>&1 || kill "${QEMU_PID}" 2>/dev/null || true
        for _ in $(seq 1 20); do
            kill -0 "${QEMU_PID}" 2>/dev/null || break
            sleep 0.5
        done
        kill -9 "${QEMU_PID}" 2>/dev/null || true
    fi
}
trap stop_qemu EXIT

FAILURE_PATTERN='Kernel panic|end Kernel panic|\(initramfs\)|Unable to find a medium|BusyBox v[0-9]|Failed to start .*Display Manager|No bootable option'
SUCCESS_PATTERN='djerba login:'
# Sous émulation TCG (runners sans KVM, ARM64 notamment), le démarrage est
# jusqu'à vingt fois plus lent : udev peut dépasser le délai de 90 s de
# l'unité du port série et le getty n'apparaît pas. systemd affiche alors
# l'état des unités ; « systemd-user-sessions » terminé prouve que noyau,
# live-boot, squashfs, live-config et les services système ont démarré (la
# session graphique, elle, n'est vérifiable qu'avec KVM).
if [[ "${ACCEL}" == "tcg" ]]; then
    SUCCESS_PATTERN='djerba login:|Reached target graphical\.target|Finished systemd-user-sessions\.service'
fi

result="timeout"
elapsed=0
while [[ "${elapsed}" -lt "${TIMEOUT}" ]]; do
    if ! kill -0 "${QEMU_PID}" 2>/dev/null; then
        result="qemu-exited"
        break
    fi
    if [[ -f "${SERIAL_LOG}" ]]; then
        # systemd colore l'état des unités : séquences ANSI retirées avant analyse.
        sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' "${SERIAL_LOG}" > "${SERIAL_TEXT}"
        if grep -qaE "${SUCCESS_PATTERN}" "${SERIAL_TEXT}"; then
            result="success"
            break
        fi
        if grep -qaE "${FAILURE_PATTERN}" "${SERIAL_TEXT}"; then
            result="failure"
            break
        fi
    fi
    sleep 5
    elapsed=$((elapsed + 5))
    if (( elapsed % 60 == 0 )); then
        echo "[qemu] ${elapsed} s écoulées — dernière ligne série : $(tail -n 1 "${SERIAL_LOG}" 2>/dev/null | tr -d '\r' | cut -c1-120)"
    fi
done

echo "[qemu] résultat : ${result} après ${elapsed} s"
case "${result}" in
    success)
        if grep -qa 'djerba login:' "${SERIAL_TEXT}"; then
            echo "[qemu] invite de connexion atteinte ; attente de la session graphique (${DESKTOP_WAIT} s)"
        else
            echo "[qemu] démarrage système vérifié (émulation TCG) ; session graphique non vérifiable sans KVM"
        fi
        sleep "${DESKTOP_WAIT}"
        PPM="${OUT}/desktop-${ARCH}.ppm"
        if qmp screendump "{\"filename\": \"${PPM}\"}"; then
            sleep 2
            python3 -c 'import sys; from PIL import Image; Image.open(sys.argv[1]).save(sys.argv[2])' "${PPM}" "${SCREENSHOT}" \
                && rm -f "${PPM}" && echo "[qemu] capture : ${SCREENSHOT}"
        fi
        # Une session graphique vivante n'est jamais uniformément noire.
        if [[ -f "${SCREENSHOT}" ]] && python3 -c 'import sys; from PIL import Image; sys.exit(0 if Image.open(sys.argv[1]).convert("L").getextrema()[1] > 16 else 1)' "${SCREENSHOT}"; then
            echo "[qemu] session graphique affichée"
        elif [[ "${ACCEL}" == "kvm" ]]; then
            echo "[qemu] écran noir après ${DESKTOP_WAIT} s : session graphique absente" >&2
            exit 1
        else
            echo "[qemu] avertissement : écran encore noir (émulation TCG lente)" >&2
        fi
        grep -aE 'GRUB|Djerba|login:' "${SERIAL_LOG}" | tr -d '\r' | tail -n 5 || true
        exit 0
        ;;
    *)
        echo "[qemu] ---- 60 dernières lignes de la console série ----"
        tail -n 60 "${SERIAL_LOG}" 2>/dev/null | tr -d '\r' || true
        echo "[qemu] ---- journal QEMU ----"
        cat "${QEMU_LOG}" || true
        exit 1
        ;;
esac
