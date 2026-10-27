# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — environnement de construction reproductible (x86_64 et arm64).
#
# Djerba OS est une distribution dérivée de Debian 13 « trixie » : l'image de
# construction est elle-même une Debian 13, qui fournit nativement live-build
# (1:20250505), debootstrap, les chargeurs signés et l'outillage Qt 6 du
# générateur d'identité visuelle. Uniquement des paquets APT (PEP 668).
FROM debian:trixie

ARG DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Chaîne de construction + paquets Djerba + générateur d'assets + bancs QEMU.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
        amd64) arch_pkgs="grub-efi-amd64-signed grub-efi-amd64-bin grub-pc-bin shim-signed syslinux-utils isolinux qemu-system-x86" ;; \
        arm64) arch_pkgs="grub-efi-arm64-signed grub-efi-arm64-bin shim-signed qemu-system-arm" ;; \
        *) echo "Architecture non supportée : ${arch}" >&2; exit 1 ;; \
    esac; \
    until apt-get update; do echo "apt-get update a échoué, nouvel essai dans 5 s"; sleep 5; done; \
    until apt-get install -y --no-install-recommends \
        live-build debootstrap debian-archive-keyring cpio rsync \
        xorriso squashfs-tools mtools dosfstools \
        grub-common grub2-common \
        ${arch_pkgs} \
        ovmf qemu-efi-aarch64 qemu-utils ipxe-qemu \
        dpkg-dev fakeroot \
        python3 python3-yaml python3-jinja2 python3-pil python3-cairo python3-cairosvg \
        python3-pyqt6 python3-pyqt6.qtquick python3-pyqt6.qtsvg python3-dbus \
        qml6-module-qtquick qml6-module-qtquick-window qml6-module-qtquick-layouts \
        qml6-module-qtquick-controls qml6-module-qtquick-templates \
        qml6-module-qtquick-particles qml6-module-qtquick-shapes \
        qml6-module-qtquick-effects qml6-module-qt5compat-graphicaleffects \
        qml6-module-qtqml-workerscript \
        libqt6svg6 qt6-svg-plugins qt6-qpa-plugins fonts-inter fonts-dejavu-core librsvg2-bin \
        x11-xserver-utils \
        shellcheck sudo git curl file ca-certificates python3-pyflakes \
    ; do echo "apt-get install a échoué, nouvel essai dans 5 s"; sleep 5; done; \
    test "$(dpkg-query -W -f='${Version}' live-build | cut -d: -f1)" = "1"; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

RUN git config --global --add safe.directory '*'

WORKDIR /build

CMD ["./build.sh"]
