# SPDX-License-Identifier: GPL-3.0-or-later
"""Capacités système de base (« os ») : paquets apt/Flatpak, fichiers et
dossiers, permissions, recherche (par nom et sémantique).

Les variantes « sys_* » et la gestion des paquets s'exécutent uniquement dans
le démon root djerba-ai-sysd (socket /run/djerba-ai/sysd.sock, SO_PEERCRED,
polkit, journal d'audit) ; les autres agissent avec les droits de
l'utilisateur. Les chemins vitaux du système sont toujours refusés.
"""
