#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — tests fonctionnels de l'ISO produite.
#
# Usage : tests/test-features.sh chemin/vers/djerba-os-<version>-<arch>.iso
#
# Niveau 1 (sans privilège) : structure de l'ISO (xorriso) — EFI, GRUB,
#   thème, noyau, initrd, squashfs, paramètres de démarrage — puis contenu du
#   squashfs (unsquashfs -l / -cat) : fichiers, droits et configurations.
# Niveau 2 (root) : le squashfs est monté en lecture seule sous un overlay
#   tmpfs puis des contrôles sont exécutés dans un chroot : visudo, polkit,
#   imports Python/PyQt6/D-Bus, chargement QML réel de l'assistant d'accueil,
#   remote Flathub, dépôt Mozilla, absence de snapd, services activés.
set -euo pipefail

ISO="${1:-}"
[[ -n "${ISO}" && -f "${ISO}" ]] || { echo "Usage : $0 <image.iso>" >&2; exit 2; }
ISO="$(readlink -f "${ISO}")"

for tool in xorriso unsquashfs python3; do
    command -v "${tool}" >/dev/null 2>&1 || { echo "Outil requis manquant : ${tool}" >&2; exit 2; }
done

case "$(basename "${ISO}")" in
    *-arm64.iso) ARCH="arm64"; EFI_LOADER="bootaa64.efi" ;;
    *) ARCH="amd64"; EFI_LOADER="bootx64.efi" ;;
esac

