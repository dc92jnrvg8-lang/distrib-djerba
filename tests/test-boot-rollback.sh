#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — test réel du rollback au démarrage (invariant de réversibilité, phase 1).
#
# Construit un vrai système Debian 13 installé — GPT, ESP FAT32, racine Btrfs
# dans le sous-volume @, noyau Debian, initramfs, GRUB EFI — et y installe les
# fichiers de rollback livrés par le paquet djerba-tools (djerba_ai, djerba-boot,
# /etc/grub.d/09_djerba_bootcount et 42_djerba_rollback, unités djerba-boot-ok).
# Puis le démarre plusieurs fois dans QEMU (UEFI OVMF, console série) :
#
#   1. démarrage sain : djerba-boot-ok.service (l'unité réelle) clone @ en
#      @djerba-last-good, remet le compteur GRUB à zéro sur l'ESP et ajoute
#      l'entrée de restauration (update-grub) ; le test casse alors @ (son
#      /sbin/init s'arrête aussitôt : panique noyau) et redémarre ;
#   2. et 3. GRUB incrémente le compteur (1 puis 2) et démarre @ : panique ;
#   4. compteur à 2 : GRUB démarre seul l'entrée « dernier démarrage
#      réussi » ; le système tourne sur @djerba-last-good, djerba-boot-ok
#      garde le compteur à 2 (pas de retour sur l'état cassé).
#
# Succès : la console série montre ces étapes dans cet ordre. Aucun
# raccourci : GRUB, le noyau, l'initramfs, Btrfs, l'ESP et les scripts du
# paquet sont les vrais. KVM si /dev/kvm est accessible, TCG sinon.
#
# Usage (root, dans l'image de construction) : tests/test-boot-rollback.sh
# Variables : DJERBA_MIRROR (miroir Debian), DJERBA_ROLLBACK_TIMEOUT (s, 2400).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE="${ROOT}/packages/djerba-tools/root"
OUT="${ROOT}/tests/out"
SERIAL="${OUT}/rollback-serial.log"
MIRROR="${DJERBA_MIRROR:-http://deb.debian.org/debian}"
TIMEOUT="${DJERBA_ROLLBACK_TIMEOUT:-2400}"
WORK="$(mktemp -d /var/tmp/djerba-rollback.XXXXXX)"
IMAGE="${WORK}/disque.raw"
TOP="${WORK}/sommet"
TARGET="${WORK}/racine"
LOOP=""
mkdir -p "${OUT}"

[[ "$(id -u)" == 0 ]] || { echo "[rollback] à exécuter en root (image de construction)" >&2; exit 2; }

detach() {
    # Tous les loops de l'image (disque entier et, en repli, un par partition).
    local device
    while read -r device; do
        if [[ -n "${device}" ]]; then losetup -d "${device}"; fi
    done < <(losetup -j "${IMAGE}" -O NAME -n 2>/dev/null)
    if [[ -n "${LOOP}" ]]; then rm -f "${LOOP}p1" "${LOOP}p2"; fi
    LOOP=""
}

cleanup() {
    set +e
    for mountpoint in "${TARGET}/boot/efi" "${TARGET}/dev/pts" "${TARGET}/dev" "${TARGET}/proc" "${TARGET}/sys" \
                      "${TARGET}/run" "${TARGET}" "${TOP}"; do
        mountpoint -q "${mountpoint}" && umount -l "${mountpoint}"
    done
    detach
    rm -rf "${WORK}"
}
trap cleanup EXIT

log() { printf '[rollback] %s\n' "$*"; }

