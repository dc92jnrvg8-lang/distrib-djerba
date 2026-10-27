# SPDX-License-Identifier: GPL-3.0-or-later
"""Applications : store djerba-apps, Flatpak, APT, multiarch i386, Proton."""

from __future__ import annotations

import json
import re

from djerba_ai.tools.base import (FLATPAK_REF, PACKAGE, command_tool, fail, function_tool, launch_detached, ok,
                                  run_command)

C = "apps"
APP_ID = r"^[a-z0-9][a-z0-9-]{0,40}$"
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive", "APT_LISTCHANGES_FRONTEND": "none"}


def _catalog(params: dict, _verbose: bool) -> dict:
    result = run_command(["djerba-apps", "list", "--json"], timeout=60)
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return fail("catalogue illisible")
    apps = [app for app in data["apps"] if app.get("supported")]
    if params.get("category"):
        apps = [app for app in apps if app["category"] == params["category"]]
    rows = [{"id": a["id"], "name": a["name"], "installed": a["installed"], "pending": a["pending"],
             "source": a["backend"], "description": a["description"]} for a in apps]
    installed = sum(1 for row in rows if row["installed"])
    return ok(f"{len(rows)} application(s) au catalogue, {installed} installée(s)", apps=rows)


def _store(action: str):
    def handler(params: dict, _verbose: bool) -> dict:
        ids = [item for item in re.split(r"[\s,]+", params["ids"]) if item]
        if not ids or any(not re.fullmatch(APP_ID, item) for item in ids):
            return fail("identifiants invalides (voir apps_catalog)")
        result = run_command(["djerba-apps", action, *ids, "--json-progress"], timeout=7200)
        events = []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") in ("finish", "complete", "error"):
                events.append(event)
        last = events[-1]["message"] if events else (result.stderr.strip() or "terminé")
        return (ok if result.returncode == 0 else fail)(last, events=events)
    return handler


def _multilib(_params: dict, _verbose: bool) -> dict:
    result = run_command(["dpkg", "--print-foreign-architectures"], timeout=10)
    arches = result.stdout.split()
    return ok("Multiarch i386 actif" if "i386" in arches else "Multiarch i386 inactif (activé automatiquement "
              "à l'installation de Steam ou Wine)", foreign=arches)


def _enable_i386(_params: dict, _verbose: bool) -> dict:
    if run_command(["dpkg", "--add-architecture", "i386"], timeout=30).returncode != 0:
        return fail("activation de i386 impossible")
    update = run_command(["apt-get", "update", "-o", "DPkg::Lock::Timeout=300"], timeout=900, env=APT_ENV)
    if update.returncode != 0:
        return fail("i386 activé, mais la mise à jour des listes a échoué")
    return ok("Architecture i386 activée (bibliothèques 32 bits pour Steam et Wine).")


def _default_apps(_params: dict, _verbose: bool) -> dict:
    browser = run_command(["xdg-settings", "get", "default-web-browser"], timeout=10).stdout.strip()
    mail = run_command(["xdg-mime", "query", "default", "x-scheme-handler/mailto"], timeout=10).stdout.strip()
    pdf = run_command(["xdg-mime", "query", "default", "application/pdf"], timeout=10).stdout.strip()
    return ok(f"Navigateur : {browser or '—'}", browser=browser, mail=mail, pdf=pdf)


def _apt_history(params: dict, _verbose: bool) -> dict:
    try:
        text = open("/var/log/apt/history.log", encoding="utf-8", errors="replace").read()
    except OSError:
        return fail("historique APT illisible")
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    entries = []
    for block in blocks[-params["count"]:]:
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        entries.append({"date": fields.get("Start-Date", ""), "command": fields.get("Commandline", ""),
                        "install": fields.get("Install", "")[:300], "remove": fields.get("Remove", "")[:300]})
    return ok(f"{len(entries)} opération(s) APT récente(s)", entries=entries)


def _launcher(app: str):
    return lambda _params, _verbose: _store("install")({"ids": app}, _verbose)


