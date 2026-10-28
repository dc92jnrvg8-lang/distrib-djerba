# SPDX-License-Identifier: GPL-3.0-or-later
"""Moteur de politique de l'agent : l'agent demande, le démon root décide.

Décision **déterministe**, identique pour tous les moteurs d'IA (Ollama
local, Tailscale, Jarvis, API en ligne), prise dans le démon root à partir :

* de la politique versionnée livrée par le paquet
  (``/usr/share/djerba-ai/policy.toml``, lecture seule) : niveau de chaque
  outil, racines autorisées à l'écriture root, plafonds de débit ;
* de règles **en dur** (ce module) que ni la configuration ni l'agent ne
  peuvent changer : chemins interdits (amorçage, ESP, Secure Boot, comptes,
  sudo/polkit/PAM, code et politique de l'agent, instantanés, audit,
  journal de transactions) ;
* du **mode** choisi par l'utilisateur (``policy.mode`` : « prudent »,
  « autonome » par défaut, « total »), modifiable seulement par une
  authentification administrateur (méthode ``policy.set_mode``) ;
* de l'**origine** de la demande : session « tainted » (contenu non fiable
  lu pendant la tâche : page web, fichier, sortie d'outil, réponse d'un moteur
  distant) → niveau plafonné à 1 ; plafond éventuel par type de moteur
  (``policy.engine_caps``, ex. ``cloud:1``) ;
* de l'état : disjoncteur (``djerba-ai pause``), plafonds de débit, détection
  de boucle (même action répétée).

Niveaux : 0 lecture (libre) ; 1 réversible et local (autonome, transaction) ;
2 système sensible (autonome, transaction, vérification, rollback,
notification après coup) ; 3 irréversible (jamais en autonomie ; en mode
« total », authentification administrateur à chaque fois).

Mode « prudent » : toute action de niveau ≥ 1 est confirmée **hors bande**
par polkit (fenêtre du système, que le modèle ne peut ni voir ni simuler).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import tomllib
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SHARE = Path(os.environ.get("DJERBA_AI_SHARE", "/usr/share/djerba-ai"))
POLICY_FILE = Path(os.environ.get("DJERBA_POLICY_FILE", str(SHARE / "policy.toml")))
STATE_DIR = Path(os.environ.get("DJERBA_POLICY_STATE", "/var/lib/djerba-ai"))
MODES = ("prudent", "autonome", "total")
DEFAULT_MODE = "autonome"
LEVEL_LABELS = {0: "lecture", 1: "réversible", 2: "système sensible", 3: "irréversible"}
#: paramètres d'outil qui désignent un chemin
PATH_PARAMS = ("path", "source", "target", "file", "directory")
#: outils d'écriture root limités aux racines de la liste blanche
ROOT_WRITERS = {"os_sys_file_write", "os_sys_chown", "os_sys_chmod"}
#: interdits en dur, pour tous les outils et tous les modes (après résolution des liens)
FORBIDDEN_TREES = (
    "/boot", "/efi", "/etc/grub.d", "/etc/default/grub", "/etc/secureboot", "/var/lib/shim-signed",
    "/etc/shadow", "/etc/gshadow", "/etc/shadow-", "/etc/gshadow-", "/etc/sudoers", "/etc/sudoers.d",
    "/etc/polkit-1", "/etc/pam.d", "/etc/security", "/etc/djerba", "/etc/apparmor.d",
    "/usr/share/djerba-ai", "/usr/lib/python3/dist-packages/djerba_ai", "/usr/libexec/djerba-ai-sysd",
    "/usr/libexec/djerba-boot", "/usr/libexec/djerba-ai-ids", "/usr/bin/djerba-ai",
    "/usr/share/polkit-1", "/var/lib/djerba-ai", "/var/log/djerba", "/run/djerba-ai", "/.snapshots",
    "/etc/systemd/system/djerba-ai-sysd.service.d", "/etc/systemd/system/djerba-ai-sysd.socket.d",
    "/proc", "/sys", "/dev",
)
#: pseudo-outils des méthodes du démon (même politique que les outils)
METHOD_TOOLS = {"sys.deploy_patch": "patch_deploy", "sys.rollback_patch": "patch_rollback",
                "sys.install_ollama": "ollama_install", "sys.venv_install": "venv_install",
                "config.set": "settings_set", "security.apparmor_load": "apparmor_load",
                "engines.save": "engine_save", "engines.remove": "engine_remove",
                "engines.set_profile": "engine_profile", "engines.pair_jarvis": "jarvis_pair"}


@dataclass
class Decision:
    allowed: bool
    level: int
    reason: str = ""
    #: confirmation hors bande requise (polkit) avant exécution
    confirm: bool = False
    #: authentification administrateur requise (niveau 3, mode « total »)
    admin: bool = False
    #: notification à l'utilisateur après coup (niveau 2)
    notify: bool = False
    mode: str = DEFAULT_MODE
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Origin:
    engine: str = ""
    session: str = ""
    tainted: bool = False

    @classmethod
    def from_params(cls, data: Any) -> "Origin":
        data = data if isinstance(data, dict) else {}
        return cls(str(data.get("engine", "")), str(data.get("session", "")), bool(data.get("tainted", False)))

    @property
    def kind(self) -> str:
        return self.engine.split(":", 1)[0] if self.engine else ""


def _inside(path: str, tree: str) -> bool:
    return path == tree or path.startswith(tree.rstrip("/") + "/")


def resolve(path: str) -> str:
    """Chemin absolu réel (liens symboliques et « .. » résolus, même pour un fichier à créer)."""
    expanded = os.path.expanduser(str(path))
    return os.path.realpath(expanded if os.path.isabs(expanded) else os.path.join(os.path.expanduser("~"), expanded))


def forbidden(path: str) -> str | None:
    real = resolve(path)
    for tree in FORBIDDEN_TREES:
        if _inside(real, tree) or _inside(os.path.normpath(os.path.expanduser(str(path))), tree):
            return tree
    return None


@dataclass
class Rules:
    version: int = 1
    levels: dict[str, int] = field(default_factory=dict)
    root_writable: tuple[str, ...] = ("/etc", "/usr/local", "/opt", "/srv")
    per_minute: int = 20
    per_ten_minutes: int = 60
    repetitions: int = 3

    @classmethod
    def load(cls, path: Path = POLICY_FILE) -> "Rules":
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return cls()  # politique illisible : valeurs prudentes par défaut
        levels = {str(name): max(0, min(3, int(level))) for name, level in (data.get("niveaux") or {}).items()}
        limits = data.get("plafonds") or {}
        roots = tuple(str(item) for item in (data.get("racines") or {}).get("ecriture_root", cls.root_writable))
        return cls(int(data.get("version", 1)), levels, roots, int(limits.get("par_minute", 20)),
                   int(limits.get("par_dix_minutes", 60)), int(limits.get("repetitions", 3)))


class Policy:
    def __init__(self, rules: Rules | None = None, settings: Any = None, state_dir: Path | None = None,
                 clock=time.time) -> None:
        self.rules = rules or Rules.load()
        self._settings = settings
        self.state_dir = state_dir or STATE_DIR
        self.clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, deque] = defaultdict(deque)
        self._global: deque = deque()
        self._repeats: dict[str, deque] = defaultdict(deque)
        self.refusals: deque = deque(maxlen=100)

    # ------------------------------------------------------------ réglages
    def settings(self) -> dict:
        if self._settings is not None:
            return self._settings if isinstance(self._settings, dict) else dict(self._settings.values)
        try:
            from djerba_ai.core.config import Store
            return Store().load()
        except Exception:  # noqa: BLE001 - configuration illisible : valeurs par défaut prudentes
            return {}

    def mode(self) -> str:
        value = str(self.settings().get("policy.mode", DEFAULT_MODE))
        return value if value in MODES else DEFAULT_MODE

    def engine_cap(self, origin: Origin) -> int:
        """Plafond du moteur : réglage administrateur (par identifiant) et profil de capacité mesuré."""
        caps = {}
        for item in self.settings().get("policy.engine_caps", []) or []:
            kind, _sep, level = str(item).partition(":")
            if level.isdigit():
                caps[kind] = int(level)
        configured = caps.get(origin.kind, 3)
        measured = 3
        if origin.kind.startswith("mcp-"):
            # Appelant MCP (Jarvis…) : plafond fixé à l'appairage ; révoqué -> 0 (plus rien au-delà de la lecture).
            try:
                from djerba_ai.core.mcp import ClientStore
                measured = ClientStore().cap(origin.kind[4:])
            except Exception:  # noqa: BLE001
                measured = 0
        elif origin.kind and origin.kind not in ("local", "tailscale", "cloud"):
            try:
                from djerba_ai.core.engines import EngineStore
                measured = EngineStore().measured_cap(origin.kind)
            except Exception:  # noqa: BLE001 - registre illisible : plafond prudent
                measured = 1
        return min(configured, measured)

    @property
    def pause_file(self) -> Path:
        return self.state_dir / "paused"

    def paused(self) -> dict | None:
        try:
            return json.loads(self.pause_file.read_text())
        except (OSError, ValueError):
            return None

    def pause(self, reason: str = "", by: int = 0) -> dict:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state = {"since": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "reason": reason[:200] or "pause manuelle", "by": by}
        self.pause_file.write_text(json.dumps(state, ensure_ascii=False))
        return state

    def resume(self) -> None:
        self.pause_file.unlink(missing_ok=True)

    # ------------------------------------------------------------ niveaux
    def level(self, tool) -> int:
        name = tool if isinstance(tool, str) else tool.name
        if name in self.rules.levels:
            return self.rules.levels[name]
        if isinstance(tool, str):
            return 2
        if tool.requires_root:
            return 2 if tool.mutates else 0
        return 1 if tool.dangerous else 0

    # ------------------------------------------------------------ décision
    def decide(self, tool, params: dict | None = None, origin: Origin | None = None, commit: bool = False) -> Decision:
        """Décision pour une demande ; ``commit`` : l'action sera exécutée (comptée pour les plafonds)."""
        origin = origin or Origin()
        params = params or {}
        name = tool if isinstance(tool, str) else tool.name
        level = self.level(tool)
        mode = self.mode()

        def refuse(reason: str) -> Decision:
            decision = Decision(False, level, reason, mode=mode, label=LEVEL_LABELS[level])
            self.refusals.append({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": name, "level": level,
                                  "engine": origin.engine, "session": origin.session, "reason": reason})
            return decision

        # 1. Interdits en dur : chemins, quel que soit le niveau ou le mode.
        for key in PATH_PARAMS:
            if isinstance(params.get(key), str) and params[key]:
                tree = forbidden(params[key])
                if tree:
                    return refuse(f"chemin protégé ({tree}) : action interdite à l'agent")
        if name in ROOT_WRITERS and isinstance(params.get("path"), str):
            real = resolve(params["path"])
            if not any(_inside(real, root) and real != root for root in self.rules.root_writable):
                return refuse(f"{real} : écriture root permise seulement sous {', '.join(self.rules.root_writable)}")
        if level == 0:
            return Decision(True, 0, mode=mode, label=LEVEL_LABELS[0])
        # 2. Disjoncteur global.
        paused = self.paused()
        if paused:
            return refuse(f"agent en pause depuis {paused.get('since', '?')} ({paused.get('reason', '')}) : "
                          "« djerba-ai resume » pour reprendre")
        # 3. Plafonds : contenu non fiable, moteur, niveau 3.
        if origin.tainted and level > 1:
            return refuse("contenu non fiable lu pendant cette tâche (page web, fichier, réponse d'un moteur "
                          "distant) : actions limitées au niveau 1 jusqu'à la fin de la tâche")
        cap = self.engine_cap(origin)
        if level > cap:
            return refuse(f"moteur « {origin.kind} » limité au niveau {cap} par la politique")
        admin = False
        if level == 3:
            if mode != "total":
                return refuse("action irréversible : jamais en autonomie (mode « total » requis, avec "
                              "authentification administrateur)")
            admin = True
        # 4. Débit et boucles.
        if commit:
            reason = self._account(name, params, origin)
            if reason:
                return refuse(reason)
        return Decision(True, level, mode=mode, label=LEVEL_LABELS[level], admin=admin,
                        confirm=mode == "prudent" or admin, notify=level >= 2)

    def _account(self, name: str, params: dict, origin: Origin) -> str:
        now = self.clock()
        key = hashlib.sha256(json.dumps([name, params], sort_keys=True, default=str).encode()).hexdigest()
        with self._lock:
            session = self._sessions[origin.session or "-"]
            for window, horizon in ((session, 60), (self._global, 600)):
                while window and now - window[0] > horizon:
                    window.popleft()
            repeats = self._repeats[key]
            while repeats and now - repeats[0] > 3600:
                repeats.popleft()
            if len(repeats) >= self.rules.repetitions:
                return (f"boucle détectée : « {name} » avec les mêmes paramètres déjà exécuté {len(repeats)} fois "
                        "en une heure ; arrêt de cette correction")
            if len(session) >= self.rules.per_minute:
                return f"plafond atteint : {self.rules.per_minute} actions par minute pour cette session"
            if len(self._global) >= self.rules.per_ten_minutes:
                return f"plafond atteint : {self.rules.per_ten_minutes} actions en dix minutes"
            session.append(now)
            self._global.append(now)
            repeats.append(now)
        return ""

    def status(self) -> dict:
        return {"mode": self.mode(), "paused": self.paused(), "version": self.rules.version,
                "limits": {"par_minute": self.rules.per_minute, "par_dix_minutes": self.rules.per_ten_minutes,
                           "repetitions": self.rules.repetitions},
                "engine_caps": list(self.settings().get("policy.engine_caps", []) or []),
                "refusals": list(self.refusals)[-30:]}


# ---------------------------------------------------------------- Côté agent
def check(tool, params: dict | None, origin: dict | None, commit: bool = True) -> dict:
    """Demande au démon root la décision pour un outil de l'utilisateur (les outils root sont décidés
    par le démon lui-même à l'exécution). Démon injoignable : décision locale prudente, plafonnée au
    niveau 1 et sans confirmation possible."""
    from djerba_ai.core import ipc
    name = tool if isinstance(tool, str) else tool.name
    try:
        return ipc.call("policy.check", {"tool": name, "params": params or {}, "origin": origin or None,
                                         "commit": commit}, timeout=330)
    except ipc.RPCError as exc:
        if exc.code != ipc.INTERNAL_ERROR:
            return Decision(False, 3, f"refusé par le démon root : {exc}").to_dict()
    decision = Policy().decide(tool, params, Origin.from_params(origin), commit=commit)
    if decision.allowed and decision.level > 1:
        decision.allowed, decision.reason = False, "démon root injoignable : actions limitées au niveau 1"
    elif decision.allowed and decision.confirm:
        decision.allowed, decision.reason = False, "démon root injoignable : confirmation impossible (mode prudent)"
    return decision.to_dict()
