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
#   identité Djerba OS (os-release, paquets djerba-*), catalogue
#   d'applications, remote Flathub, dépôt Mozilla, services activés.
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
check "Noyau Debian ${ARCH}" sq_has "/boot/vmlinuz-[0-9][^ ]*-${ARCH}"
check "Préemption complète au démarrage (preempt=full)" grep -q "preempt=full" "${WORK}/grub.cfg"
check "Identité Djerba OS (os-release)" sh -c "unsquashfs -cat '${SQUASHFS}' /usr/lib/os-release | grep -qx 'ID=djerba'"
check "/etc/os-release régénéré par djerba-branding" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/os-release | grep -q '^PRETTY_NAME=\"Djerba OS'"
check "Logo Djerba pour « À propos »" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/xdg/kcm-about-distrorc | grep -q 'LogoPath=/usr/share/pixmaps/djerba-os.png'"
check "Icône djerba-os (hicolor)" sq_has "/usr/share/icons/hicolor/scalable/apps/djerba-os.svg"
check "live-boot" sq_has "/usr/share/initramfs-tools/scripts/live"
check "user-setup (création de l'utilisateur live)" sq_has "/usr/lib/user-setup/user-setup-apply"
check "KDE Plasma Wayland" sq_has "/usr/share/wayland-sessions/plasma.desktop"
check "KWin Wayland" sq_has "/usr/bin/kwin_wayland"
check "XWayland" sq_has "/usr/bin/Xwayland"
check "SDDM" sq_has "/usr/bin/sddm"
check "Calamares" sq_has "/usr/bin/calamares"
for tool in djerba-welcome djerba-apps djerba-anonsurf djerba-undercover djerba-gamemode djerba-game-center djerba-ai; do
    check "Outil ${tool} exécutable" sh -c "[ \"\$(printf '%s' '$(sq_mode "/usr/bin/${tool}")' | cut -c4)\" = x ]"