# ---------------------------------------------------------------- Disque : GPT, ESP, Btrfs @
log "disque : GPT, ESP FAT32, Btrfs (sous-volume @)"
truncate -s 6G "${IMAGE}"
printf 'label: gpt\nsize=256MiB, type=uefi, name=ESP\ntype=linux, name=racine\n' | sfdisk -q "${IMAGE}"
LOOP="$(losetup -P --show -f "${IMAGE}")"
udevadm settle 2>/dev/null || sleep 2
LOOP_PARTITIONS=0
ESP_DEV="${LOOP}p1"
ROOT_DEV="${LOOP}p2"
if [[ ! -e "/sys/class/block/$(basename "${LOOP}")p1" ]]; then
    # Noyau sans balayage des partitions des périphériques loop (certains conteneurs) : un loop par
    # partition, au décalage lu dans la table GPT (mêmes octets, mêmes systèmes de fichiers).
    PARTS=()
    while read -r start sectors; do
        PARTS+=("$(losetup --show -f -o $((start * 512)) --sizelimit $((sectors * 512)) "${IMAGE}")")
    done < <(partx -g -o START,SECTORS "${LOOP}")
    LOOP_PARTITIONS=1
    ESP_DEV="${PARTS[0]}"
    ROOT_DEV="${PARTS[1]}"
else
    for partition in "${ESP_DEV}" "${ROOT_DEV}"; do
        # Conteneur sans udev : nœud de la partition créé depuis sysfs (majeur:mineur du noyau).
        if [[ ! -b "${partition}" ]]; then
            IFS=: read -r major minor <"/sys/class/block/$(basename "${partition}")/dev"
            mknod "${partition}" b "${major}" "${minor}"
        fi
    done
fi
for partition in "${ESP_DEV}" "${ROOT_DEV}"; do
    [[ -b "${partition}" ]] || { echo "[rollback] partition ${partition} absente" >&2; exit 1; }
done
mkfs.vfat -F 32 -n ESP "${ESP_DEV}" >/dev/null
mkfs.btrfs -q -f -L djerba "${ROOT_DEV}"
mkdir -p "${TOP}" "${TARGET}"
mount "${ROOT_DEV}" "${TOP}"
btrfs -q subvolume create "${TOP}/@"
mount -o subvol=@ "${ROOT_DEV}" "${TARGET}"
ROOT_UUID="$(blkid -s UUID -o value "${ROOT_DEV}")"
ESP_UUID="$(blkid -s UUID -o value "${ESP_DEV}")"

# ---------------------------------------------------------------- Système Debian 13 installé
log "debootstrap trixie (${MIRROR})"
debootstrap --variant=minbase \
    --include=systemd-sysv,udev,kmod,util-linux,mount,btrfs-progs,dosfstools,python3,initramfs-tools,ca-certificates \
    trixie "${TARGET}" "${MIRROR}" >"${OUT}/rollback-debootstrap.log" 2>&1 \
    || { tail -n 30 "${OUT}/rollback-debootstrap.log"; exit 1; }
mkdir -p "${TARGET}/boot/efi"
# Hôte sans pilote FAT (certains noyaux de conteneur) : ESP préparée dans un dossier puis copiée dans la
# partition FAT par mtools ; la VM, elle, monte la vraie ESP.
ESP_STAGING=""
if mount "${ESP_DEV}" "${TARGET}/boot/efi" 2>/dev/null; then :; else
    ESP_STAGING="${WORK}/esp"
    mkdir -p "${ESP_STAGING}"
    mount --bind "${ESP_STAGING}" "${TARGET}/boot/efi"
    log "pilote FAT absent sur l'hôte : ESP copiée par mtools"
fi
for fs in dev dev/pts proc sys; do
    mkdir -p "${TARGET}/${fs}"
    mount --bind "/${fs}" "${TARGET}/${fs}"
done
cat >"${TARGET}/etc/fstab" <<EOF
UUID=${ROOT_UUID} / btrfs subvol=/@,noatime 0 0
UUID=${ESP_UUID} /boot/efi vfat umask=0077 0 1
EOF
echo djerba-rollback >"${TARGET}/etc/hostname"
mkdir -p "${TARGET}/etc/default"
cat >"${TARGET}/etc/default/grub" <<'EOF'
GRUB_DEFAULT=0
GRUB_TIMEOUT=1
GRUB_DISTRIBUTOR="Djerba OS (test)"
GRUB_CMDLINE_LINUX_DEFAULT="console=ttyS0,115200n8 panic=5"
GRUB_CMDLINE_LINUX=""
GRUB_TERMINAL="console serial"
GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"
GRUB_DISABLE_OS_PROBER=true
EOF
log "noyau, GRUB EFI"
chroot "${TARGET}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    linux-image-amd64 grub-efi-amd64-bin grub2-common >"${OUT}/rollback-apt.log" 2>&1 \
    || { tail -n 30 "${OUT}/rollback-apt.log"; exit 1; }

