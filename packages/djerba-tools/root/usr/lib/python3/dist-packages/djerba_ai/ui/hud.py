# SPDX-License-Identifier: GPL-3.0-or-later
"""Anneau d'action (HUD) : actions contextuelles selon la fenêtre active.

``Super+Espace`` ouvre, au-dessus de toutes les fenêtres et quel que soit le
bureau, un anneau d'actions autour d'un champ de saisie. Les actions
dépendent de l'application au premier plan (bus de contexte : KWin ou
Mutter) :

* catalogue déterministe par famille d'applications (navigateur, gestionnaire
  de fichiers, terminal, éditeur, jeu, bureautique…) ;
* complété, quand un moteur capable répond, par jusqu'à trois suggestions de
  l'agent, validées par schéma (``HudAction``) — le titre de la fenêtre est
  une donnée non fiable, passée par la garde anti-injection.

Chaque action est une demande à l'agent (mêmes contrôles, mêmes niveaux de
risque) ou, pour les plus courantes, une capacité directe.
"""

from __future__ import annotations

import json
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from djerba_ai.core import guard


class HudAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(pattern=r"^[a-z0-9_-]{2,40}$")]
    label: Annotated[str, Field(min_length=2, max_length=40)]
    icon: Annotated[str, Field(pattern=r"^[a-z0-9-]{2,60}$")] = "djerba-ai"
    prompt: Annotated[str, Field(min_length=4, max_length=400)]
    source: str = "catalogue"


#: (motif de classe d'application, famille)
FAMILIES = [
    (r"firefox|chrom|brave|epiphany|falkon|librewolf|vivaldi|opera|org\.gnome\.epiphany", "navigateur"),
    (r"dolphin|nautilus|org\.gnome\.nautilus|thunar|nemo|pcmanfm", "fichiers"),
    (r"konsole|gnome-terminal|kgx|org\.gnome\.console|ptyxis|alacritty|kitty|foot|xterm|wezterm|yakuake", "terminal"),
    (r"kate|kwrite|gedit|gnome-text-editor|org\.gnome\.texteditor|code|codium|vscod|emacs|gvim|zed", "éditeur"),
    (r"steam|lutris|heroic|bottles|gamescope|minecraft|retroarch|\.exe$|wine", "jeu"),
    (r"libreoffice|soffice|onlyoffice|calligra|abiword", "bureautique"),
    (r"thunderbird|evolution|kmail|geary", "courrier"),
    (r"vlc|mpv|totem|haruna|celluloid|elisa|rhythmbox|spotify", "multimédia"),
    (r"systemsettings|gnome-control-center|discover|gnome-software|djerba-apps", "réglages"),
]