done
check "Interface QML de l'assistant" sq_has "/usr/share/djerba-welcome/ui/main.qml"
check "Catalogue d'applications" sq_has "/usr/share/djerba/apps.yaml"
check "Installation des applications au premier démarrage" sq_has "/usr/lib/systemd/system/djerba-apps-firstboot.service"
check "Mode anonyme : unité Tor dédiée" sq_has "/usr/lib/systemd/system/djerba-anonsurf-tor.service"
check "Mode anonyme : torrc lisible par Tor sous AppArmor (/etc/tor)" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/tor/djerba-anonsurf.torrc | grep -q '^TransPort'"
check "DNS : /etc/resolv.conf -> stub systemd-resolved" sh -c "grep -qE ' squashfs-root/etc/resolv.conf -> \.\./run/systemd/resolve/stub-resolv.conf$' '${WORK}/rootfs.list'"
check "Tor et nftables" sh -c "grep -qE ' squashfs-root/usr/(s)?bin/tor$' '${WORK}/rootfs.list' && grep -qE ' squashfs-root/usr/sbin/nft$' '${WORK}/rootfs.list'"
check "Mode discret style Windows 10" sq_has "/usr/share/plasma/look-and-feel/org.djerba.undercover10.desktop/metadata.json"
check "Mode discret style Windows 11" sq_has "/usr/share/plasma/look-and-feel/org.djerba.undercover11.desktop/metadata.json"
check "Antivirus ClamAV" sq_has "/usr/bin/clamscan"
check "Interface ClamTk" sq_has "/usr/bin/clamtk"
check "MangoHud" sq_has "/usr/bin/mangohud"
check "Calamares : choix d'applications (netinstall)" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/calamares/modules/netinstall.conf | grep -q 'djerba-app:'"
check "Calamares : module djerbaapps" sq_has "/usr/lib/calamares/modules/djerbaapps/main.py"
check "Djerba Game Center" sq_has "/usr/share/djerba-game-center/ui/main.qml"
check "Agent IA : application djerba-ai" sq_has "/usr/share/applications/djerba-ai.desktop"
check "Agent IA : démon root (socket systemd)" sq_has "/usr/lib/systemd/system/djerba-ai-sysd.socket"
check "Agent IA : démon root exécutable" sh -c "[ \"\$(printf '%s' '$(sq_mode /usr/libexec/djerba-ai-sysd)' | cut -c4)\" = x ]"
check "Agent IA : démon utilisateur" sq_has "/usr/lib/systemd/user/djerba-ai.service"
check "Agent IA : actions polkit" sq_has "/usr/share/polkit-1/actions/org.djerba.ai.policy"
check "Agent IA : optimiseur de jeu" sq_has "/usr/lib/python3/dist-packages/djerba_ai/tools/performance/game_optimizer.py"
check "Agent IA : Studio (app_generator)" sq_has "/usr/lib/python3/dist-packages/djerba_ai/tools/devtools/app_generator.py"
check "Agent IA : icône" sq_has "/usr/share/icons/hicolor/scalable/apps/djerba-ai.svg"
check "Agent IA : capacités système « os » (apt, Flatpak, fichiers, recherche sémantique)" sq_has "/usr/lib/python3/dist-packages/djerba_ai/tools/os/search.py"
check "Agent IA : auto-extension" sq_has "/usr/lib/python3/dist-packages/djerba_ai/core/extend.py"
check "Agent IA : barre Spotlight (Meta+Espace)" sh -c "unsquashfs -cat '${SQUASHFS}' /usr/share/applications/djerba-ai-spotlight.desktop | grep -qx 'X-KDE-Shortcuts=Meta+Space'"
check "Agent IA : presse-papiers (Meta+Maj+C)" sh -c "unsquashfs -cat '${SQUASHFS}' /usr/share/applications/djerba-ai-clipboard.desktop | grep -qx 'X-KDE-Shortcuts=Meta+Shift+C'"
check "Agent IA : icône résidente (autostart)" sq_has "/etc/xdg/autostart/djerba-ai-tray.desktop"
check "Agent IA : réglages, Spotlight et Studio (QML)" sh -c "grep -qE ' squashfs-root/usr/lib/python3/dist-packages/djerba_ai/gui/qml/Spotlight.qml$' '${WORK}/rootfs.list' && grep -qE ' squashfs-root/usr/lib/python3/dist-packages/djerba_ai/gui/qml/SettingsPage.qml$' '${WORK}/rootfs.list'"
check "Agent IA : service système après Tailscale et le réseau" sh -c "unsquashfs -cat '${SQUASHFS}' /usr/lib/systemd/system/djerba-ai-sysd.service | grep -q '^Wants=network-online.target tailscaled.service'"
check "Agent IA : couche cognitive, garde anti-injection, bac à sable hybride" sh -c "for m in engine guard schemas sandbox seccomp_launch trust contextbus telemetry ghost dlp crypto; do grep -qE \" squashfs-root/usr/lib/python3/dist-packages/djerba_ai/core/\$m.py\$\" '${WORK}/rootfs.list' || exit 1; done"
check "Agent IA : manifeste des points d'entrée signés" sq_has "/usr/share/djerba-ai/trusted-callers.sha256"
check "Agent IA : script KWin du bus de contexte" sq_has "/usr/share/djerba-ai/kwin/active-window.js"
check "Agent IA : tableau de bord Grafana et configuration Prometheus" sh -c "unsquashfs -cat '${SQUASHFS}' /usr/share/djerba-ai/monitoring/grafana-dashboard.json | python3 -c 'import json,sys; assert len(json.load(sys.stdin)[\"panels\"]) >= 8'"
check "Agent IA : couche bureau (KDE/GNOME), anneau d'action, blindage" sh -c "for m in ui/adapter ui/hud security/apparmor security/ids security/netpolicy security/plugin_runner; do grep -qE \" squashfs-root/usr/lib/python3/dist-packages/djerba_ai/\$m.py\$\" '${WORK}/rootfs.list' || exit 1; done"
check "Agent IA : anneau d'action sur Super+Espace" sh -c "unsquashfs -cat '${SQUASHFS}' /usr/share/applications/djerba-ai-spotlight.desktop | grep -qx 'Exec=djerba-ai --hud'"
check "Agent IA : extension GNOME Shell" sq_has "/usr/share/gnome-shell/extensions/djerba@djerba-os.org/extension.js"
check "Agent IA : menu Djerba de Dolphin" sq_has "/usr/share/kio/servicemenus/djerba-ai.desktop"
check "Agent IA : menu Djerba de Nautilus" sq_has "/usr/share/nautilus-python/extensions/djerba_ai_nautilus.py"
check "Agent IA : greffon Gedit" sq_has "/usr/share/djerba-ai/integrations/gedit/djerba.plugin"
check "Agent IA : détection d'intrusion (service)" sq_has "/usr/lib/systemd/system/djerba-ai-ids.service"
check "Installateur : bureau GNOME proposé" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/calamares/modules/netinstall.conf | grep -q 'gnome-desktop'"
check "Agent IA : ptrace restreint (yama)" sh -c "unsquashfs -cat '${SQUASHFS}' /usr/lib/sysctl.d/60-djerba-ai.conf | grep -q '^kernel.yama.ptrace_scope = 1'"
check "Démarrage automatique de l'assistant" sq_has "/etc/xdg/autostart/djerba-welcome.desktop"
check "Thème Plymouth Djerba" sq_has "/usr/share/plymouth/themes/djerba/djerba.script"
check "Animation Plymouth (60 images)" sq_has "/usr/share/plymouth/themes/djerba/throbber-59.png"
check "Thème SDDM Djerba" sq_has "/usr/share/sddm/themes/djerba/Main.qml"
check "Branding Calamares Djerba" sq_has "/usr/share/calamares/branding/djerba/branding.desc"
check "Fond d'écran Djerba" sq_has "/usr/share/wallpapers/Djerba/contents/images/3840x2160.png"
check "Look-and-Feel Plasma Djerba" sq_has "/usr/share/plasma/look-and-feel/org.djerba.desktop/contents/defaults"
check "Thème global Djerba Horizon (barre centrée)" sq_has "/usr/share/plasma/look-and-feel/org.djerba.horizon.desktop/contents/layouts/org.kde.plasma.desktop-layout.js"
check "Curseur logiciel KWin en machine virtuelle" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/xdg/plasma-workspace/env/10-djerba-vm-cursor.sh | grep -q 'KWIN_FORCE_SW_CURSOR=1'"
check "KWin sans profil OpenGL Core forcé" sh -c "! unsquashfs -cat '${SQUASHFS}' /etc/skel/.config/kwinrc | grep -q 'GLCore'"
check "GameMode" sq_has "/usr/bin/gamemoded"
check "Flatpak" sq_has "/usr/bin/flatpak"
check "Firefox (dépôt Mozilla)" sq_has "/usr/lib/firefox/firefox"
check "zram-tools" sq_has "/usr/sbin/zramswap"
check "BTRFS" sq_has "/usr/bin/btrfs"
check "Pas de snapd" sh -c "! grep -qE ' squashfs-root/usr/bin/snap$' '${WORK}/rootfs.list'"
if [[ "${ARCH}" == "amd64" ]]; then
    check "Steam proposé dans le catalogue (x86_64)" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/calamares/modules/netinstall.conf | grep -q 'djerba-app:steam'"
    check "GRUB EFI signé pour l'installation" sq_has "/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed"