TOOLS = [
    function_tool("apps_catalog", C, "Catalogue Djerba OS (Steam, Lutris, Heroic, VLC…) et état d'installation.",
                  _catalog,
                  parameters={"category": {"type": "string", "description": "catégorie",
                                           "enum": ["gaming", "multimedia", "office", "security"]}},
                  keywords=("applications", "logiciels", "catalogue", "store", "installer"),
                  binaries=("djerba-apps",)),
    function_tool("apps_install", C, "Installe des applications du catalogue Djerba OS (identifiants séparés par des virgules).",
                  _store("install"),
                  parameters={"ids": {"type": "string", "description": "ex. steam,lutris",
                                      "pattern": r"^[a-z0-9, -]{2,300}$"}},
                  required=("ids",), requires_root=True, dangerous=True,
                  keywords=("installer", "install", "télécharger logiciel"), binaries=("djerba-apps",)),
    function_tool("apps_remove", C, "Supprime des applications du catalogue Djerba OS.", _store("remove"),
                  parameters={"ids": {"type": "string", "description": "ex. vlc",
                                      "pattern": r"^[a-z0-9, -]{2,300}$"}},
                  required=("ids",), requires_root=True, dangerous=True,
                  keywords=("désinstaller", "supprimer application"), binaries=("djerba-apps",)),
    command_tool("apps_status", C, "Applications en attente d'installation et dernier résultat.",
                 ["djerba-apps", "status", "--json"], keywords=("en attente", "file d'attente", "premier démarrage"),
                 done="État du store."),
    command_tool("apps_retry_pending", C, "Relance l'installation des applications en attente.",
                 ["systemctl", "start", "--no-block", "djerba-apps-firstboot.service"], requires_root=True,
                 keywords=("réessayer", "en attente"), done="Installation des applications en attente relancée."),
    command_tool("flatpak_search", C, "Recherche une application sur Flathub.", ["flatpak", "search", "--columns=name,application,version", "{query}"],
                 parameters={"query": {"type": "string", "description": "mot-clé", "pattern": r"^[\w .+-]{2,60}$"}},
                 required=("query",), timeout=90, keywords=("flathub", "chercher application", "flatpak"),
                 done="Résultats Flathub."),
    command_tool("flatpak_install", C, "Installe une application Flathub (ex. com.discordapp.Discord).",
                 ["flatpak", "install", "--system", "-y", "--noninteractive", "flathub", "{app}"],
                 parameters={"app": {"type": "string", "description": "identifiant Flathub", "pattern": FLATPAK_REF}},
                 required=("app",), requires_root=True, dangerous=True, timeout=3600,
                 keywords=("flathub", "installer flatpak"), done="{app} installé."),
    command_tool("flatpak_remove", C, "Supprime une application Flatpak.",
                 ["flatpak", "uninstall", "--system", "-y", "--noninteractive", "{app}"],
                 parameters={"app": {"type": "string", "description": "identifiant", "pattern": FLATPAK_REF}},
                 required=("app",), requires_root=True, dangerous=True, timeout=900,
                 keywords=("désinstaller flatpak",), done="{app} supprimé."),
    command_tool("flatpak_list", C, "Applications Flatpak installées.",
                 ["flatpak", "list", "--app", "--columns=name,application,version,installation"],
                 keywords=("flatpak installés", "applications flathub"), done="Applications Flatpak."),
    command_tool("flatpak_remotes", C, "Dépôts Flatpak configurés (Flathub).", ["flatpak", "remotes", "--show-details"],
                 keywords=("flathub", "dépôt flatpak"), done="Dépôts Flatpak."),
    command_tool("apt_install", C, "Installe un paquet Debian.",
                 ["apt-get", "install", "-y", "-o", "DPkg::Lock::Timeout=300", "{package}"],
                 parameters={"package": {"type": "string", "description": "paquet", "pattern": PACKAGE}},
                 required=("package",), requires_root=True, dangerous=True, timeout=3600, env=APT_ENV,
                 keywords=("installer paquet", "apt install"), done="{package} installé."),
    command_tool("apt_remove", C, "Supprime un paquet Debian.",
                 ["apt-get", "remove", "-y", "-o", "DPkg::Lock::Timeout=300", "{package}"],
                 parameters={"package": {"type": "string", "description": "paquet", "pattern": PACKAGE}},
                 required=("package",), requires_root=True, dangerous=True, timeout=1800, env=APT_ENV,
                 keywords=("supprimer paquet", "apt remove"), done="{package} supprimé."),
    function_tool("multilib_status", C, "Indique si les bibliothèques 32 bits (i386) sont activées.", _multilib,
                  keywords=("i386", "32 bits", "multiarch", "multilib"), binaries=("dpkg",)),
    function_tool("multilib_enable", C, "Active l'architecture i386 (Steam, Wine 32 bits).", _enable_i386,
                  requires_root=True, keywords=("i386", "32 bits", "multilib", "steam", "wine"),
                  binaries=("dpkg", "apt-get")),
    function_tool("steam_install", C, "Installe Steam (multiarch i386 et licence Valve gérés).", _launcher("steam"),
                  requires_root=True, dangerous=True, keywords=("steam", "valve", "jeux"), binaries=("djerba-apps",)),
    function_tool("wine_install", C, "Installe Wine 32 et 64 bits avec winetricks.", _launcher("wine"),
                  requires_root=True, dangerous=True, keywords=("wine", "programmes windows", "exe"),
                  binaries=("djerba-apps",)),
    function_tool("lutris_install", C, "Installe Lutris (GOG, Battle.net, émulateurs).", _launcher("lutris"),
                  requires_root=True, dangerous=True, keywords=("lutris", "battle.net", "gog"),
                  binaries=("djerba-apps",)),
    function_tool("heroic_install", C, "Installe Heroic Games Launcher (Epic, GOG, Amazon).", _launcher("heroic"),
                  requires_root=True, dangerous=True, keywords=("heroic", "epic games", "gog"),
                  binaries=("djerba-apps",)),
    function_tool("open_store", C, "Ouvre le store Djerba OS (page Applications de l'assistant).",
                  lambda _p, _v: ok("Store ouvert.") if launch_detached(["djerba-welcome", "--page", "apps"])
                  else fail("assistant introuvable"), keywords=("store", "boutique", "logithèque"),
                  binaries=("djerba-welcome",)),
    function_tool("open_discover", C, "Ouvre Discover (logithèque KDE, Flathub et mises à jour).",
                  lambda _p, _v: ok("Discover ouvert.") if launch_detached(["plasma-discover"])
                  else fail("Discover n'est pas installé"), keywords=("discover", "logithèque"),
                  binaries=("plasma-discover",)),
    function_tool("default_apps", C, "Navigateur, messagerie et lecteur PDF par défaut.", _default_apps,
                  keywords=("par défaut", "navigateur", "default"), binaries=("xdg-settings", "xdg-mime")),
    command_tool("default_browser_set", C, "Définit le navigateur par défaut (ex. firefox.desktop).",
                 ["xdg-settings", "set", "default-web-browser", "{desktop}"],
                 parameters={"desktop": {"type": "string", "description": "fichier .desktop",
                                         "pattern": r"^[A-Za-z0-9._-]{2,100}\.desktop$"}},
                 required=("desktop",), keywords=("navigateur par défaut",), done="Navigateur par défaut : {desktop}."),
    command_tool("proton_list", C, "Couches Proton installées pour Steam (GE-Proton…).",
                 ["djerba-gamemode", "proton", "list", "--json"], keywords=("proton", "ge-proton", "compatibilité"),
                 done="Couches Proton."),
    command_tool("proton_install_ge", C, "Installe la dernière version de GE-Proton (somme SHA-512 vérifiée).",
                 ["djerba-gamemode", "proton", "install-ge", "--json-progress"], timeout=1800,
                 keywords=("ge-proton", "proton", "jeux windows"), done="GE-Proton installé."),
    function_tool("apt_history", C, "Dernières installations et suppressions de paquets.", _apt_history,
                  parameters={"count": {"type": "integer", "description": "nombre", "default": 10,
                                        "minimum": 1, "maximum": 50}},
                  keywords=("historique", "installé récemment", "apt log")),
    command_tool("package_files", C, "Fichiers installés par un paquet.", ["dpkg", "-L", "{package}"],
                 parameters={"package": {"type": "string", "description": "paquet", "pattern": PACKAGE}},
                 required=("package",), keywords=("fichiers du paquet", "dpkg -L"), done="Fichiers de {package}."),
    command_tool("package_owner", C, "Paquet qui fournit un fichier.", ["dpkg", "-S", "{path}"],
                 parameters={"path": {"type": "string", "description": "chemin absolu",
                                      "pattern": r"^/[^\x00]{1,400}$"}},
                 required=("path",), keywords=("quel paquet", "dpkg -S"), done="Paquet propriétaire."),
]