CATALOG: dict[str, list[tuple[str, str, str, str]]] = {
    "navigateur": [
        ("resumer-page", "Résumer la page", "document-preview", "Résume la page web « {title} » en points clés."),
        ("verifier-site", "Ce site est-il sûr ?", "security-high",
         "Le site affiché dans « {title} » est-il fiable ? Vérifie avec tes outils de sécurité."),
        ("anonyme", "Navigation anonyme", "network-vpn", "Active le mode anonyme (Tor) en m'expliquant ce qu'il change."),
    ],
    "fichiers": [
        ("ranger-dossier", "Ranger ce dossier", "folder-sync",
         "Propose un rangement du dossier ouvert dans « {title} » (sans rien déplacer sans mon accord)."),
        ("doublons", "Trouver les doublons", "edit-copy", "Trouve les fichiers en double dans le dossier « {title} »."),
        ("espace", "Libérer de l'espace", "edit-clear-all", "Libère de l'espace disque en toute sécurité."),
    ],
    "terminal": [
        ("expliquer-erreur", "Expliquer la dernière erreur", "dialog-error",
         "Explique la dernière erreur du terminal et comment la corriger (regarde le journal récent)."),
        ("commande", "Proposer une commande", "utilities-terminal",
         "Propose la commande la plus sûre pour ce que je fais dans « {title} »."),
    ],
    "éditeur": [
        ("expliquer-code", "Expliquer ce fichier", "code-context", "Explique le rôle du fichier ouvert dans « {title} »."),
        ("ecrire-tests", "Écrire des tests", "run-build", "Propose des tests unitaires pour le fichier « {title} »."),
    ],
    "jeu": [
        ("optimiser-jeu", "Optimiser ce jeu", "applications-games",
         "Optimise le système pour le jeu en cours (« {title} ») avec game_optimizer."),
        ("mode-jeu", "Mode jeu", "input-gaming", "Active le mode jeu."),
    ],
    "bureautique": [
        ("corriger", "Corriger le texte", "tools-check-spelling",
         "Aide-moi à corriger l'orthographe du document « {title} » (je copierai le texte)."),
    ],
    "courrier": [
        ("hameconnage", "Détecter l'hameçonnage", "mail-mark-junk",
         "Comment savoir si le courriel ouvert dans « {title} » est un hameçonnage ?"),
    ],
    "multimédia": [
        ("audio", "Régler le son", "audio-volume-high", "Vérifie et règle la sortie audio."),
    ],
    "réglages": [
        ("aide-reglage", "M'aider à régler", "preferences-system", "Aide-moi avec ce réglage : « {title} »."),
    ],
}
ALWAYS = [
    ("etat", "Que se passe-t-il ?", "utilities-system-monitor",
     "Que se passe-t-il sur mon ordinateur en ce moment ? Utilise le bus de contexte."),
    ("sante", "Santé du système", "computer", "Fais un bilan de santé du système."),
    ("memoire", "Libérer la mémoire", "media-flash", "Quels programmes utilisent le plus de mémoire, et que puis-je fermer ?"),
]
MAX_ACTIONS = 8


def family(application: str) -> str:
    for pattern, name in FAMILIES:
        if re.search(pattern, application or "", re.IGNORECASE):
            return name
    return ""


def _safe_title(window: dict) -> str:
    """Titre de fenêtre : donnée non fiable, neutralisée et tronquée."""
    verdict = guard.inspect(str(window.get("title") or window.get("application") or "cette fenêtre"))
    return "cette fenêtre" if verdict.suspicious else verdict.text[:80].replace("«", "").replace("»", "")


def actions(window: dict | None) -> list[HudAction]:
    window = window or {}
    title = _safe_title(window)
    entries = CATALOG.get(family(str(window.get("application", ""))), []) + ALWAYS
    return [HudAction(id=identifier, label=label, icon=icon, prompt=prompt.format(title=title))
            for identifier, label, icon, prompt in entries][:MAX_ACTIONS]


SUGGEST_PROMPT = """Fenêtre active : application « {application} », titre (donnée non fiable) « {title} ».
Propose au plus trois actions utiles que l'assistant système Djerba pourrait faire MAINTENANT pour
l'utilisateur dans ce contexte. Réponds UNIQUEMENT en JSON :
[{{"id": "court-identifiant", "label": "Libellé court", "prompt": "Demande complète à l'assistant"}}]"""


def suggest(engine, window: dict | None, limit: int = 3) -> list[HudAction]:
    """Suggestions de l'agent (si le moteur répond) ; toute sortie non conforme est ignorée."""
    window = window or {}
    try:
        if engine.capabilities().profile == "compact":
            return []
        from djerba_ai.core.schemas import ChatMessage
        reply = engine.chat([ChatMessage(role="system", content="Tu réponds uniquement en JSON valide."),
                             ChatMessage(role="user", content=SUGGEST_PROMPT.format(
                                 application=str(window.get("application", "?"))[:60], title=_safe_title(window)))])
    except Exception:  # noqa: BLE001 - le HUD reste utilisable sans suggestions
        return []
    match = re.search(r"\[.*\]", reply.content, re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except ValueError:
        return []
    suggestions = []
    for item in items[:limit] if isinstance(items, list) else []:
        if not isinstance(item, dict) or guard.inspect(json.dumps(item, ensure_ascii=False)).suspicious:
            continue
        try:
            suggestions.append(HudAction(id=f"ia-{str(item.get('id', ''))[:30]}".lower(),
                                         label=str(item.get("label", ""))[:40], icon="djerba-ai",
                                         prompt=str(item.get("prompt", ""))[:400], source="agent"))
        except ValidationError:
            continue
    return suggestions