WORK="$(mktemp -d)"
MOUNTS=()
# shellcheck disable=SC2317,SC2329 # fonction appelée via « trap EXIT »
cleanup() {
    local index
    for ((index = ${#MOUNTS[@]} - 1; index >= 0; index--)); do
        umount -l "${MOUNTS[index]}" 2>/dev/null || true
    done
    rm -rf "${WORK}"
}
trap cleanup EXIT

PASSED=0
FAILED=0
pass() { PASSED=$((PASSED + 1)); printf '\033[32m[ OK ]\033[0m %s\n' "$*"; }
fail() { FAILED=$((FAILED + 1)); printf '\033[31m[FAIL]\033[0m %s\n' "$*"; }
check() {
    local label="$1"
    shift
    if "$@" >/dev/null 2>&1; then pass "${label}"; else fail "${label}"; fi
}
section() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

# ============================================================== ISO
section "Structure de l'ISO (${ARCH})"
xorriso -indev "${ISO}" -find / -type f 2>/dev/null | sed -e "s/^'//" -e "s/'$//" > "${WORK}/iso.list"
xorriso -indev "${ISO}" -report_el_torito plain > "${WORK}/eltorito.txt" 2>&1 || true
xorriso -indev "${ISO}" -pvd_info > "${WORK}/pvd.txt" 2>&1 || true

iso_has() { grep -qiE "^$1\$" "${WORK}/iso.list"; }
check "Volume ISO « DJERBA_OS »" grep -q "Volume Id *: DJERBA_OS" "${WORK}/pvd.txt"
check "Chargeur EFI amovible /EFI/boot/${EFI_LOADER}" iso_has "/EFI/boot/${EFI_LOADER}"
check "Image EFI El Torito (efi.img)" grep -qiE "UEFI|0xef" "${WORK}/eltorito.txt"
check "Configuration GRUB /boot/grub/grub.cfg" iso_has "/boot/grub/grub.cfg"
check "Splash GRUB Djerba" iso_has "/boot/grub/splash.png"
check "Thème GRUB Djerba" iso_has "/boot/grub/live-theme/theme.txt"
check "Surbrillance verre du menu" iso_has "/boot/grub/live-theme/select_c.png"
check "Noyau live" iso_has "/live/vmlinuz.*"
check "initrd live" iso_has "/live/initrd.img.*"
check "Système de fichiers squashfs" iso_has "/live/filesystem.squashfs"
if [[ "${ARCH}" == "amd64" ]]; then
    check "Shim signé (Secure Boot)" iso_has "/EFI/boot/grubx64.efi"
    check "Amorçage BIOS (grub-pc El Torito)" grep -qiE "BIOS|0x00" "${WORK}/eltorito.txt"
fi

xorriso -osirrox on -indev "${ISO}" -extract /boot/grub/grub.cfg "${WORK}/grub.cfg" >/dev/null 2>&1
xorriso -osirrox on -indev "${ISO}" -extract /boot/grub/config.cfg "${WORK}/config.cfg" >/dev/null 2>&1
check "Paramètre boot=live" grep -q "boot=live" "${WORK}/grub.cfg"
check "Utilisateur live « djerba »" grep -q "username=djerba" "${WORK}/grub.cfg"
check "Plymouth (splash) au démarrage" grep -q " splash" "${WORK}/grub.cfg"
check "Console série pour l'exploitation headless" grep -qE "console=tty(S0|AMA0)" "${WORK}/grub.cfg"
check "Délai GRUB de 5 s" grep -q "set timeout=5" "${WORK}/config.cfg"

# ============================================================== squashfs
section "Contenu du squashfs"
SQUASHFS="${WORK}/filesystem.squashfs"
xorriso -osirrox on -indev "${ISO}" -extract /live/filesystem.squashfs "${SQUASHFS}" >/dev/null 2>&1
unsquashfs -lls "${SQUASHFS}" > "${WORK}/rootfs.list"
sq_has() { grep -qE " squashfs-root$1( |\$)" "${WORK}/rootfs.list"; }
sq_cat() { unsquashfs -cat "${SQUASHFS}" "$1" 2>/dev/null; }
sq_mode() { grep -E " squashfs-root$1\$" "${WORK}/rootfs.list" | awk '{print $1}' | head -n 1; }

check "Compression zstd" sh -c "unsquashfs -s '${SQUASHFS}' | grep -q 'Compression zstd'"
check "Blocs de 1 Mio" sh -c "unsquashfs -s '${SQUASHFS}' | grep -q 'Block size 1048576'"
if sq_has "/boot/vmlinuz-[0-9.]+-[0-9]+-lowlatency"; then
    pass "Noyau lowlatency"
elif sq_has "/boot/vmlinuz-[0-9.]+-[0-9]+-generic"; then
    pass "Noyau generic (repli documenté : lowlatency indisponible)"
else
    fail "Noyau Ubuntu introuvable"
fi
check "live-boot" sq_has "/usr/share/initramfs-tools/scripts/live"
check "user-setup (création de l'utilisateur live)" sq_has "/usr/lib/user-setup/user-setup-apply"
check "KDE Plasma Wayland" sq_has "/usr/share/wayland-sessions/plasmawayland.desktop"
check "KWin Wayland" sq_has "/usr/bin/kwin_wayland"
check "XWayland" sq_has "/usr/bin/Xwayland"
check "SDDM" sq_has "/usr/bin/sddm"
check "Calamares" sq_has "/usr/bin/calamares"
check "Assistant djerba-welcome" sq_has "/usr/local/bin/djerba-welcome"
check "Assistant exécutable" sh -c "[ \"\$(printf '%s' '$(sq_mode /usr/local/bin/djerba-welcome)' | cut -c4)\" = x ]"
check "Interface QML de l'assistant" sq_has "/usr/local/share/djerba-welcome/ui/main.qml"
check "Démarrage automatique de l'assistant" sq_has "/etc/xdg/autostart/djerba-welcome.desktop"
check "Thème Plymouth Djerba" sq_has "/usr/share/plymouth/themes/djerba/djerba.script"
check "Animation Plymouth (60 images)" sq_has "/usr/share/plymouth/themes/djerba/throbber-59.png"
check "Thème SDDM Djerba" sq_has "/usr/share/sddm/themes/djerba/Main.qml"
check "Branding Calamares Djerba" sq_has "/usr/share/calamares/branding/djerba/branding.desc"
check "Fond d'écran Djerba" sq_has "/usr/share/wallpapers/Djerba/contents/images/3840x2160.png"
check "GameMode" sq_has "/usr/bin/gamemoded"
check "Flatpak" sq_has "/usr/bin/flatpak"
check "Firefox (dépôt Mozilla)" sq_has "/usr/lib/firefox/firefox"
check "zram-tools" sq_has "/usr/sbin/zramswap"
check "BTRFS" sq_has "/usr/bin/btrfs"
check "Pas de snapd" sh -c "! grep -qE ' squashfs-root/usr/bin/snap$' '${WORK}/rootfs.list'"
if [[ "${ARCH}" == "amd64" ]]; then
    check "Steam (x86_64)" sq_has "/usr/games/steam"
    check "Wine 32 bits (multiarch i386)" sq_has "/usr/lib/wine/wine"
    check "GRUB EFI signé pour l'installation" sq_has "/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed"
else
    check "Pas de Steam sur arm64" sh -c "! grep -qE ' squashfs-root/usr/games/steam$' '${WORK}/rootfs.list'"
    check "GRUB EFI arm64 pour l'installation" sq_has "/usr/lib/grub/arm64-efi/linux.mod"
fi

section "Configurations embarquées"
check "mount.conf : sous-volume /@home" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/calamares/modules/mount.conf | grep -q 'subvolume: /@home'"
check "mount.conf : compress=zstd" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/calamares/modules/mount.conf | grep -q 'compress=zstd'"
check "partition.conf : pas de swap" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/calamares/modules/partition.conf | grep -q 'initialSwapChoice: none'"
check "flathub.conf (INI)" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/flatpak/remotes.d/flathub.conf | grep -q '^\[remote \"flathub\"\]'"
check "Remote Flathub enregistré" sh -c "unsquashfs -cat '${SQUASHFS}' /var/lib/flatpak/repo/config | grep -q 'remote \"flathub\"'"
check "Netplan -> NetworkManager" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/netplan/01-network-manager-all.yaml | grep -q 'renderer: NetworkManager'"
check "Netplan en 0600" sh -c "[ '$(sq_mode /etc/netplan/01-network-manager-all.yaml)' = '-rw-------' ]"
check "sudoers live en 0440" sh -c "[ '$(sq_mode /etc/sudoers.d/live-user)' = '-r--r-----' ]"
check "Autologin Wayland" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/sddm.conf.d/10-wayland-autologin.conf | grep -q 'Session=plasmawayland'"
check "Thème SDDM actif" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/sddm.conf.d/20-djerba-theme.conf | grep -q 'Current=djerba'"
check "zram en zstd" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/default/zramswap | grep -q 'ALGO=zstd'"
check "UFW activé" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/ufw/ufw.conf | grep -q '^ENABLED=yes'"
check "Durcissement sysctl" sq_has "/etc/sysctl.d/90-djerba-hardening.conf"
check "Optimisations sysctl jeu" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/sysctl.d/90-djerba-performance.conf | grep -q 'vm.max_map_count = 2147483642'"
check "Plymouth Djerba par défaut" sh -c "unsquashfs -lls '${SQUASHFS}' /etc/alternatives/default.plymouth | grep -q 'djerba/djerba.plymouth'"

# ============================================================== chroot
mkdir -p "${WORK}/probe"
if [[ "${EUID}" -eq 0 ]] && mount -t tmpfs -o size=1M probe "${WORK}/probe" 2>/dev/null; then
    umount "${WORK}/probe"
    section "Contrôles exécutés dans le système live (chroot overlay)"
    mkdir -p "${WORK}/lower" "${WORK}/rw" "${WORK}/root"
    mount -t squashfs -o loop,ro "${SQUASHFS}" "${WORK}/lower"; MOUNTS+=("${WORK}/lower")
    mount -t tmpfs -o size=2G tmpfs "${WORK}/rw"; MOUNTS+=("${WORK}/rw")
    mkdir -p "${WORK}/rw/upper" "${WORK}/rw/work"
    mount -t overlay overlay \
        -o "lowerdir=${WORK}/lower,upperdir=${WORK}/rw/upper,workdir=${WORK}/rw/work" "${WORK}/root"
    MOUNTS+=("${WORK}/root")
    mount -t proc proc "${WORK}/root/proc"; MOUNTS+=("${WORK}/root/proc")
    mount --bind /dev "${WORK}/root/dev"; MOUNTS+=("${WORK}/root/dev")
    mount -t sysfs sysfs "${WORK}/root/sys"; MOUNTS+=("${WORK}/root/sys")
    mount -t tmpfs tmpfs "${WORK}/root/tmp"; MOUNTS+=("${WORK}/root/tmp")

    in_chroot() { chroot "${WORK}/root" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 "$@"; }

    check "visudo valide l'ensemble des sudoers" in_chroot visudo -c
    check "Règle polkit présente" in_chroot test -f /etc/polkit-1/rules.d/99-djerba-installer.rules
    check "Action polkit org.djerba.installer" in_chroot grep -q 'org.djerba.installer' /usr/share/polkit-1/actions/org.djerba.installer.policy
    check "Python : PyQt6.QtQuick + dbus" in_chroot python3 -c 'import PyQt6.QtQuick, PyQt6.QtQml, dbus'
    check "Remote Flathub (flatpak remotes)" sh -c "chroot '${WORK}/root' flatpak remotes --system --columns=name | grep -qx flathub"
    check "Firefox épinglé sur le dépôt Mozilla" sh -c "chroot '${WORK}/root' apt-cache policy firefox | grep -q packages.mozilla.org"
    check "Service SDDM activé" in_chroot test -L /etc/systemd/system/display-manager.service
    check "Service zramswap activé" in_chroot systemctl is-enabled zramswap.service
    check "Service UFW activé" in_chroot systemctl is-enabled ufw.service
    check "Cible graphique par défaut" sh -c "chroot '${WORK}/root' systemctl get-default | grep -qx graphical.target"
    check "Groupe gamemode" in_chroot getent group gamemode
    check "Locale fr_FR.UTF-8 générée" sh -c "chroot '${WORK}/root' locale -a | grep -qi 'fr_FR.utf8'"

    cat > "${WORK}/root/tmp/qml-load.py" <<'PY'
import importlib.machinery
import importlib.util
import sys

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine

loader = importlib.machinery.SourceFileLoader("welcome", "/usr/local/bin/djerba-welcome")
spec = importlib.util.spec_from_loader("welcome", loader)
welcome = importlib.util.module_from_spec(spec)
loader.exec_module(welcome)

app = QGuiApplication(sys.argv)
backend = welcome.Backend(app)
engine = QQmlApplicationEngine()
engine.addImageProvider("icon", welcome.IconProvider())
engine.rootContext().setContextProperty("backend", backend)
errors = []
engine.warnings.connect(lambda warnings: errors.extend(w.toString() for w in warnings))
engine.load(QUrl.fromLocalFile(str(welcome.QML_FILE)))
if not engine.rootObjects():
    print("\n".join(errors) or "aucun objet racine", file=sys.stderr)
    sys.exit(1)
fatal = [e for e in errors if "is not a type" in e or "module" in e and "not installed" in e]
if fatal:
    print("\n".join(fatal), file=sys.stderr)
    sys.exit(1)
QTimer.singleShot(1500, app.quit)
sys.exit(app.exec())
PY
    check "Assistant : chargement QML réel (modules Qt 6 présents)" \
        chroot "${WORK}/root" /usr/bin/env -i PATH=/usr/bin:/bin HOME=/root LANG=C.UTF-8 \
        QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software XDG_RUNTIME_DIR=/tmp \
        timeout 60 python3 /tmp/qml-load.py
else
    printf '\nContrôles en chroot ignorés (root et montages requis).\n'
fi

printf '\n%d réussite(s), %d échec(s)\n' "${PASSED}" "${FAILED}"
[[ "${FAILED}" -eq 0 ]]