# ---------------------------------------------------------------- Fichiers du paquet djerba-tools
log "rollback de Djerba (fichiers du paquet djerba-tools)"
mkdir -p "${TARGET}/usr/lib/python3/dist-packages" "${TARGET}/usr/libexec" "${TARGET}/etc/grub.d"
cp -a "${PACKAGE}/usr/lib/python3/dist-packages/djerba_ai" "${TARGET}/usr/lib/python3/dist-packages/"
install -m 0755 "${PACKAGE}/usr/libexec/djerba-boot" "${TARGET}/usr/libexec/djerba-boot"
install -m 0755 "${PACKAGE}/etc/grub.d/09_djerba_bootcount" "${PACKAGE}/etc/grub.d/42_djerba_rollback" \
    "${TARGET}/etc/grub.d/"
install -m 0644 "${PACKAGE}/usr/lib/systemd/system/djerba-boot-ok.service" \
    "${PACKAGE}/usr/lib/systemd/system/djerba-boot-ok.timer" "${TARGET}/usr/lib/systemd/system/"

# Pilote du test : chaque démarrage annonce sur la console série où il a démarré et agit en conséquence.
cat >"${TARGET}/usr/local/sbin/djerba-test-etape" <<'EOF'
#!/bin/sh
set -u
dire() { echo "DJERBA-TEST: $*" >/dev/ttyS0; }
systemctl is-system-running --wait >/dev/null 2>&1
racine="$(findmnt -n -o FSROOT /)"
dire "démarré depuis ${racine} ; $(/usr/libexec/djerba-boot status)"
if [ "${racine}" = "/@djerba-last-good" ]; then
    systemctl start djerba-boot-ok.service
    dire "$(/usr/libexec/djerba-boot status)"
    dire "ROLLBACK-REUSSI"
    systemctl poweroff
    exit 0
fi
if [ ! -e /var/lib/djerba-test/etape1 ]; then
    systemctl start djerba-boot-ok.service        # unité réelle : clone, compteur, entrée GRUB
    if ! grep -q "djerba-last-good" /boot/grub/grub.cfg; then
        dire "ECHEC : entrée de restauration absente de grub.cfg"
        systemctl poweroff
        exit 1
    fi
    mkdir -p /var/lib/djerba-test
    touch /var/lib/djerba-test/etape1
    # Panne : l'init de @ (et seulement de @) s'arrête aussitôt -> panique noyau.
    rm -f /usr/sbin/init
    printf '#!/bin/sh\necho "DJERBA-TEST: INIT-CASSE" > /dev/console\nexit 1\n' >/usr/sbin/init
    chmod 0755 /usr/sbin/init
    sync
    dire "ETAPE1-OK : dernier bon état enregistré, @ cassé, redémarrage"
    systemctl reboot
    exit 0
fi
dire "ECHEC : @ cassé a démarré"
systemctl poweroff
EOF
chmod 0755 "${TARGET}/usr/local/sbin/djerba-test-etape"
cat >"${TARGET}/etc/systemd/system/djerba-test-etape.service" <<'EOF'
[Unit]
Description=Djerba : pilote du test de rollback au démarrage
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/local/sbin/djerba-test-etape

[Install]
WantedBy=multi-user.target
EOF
chroot "${TARGET}" systemctl enable djerba-test-etape.service >/dev/null 2>&1
chroot "${TARGET}" systemctl mask getty@tty1.service serial-getty@ttyS0.service >/dev/null 2>&1 || true

if [[ -z "${ESP_STAGING}" ]]; then
    chroot "${TARGET}" grub-install --target=x86_64-efi --efi-directory=/boot/efi --boot-directory=/boot \
        --removable --no-nvram >"${OUT}/rollback-grub.log" 2>&1 || { cat "${OUT}/rollback-grub.log"; exit 1; }
