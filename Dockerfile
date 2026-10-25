# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — environnement de construction reproductible (x86_64 et arm64).
#
# live-build : Ubuntu 24.04 (et jusqu'à 26.04) ne fournit que live-build
# 3.0~a57, une branche figée en 2013 qui ne sait pas produire d'image EFI
# (aucun binary_grub-efi). La version Debian moderne (arch: all, dépend
# seulement de cpio et debootstrap) est donc installée par APT depuis Debian
# trixie avec un épinglage strict : seul le paquet live-build peut provenir
# de ce dépôt, tout le reste reste Ubuntu 24.04. Aucun pip (PEP 668).
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Dépôt Debian trixie restreint au seul paquet live-build.
RUN set -eux; \
    until apt-get update; do echo "apt-get update a échoué, nouvel essai dans 5 s"; sleep 5; done; \
    until apt-get install -y --no-install-recommends ca-certificates debian-archive-keyring; do sleep 5; done; \
    printf '%s\n' \
        'Types: deb' \
        'URIs: http://deb.debian.org/debian' \
        'Suites: trixie' \
        'Components: main' \
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
        > /etc/apt/sources.list.d/debian-trixie-live-build.sources; \
    printf '%s\n' \
        'Package: *' \
        'Pin: release n=trixie' \
        'Pin-Priority: -1' \
        '' \
        'Package: live-build' \
        'Pin: release n=trixie' \
        'Pin-Priority: 990' \
        > /etc/apt/preferences.d/99-debian-live-build

# Chaîne de construction + générateur d'assets + bancs de test QEMU.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
        amd64) arch_pkgs="grub-efi-amd64-signed grub-efi-amd64-bin grub-pc-bin shim-signed syslinux-utils isolinux qemu-system-x86" ;; \
        arm64) arch_pkgs="grub-efi-arm64-signed grub-efi-arm64-bin shim-signed qemu-system-arm" ;; \
        *) echo "Architecture non supportée : ${arch}" >&2; exit 1 ;; \
    esac; \
    until apt-get update; do echo "apt-get update a échoué, nouvel essai dans 5 s"; sleep 5; done; \
    until apt-get install -y --no-install-recommends \
        live-build debootstrap ubuntu-keyring cpio rsync \
        xorriso squashfs-tools mtools dosfstools \
        grub-common grub2-common \
        ${arch_pkgs} \
        ovmf qemu-efi-aarch64 qemu-utils ipxe-qemu \
        python3 python3-yaml python3-jinja2 python3-pil python3-cairo python3-cairosvg \
        python3-pyqt6 python3-pyqt6.qtquick python3-pyqt6.qtsvg python3-dbus \
        qml6-module-qtquick qml6-module-qtquick-window qml6-module-qtquick-layouts \
        qml6-module-qtquick-controls qml6-module-qtquick-templates \
        qml6-module-qtquick-particles qml6-module-qtquick-shapes \
        qml6-module-qt5compat-graphicaleffects qml6-module-qtqml-workerscript \
        libqt6svg6 qt6-qpa-plugins fonts-inter fonts-dejavu-core librsvg2-bin \
        x11-xserver-utils \
        shellcheck sudo git curl file \
    ; do echo "apt-get install a échoué, nouvel essai dans 5 s"; sleep 5; done; \
    test "$(dpkg-query -W -f='${Version}' live-build | cut -d: -f1)" = "1"; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

RUN git config --global --add safe.directory '*'

WORKDIR /build

CMD ["./build.sh"]
