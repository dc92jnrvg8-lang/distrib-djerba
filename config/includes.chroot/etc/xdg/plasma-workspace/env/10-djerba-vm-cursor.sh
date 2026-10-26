# shellcheck shell=sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — fragment d'environnement de session Plasma (sourcé par
# startplasma avant le lancement de KWin ; ne pas y activer « set -e »).
#
# Dans les machines virtuelles (VirtualBox, VMware, QEMU/virtio-gpu,
# Hyper-V…), le plan curseur matériel DRM est mal positionné par l'hôte :
# KWin Wayland 5.27 l'indique (« cursor offset issues in virtual
# machines ») et le pointeur disparaît ou se décale dans certaines zones
# de l'écran. Le curseur logiciel de KWin supprime ce défaut ; il reste
# désactivé sur le matériel réel, où le curseur matériel est plus réactif.
if command -v systemd-detect-virt >/dev/null 2>&1 && systemd-detect-virt --vm --quiet; then
    KWIN_FORCE_SW_CURSOR=1
    export KWIN_FORCE_SW_CURSOR
fi