else
    check "Steam absent du catalogue arm64" sh -c "! unsquashfs -cat '${SQUASHFS}' /etc/calamares/modules/netinstall.conf | grep -q 'djerba-app:steam'"
    check "GRUB EFI arm64 pour l'installation" sq_has "/usr/lib/grub/arm64-efi/linux.mod"
fi

section "Configurations embarquées"
check "mount.conf : sous-volume /@home" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/calamares/modules/mount.conf | grep -q 'subvolume: /@home'"
check "mount.conf : compress=zstd" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/calamares/modules/mount.conf | grep -q 'compress=zstd'"
check "partition.conf : pas de swap" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/calamares/modules/partition.conf | grep -q 'initialSwapChoice: none'"
check "flathub.conf (INI)" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/flatpak/remotes.d/flathub.conf | grep -q '^\[remote \"flathub\"\]'"
check "Remote Flathub enregistré" sh -c "unsquashfs -cat '${SQUASHFS}' /var/lib/flatpak/repo/config | grep -q 'remote \"flathub\"'"
check "sudoers live en 0440" sh -c "[ '$(sq_mode /etc/sudoers.d/live-user)' = '-r--r-----' ]"
check "Autologin X11 de live-config désactivé" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/live/config.conf.d/djerba.conf | grep -q 'LIVE_CONFIG_NOX11AUTOLOGIN=\"true\"'"
check "Autologin Wayland" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/sddm.conf.d/10-wayland-autologin.conf | grep -qx 'Session=plasma'"
check "Thème SDDM actif" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/sddm.conf.d/20-djerba-theme.conf | grep -q 'Current=djerba'"
check "zram en zstd" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/default/zramswap | grep -q 'ALGO=zstd'"
check "UFW activé" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/ufw/ufw.conf | grep -q '^ENABLED=yes'"
check "Durcissement sysctl" sq_has "/etc/sysctl.d/90-djerba-hardening.conf"
check "Optimisations sysctl jeu" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/sysctl.d/90-djerba-performance.conf | grep -q 'vm.max_map_count = 2147483642'"
check "Plymouth Djerba par défaut" sh -c "unsquashfs -cat '${SQUASHFS}' /etc/plymouth/plymouthd.conf | grep -qx 'Theme=djerba'"

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
    check "Actions polkit apps/anonsurf" sh -c "chroot '${WORK}/root' cat /usr/share/polkit-1/actions/org.djerba.tools.policy | grep -q 'org.djerba.anonsurf'"
    check "Paquets djerba-branding et djerba-tools installés" sh -c "chroot '${WORK}/root' dpkg-query -W -f='\${db:Status-Status}\n' djerba-branding djerba-tools | grep -c '^installed$' | grep -qx 2"
    check "Diversion de /usr/lib/os-release" sh -c "chroot '${WORK}/root' dpkg-divert --list /usr/lib/os-release | grep -q djerba-branding"
    check "Catalogue lisible (djerba-apps list)" sh -c "chroot '${WORK}/root' djerba-apps list --json | python3 -c 'import json,sys; assert len(json.load(sys.stdin)[\"apps\"]) >= 10'"
    check "Mode anonyme inactif par défaut" sh -c "chroot '${WORK}/root' djerba-anonsurf status --json | grep -q '\"active\": false'"
    check "tor.service désactivé (Tor à la demande)" sh -c "! chroot '${WORK}/root' systemctl is-enabled tor.service"
    check "Service djerba-apps-firstboot activé" in_chroot systemctl is-enabled djerba-apps-firstboot.service
    check "Mises à jour ClamAV (freshclam)" in_chroot systemctl is-enabled clamav-freshclam.service
    check "Agent IA : au moins 260 capacités" sh -c "chroot '${WORK}/root' env HOME=/tmp djerba-ai tools | tail -n 1 | grep -Eq '/ (2[6-9][0-9]|[3-9][0-9]{2}) capacités'"
    check "Agent IA : socket du démon root activée" in_chroot systemctl is-enabled djerba-ai-sysd.socket
    check "Agent IA : démon utilisateur activé pour toutes les sessions" in_chroot test -L /etc/systemd/user/graphical-session.target.wants/djerba-ai.service
    check "Agent IA : imports Python" in_chroot python3 -c 'import djerba_ai.core.agent, djerba_ai.core.sysd, djerba_ai.core.extend, djerba_ai.gui.app'
    check "Agent IA : /etc/djerba/config.toml root 0600" sh -c "[ \"\$(chroot '${WORK}/root' stat -c '%U %a' /etc/djerba/config.toml)\" = 'root 600' ]"
    check "Agent IA : configuration TOML valide ([llm] [shortcuts] [system])" in_chroot python3 -c 'import tomllib; d = tomllib.load(open("/etc/djerba/config.toml", "rb")); assert d["llm"]["provider"] == "local" and d["shortcuts"]["spotlight"] == "Meta+Space" and d["system"]["sandbox_timeout"] == 15'
    check "Agent IA : vue publique sans secret (0644)" sh -c "[ \"\$(chroot '${WORK}/root' stat -c '%a' /etc/djerba/config.public.json)\" = 644 ] && chroot '${WORK}/root' grep -q 'llm.cloud.api_key_set' /etc/djerba/config.public.json"
    check "Agent IA : journal d'audit root:adm 0640" sh -c "[ \"\$(chroot '${WORK}/root' stat -c '%U:%G %a' /var/log/djerba/audit.log)\" = 'root:adm 640' ]"
    check "Agent IA : venv interne (PEP 668)" in_chroot test -x /usr/share/djerba-ai/venv/bin/python3
    check "Agent IA : pyflakes (contrôle des capacités générées)" in_chroot python3 -m pyflakes --version
    check "Agent IA : pydantic 2, cryptography, seccomp, pytest-timeout, gi" in_chroot python3 -c 'import pydantic, cryptography, seccomp, pytest_timeout, gi; assert int(pydantic.VERSION.split(".")[0]) >= 2'
    check "Agent IA : point d'entrée isolé (-I) conforme au manifeste signé" sh -c "chroot '${WORK}/root' head -n 1 /usr/bin/djerba-ai | grep -qx '#!/usr/bin/python3 -I' && chroot '${WORK}/root' sh -c 'cd / && sha256sum -c /usr/share/djerba-ai/trusted-callers.sha256'"
    check "Agent IA : seccomp-bpf opérationnel (réseau refusé)" in_chroot python3 -I /usr/lib/python3/dist-packages/djerba_ai/core/seccomp_launch.py -- python3 -c 'import socket, sys; s = None