else
    # Ce que fait « grub-install --removable » (qui exige une ESP FAT montée) : modules sous /boot/grub,
    # image EFI amorçable dans EFI/BOOT/BOOTX64.EFI qui trouve /boot/grub par l'UUID du Btrfs.
    mkdir -p "${TARGET}/boot/grub/x86_64-efi" "${ESP_STAGING}/EFI/BOOT"
    cp "${TARGET}"/usr/lib/grub/x86_64-efi/*.mod "${TARGET}"/usr/lib/grub/x86_64-efi/*.lst \
        "${TARGET}/boot/grub/x86_64-efi/"
    # shellcheck disable=SC2016  # $root est une variable de GRUB, pas du shell
    printf 'search.fs_uuid %s root\nset prefix=($root)/@/boot/grub\n' "${ROOT_UUID}" >"${TARGET}/tmp/load.cfg"
    chroot "${TARGET}" grub-mkimage -O x86_64-efi -o /boot/efi/EFI/BOOT/BOOTX64.EFI -c /tmp/load.cfg \
        -p /@/boot/grub part_gpt btrfs fat search search_fs_uuid normal configfile \
        >"${OUT}/rollback-grub.log" 2>&1 || { cat "${OUT}/rollback-grub.log"; exit 1; }
fi
if [[ -n "${ESP_STAGING}" ]]; then
    mcopy -s -o -i "${ESP_DEV}" "${ESP_STAGING}"/* ::/
    mdir -/ -i "${ESP_DEV}" ::/EFI/BOOT | grep -qi "BOOTX64" || { echo "[rollback] ESP sans BOOTX64.EFI" >&2; exit 1; }
fi
# Sans udev (conteneur), /dev/disk/by-uuid est vide : 10_linux écrirait alors root=/dev/loopNpM, absent de la
# VM. Le lien attendu est créé (dans /dev du conteneur, lié dans le chroot) pour obtenir root=UUID=….
mkdir -p /dev/disk/by-uuid
ln -sfn "${ROOT_DEV}" "/dev/disk/by-uuid/${ROOT_UUID}"
if [[ ${LOOP_PARTITIONS} -eq 1 ]]; then
    # Racine sur un loop entier : 10_linux de GRUB l'écarte (« loop-mounted from files ») ; on lui donne le
    # périphérique qu'aura la VM (lu après la détection ; root= reste l'UUID détecté). Retiré ensuite.
    mkdir -p "${TARGET}/etc/default/grub.d"
    echo 'GRUB_DEVICE=/dev/vda2' >"${TARGET}/etc/default/grub.d/99-djerba-test.cfg"
fi
chroot "${TARGET}" update-grub >>"${OUT}/rollback-grub.log" 2>&1 || { cat "${OUT}/rollback-grub.log"; exit 1; }
rm -f "${TARGET}/etc/default/grub.d/99-djerba-test.cfg"
if ! grep -q "root=UUID=${ROOT_UUID}" "${TARGET}/boot/grub/grub.cfg"; then
    echo "[rollback] grub.cfg sans root=UUID=${ROOT_UUID} :" >&2
    grep -n "linux\s" "${TARGET}/boot/grub/grub.cfg" >&2 || true
    exit 1
fi
if ! grep -q "subvol=/@\|subvol=@" "${TARGET}/boot/grub/grub.cfg"; then
    echo "[rollback] grub.cfg sans racine Btrfs @ ; diagnostic de 10_linux :" >&2
    # shellcheck disable=SC2016  # développé dans le chroot
    chroot "${TARGET}" sh -c 'GRUB_DEVICE="$(grub-probe --target=device /)"; echo "périphérique : ${GRUB_DEVICE}";
        grub-probe --target=fs_uuid / ; export pkgdatadir=/usr/share/grub GRUB_DEVICE GRUB_DEVICE_UUID="$(grub-probe --target=fs_uuid /)" GRUB_FS=btrfs; sh -x /etc/grub.d/10_linux' 2>&1 | tail -n 40 >&2
    exit 1
fi
if grep -q "djerba-last-good" "${TARGET}/boot/grub/grub.cfg"; then
    echo "[rollback] entrée de restauration présente avant tout démarrage réussi" >&2
    exit 1
fi

# Processus restés dans le chroot (démons lancés par les scripts des paquets) : arrêtés, sinon la racine
# reste occupée.
for link in /proc/[0-9]*/root; do
    if [[ "$(readlink "${link}" 2>/dev/null)" == "${TARGET}" ]]; then
        pid="${link#/proc/}"; pid="${pid%/root}"
        log "processus resté dans le chroot : ${pid} ($(tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null))"
        kill -9 "${pid}" 2>/dev/null || true
    fi
