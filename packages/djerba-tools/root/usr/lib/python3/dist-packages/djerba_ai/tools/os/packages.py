# SPDX-License-Identifier: GPL-3.0-or-later
"""Paquets : apt (Debian) et Flatpak — installation, suppression, purge, mise à jour."""

from __future__ import annotations

import re
import shutil

from djerba_ai.tools.base import FLATPAK_REF, PACKAGE, ToolError, clip, fail, function_tool, ok, run_command

APT_ENV = {"DEBIAN_FRONTEND": "noninteractive", "APT_LISTCHANGES_FRONTEND": "none"}
APT = ["apt-get", "-y", "-o", "DPkg::Lock::Timeout=300", "-o", "Dpkg::Options::=--force-confdef",
       "-o", "Dpkg::Options::=--force-confold"]
MANAGER = {"type": "string", "enum": ["apt", "flatpak"], "default": "apt",
           "description": "apt (paquet Debian) ou flatpak (application Flathub)"}
NAME = {"type": "string", "description": "nom du paquet Debian ou identifiant Flatpak (org.exemple.App)",
        "pattern": rf"{PACKAGE}|{FLATPAK_REF}"}


def _check(manager: str, name: str) -> None:
    if manager == "apt" and not re.fullmatch(PACKAGE, name):
        raise ToolError("nom de paquet Debian invalide")
    if manager == "flatpak" and not re.fullmatch(FLATPAK_REF, name):
        raise ToolError("identifiant Flatpak invalide (ex. org.mozilla.firefox)")
    if shutil.which(manager if manager == "flatpak" else "apt-get") is None:
        raise ToolError(f"{manager} n'est pas installé")


def _result(process, success: str, data: dict) -> dict:
    data.update(exit_code=process.returncode, output=clip(process.stdout or "", 3000))
    if process.returncode != 0:
        errors = (process.stderr or "").strip().splitlines()
        return fail(errors[-1] if errors else f"échec (code {process.returncode})", **data)
    return ok(success, **data)


def _action(action: str):
    def handler(params: dict, _verbose: bool) -> dict:
        manager, name = params["manager"], params["name"]
        _check(manager, name)
        if manager == "apt":
            verb = {"install": "install", "remove": "remove", "purge": "purge"}[action]
            argv = [*APT, verb, name]
            env = APT_ENV
        else:
            if action == "install":
                argv = ["flatpak", "install", "-y", "--noninteractive", "--system", "flathub", name]
            else:
                argv = ["flatpak", "uninstall", "-y", "--noninteractive", "--system", name]
                if action == "purge":
                    argv.insert(-1, "--delete-data")
            env = {}
        process = run_command(argv, timeout=1800, env=env)
        verbs = {"install": "installé", "remove": "supprimé", "purge": "supprimé avec sa configuration"}
        return _result(process, f"{name} {verbs[action]} ({manager}).", {"manager": manager, "package": name})
    return handler


def _update(params: dict, _verbose: bool) -> dict:
    manager, upgrade = params["manager"], params["upgrade"]
    if manager == "apt":
        process = run_command([*APT, "update"], timeout=900, env=APT_ENV)
        if process.returncode != 0 or not upgrade:
            return _result(process, "Index des paquets mis à jour.", {"manager": "apt"})
        process = run_command([*APT, "full-upgrade"], timeout=3600, env=APT_ENV)
        return _result(process, "Système mis à jour (apt full-upgrade).", {"manager": "apt"})
    if shutil.which("flatpak") is None:
        return fail("flatpak n'est pas installé")
    argv = ["flatpak", "update", "-y", "--noninteractive", "--system"]
    if not upgrade:
        argv.append("--appstream")
    process = run_command(argv, timeout=3600)
    return _result(process, "Applications Flatpak mises à jour." if upgrade else "Catalogue Flatpak rafraîchi.",
                   {"manager": "flatpak"})


C = "os"
TOOLS = [
    function_tool("os_pkg_install", C, "Installe un paquet (apt) ou une application (Flatpak).", _action("install"),
                  parameters={"manager": MANAGER, "name": NAME}, required=("name",), requires_root=True,
                  keywords=("installer", "install", "apt", "flatpak", "paquet", "logiciel")),
    function_tool("os_pkg_remove", C, "Supprime un paquet (apt) ou une application (Flatpak).", _action("remove"),
                  parameters={"manager": MANAGER, "name": NAME}, required=("name",), requires_root=True,
                  dangerous=True, keywords=("supprimer", "désinstaller", "remove", "uninstall")),
    function_tool("os_pkg_purge", C, "Supprime un paquet et sa configuration (apt purge / Flatpak --delete-data).",
                  _action("purge"), parameters={"manager": MANAGER, "name": NAME}, required=("name",),
                  requires_root=True, dangerous=True, keywords=("purger", "purge", "configuration")),
    function_tool("os_pkg_update", C, "Rafraîchit les index (apt update / Flatpak) et, si demandé, met tout à jour.",
                  _update, parameters={"manager": MANAGER,
                                       "upgrade": {"type": "boolean", "default": False,
                                                   "description": "installer aussi les mises à jour"}},
                  requires_root=True, keywords=("mettre à jour", "update", "upgrade", "mises à jour")),
]