try: s = socket.socket(socket.AF_INET)
except PermissionError: sys.exit(0)
sys.exit(1)'
    check "Agent IA : détection d'intrusion activée" in_chroot systemctl is-enabled djerba-ai-ids.service
    check "Agent IA : AppArmor (apparmor_parser, aa-exec)" in_chroot sh -c 'command -v apparmor_parser && command -v aa-exec'
    check "Agent IA : profils AppArmor générés valides" in_chroot python3 -c 'from djerba_ai.security import apparmor as a; ok, d = a.check_syntax(a.render("plugin", "essai", "/home/x/.local/share/djerba/plugins")); assert ok, d'
    check "Agent IA : dossier des exécutions isolées (0711)" sh -c "[ \"\$(chroot '${WORK}/root' stat -c '%a' /var/lib/djerba-ai-runs)\" = 711 ]"
    check "Agent IA : Qt Quick 3D (orbe)" in_chroot test -d /usr/lib/x86_64-linux-gnu/qt6/qml/QtQuick3D -o -d /usr/lib/aarch64-linux-gnu/qt6/qml/QtQuick3D
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

loader = importlib.machinery.SourceFileLoader("welcome", "/usr/bin/djerba-welcome")
spec = importlib.util.spec_from_loader("welcome", loader)
welcome = importlib.util.module_from_spec(spec)
loader.exec_module(welcome)

app = QGuiApplication(sys.argv)
backend = welcome.Backend("home", app)
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