done
sleep 1
# Pseudo-systèmes de l'hôte : démontage différé (hors de l'image) ; ESP et Btrfs : démontage complet
# obligatoire avant que QEMU n'écrive sur le disque.
for mountpoint in "${TARGET}/dev/pts" "${TARGET}/dev" "${TARGET}/proc" "${TARGET}/sys"; do
    umount -l "${mountpoint}"
done
sync
busy() {
    echo "[rollback] $1 occupé ; sous-montages et fichiers ouverts :" >&2
    findmnt -R "$1" >&2 || true
    for item in /proc/[0-9]*/{cwd,root,exe} /proc/[0-9]*/fd/*; do
        target="$(readlink "${item}" 2>/dev/null)" || continue
        case "${target}" in "$1"*) echo "  ${item} -> ${target}" >&2 ;; esac
    done
    exit 1
}
umount "${TARGET}/boot/efi" || busy "${TARGET}/boot/efi"
umount -R "${TARGET}" || busy "${TARGET}"
umount "${TOP}" || busy "${TOP}"
detach

# ---------------------------------------------------------------- Démarrages dans QEMU
ACCEL="tcg"; CPU="max"
if [[ -r /dev/kvm && -w /dev/kvm ]]; then ACCEL="kvm"; CPU="host"; fi
cp /usr/share/OVMF/OVMF_VARS_4M.fd "${WORK}/vars.fd"
log "QEMU (${ACCEL}) : démarrages successifs, console série dans ${SERIAL}"
: >"${SERIAL}"
status=0
timeout "${TIMEOUT}" qemu-system-x86_64 -machine q35,accel="${ACCEL}" -cpu "${CPU}" -m 1536 -smp 2 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file="${WORK}/vars.fd" \
    -drive file="${IMAGE}",format=raw,if=virtio \
    -display none -serial file:"${SERIAL}" -monitor none -no-user-config || status=$?
[[ ${status} -eq 124 ]] && log "délai dépassé (${TIMEOUT} s)"

# ---------------------------------------------------------------- Vérification de la séquence
text="$(tr -d '\r' <"${SERIAL}")"
expect() {
    if grep -qF -- "$1" <<<"${text}"; then log "vu : $1"; else
        echo "[rollback] ÉCHEC : « $1 » absent de la console série" >&2
        grep -a "DJERBA-TEST" <<<"${text}" >&2 || true
        tail -n 60 <<<"${text}" >&2
        exit 1
    fi
}
expect "DJERBA-TEST: démarré depuis /@ ;"
expect "ETAPE1-OK"
[[ "$(grep -c "DJERBA-TEST: INIT-CASSE" <<<"${text}")" -eq 2 ]] \
    || { echo "[rollback] ÉCHEC : deux démarrages cassés attendus avant la restauration" >&2
         grep -a "DJERBA-TEST" <<<"${text}" >&2; exit 1; }
expect "DJERBA-TEST: démarré depuis /@djerba-last-good"
expect "sur le dernier bon état : True"
expect "ROLLBACK-REUSSI"
if grep -qF "ECHEC" <<<"${text}"; then
    echo "[rollback] ÉCHEC signalé par le pilote" >&2
    grep -a "DJERBA-TEST" <<<"${text}" >&2
    exit 1
fi
order="$(grep -aoE "ETAPE1-OK|INIT-CASSE|démarré depuis /@djerba-last-good|ROLLBACK-REUSSI" <<<"${text}" | tr '\n' '|')"
[[ "${order}" == "ETAPE1-OK|INIT-CASSE|INIT-CASSE|démarré depuis /@djerba-last-good|ROLLBACK-REUSSI|" ]] \
    || { echo "[rollback] ÉCHEC : ordre inattendu : ${order}" >&2; exit 1; }
log "rollback au démarrage vérifié : 2 échecs, puis retour automatique au dernier démarrage réussi"
