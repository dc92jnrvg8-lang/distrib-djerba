# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-réparation : surveillance du journal et correctifs en boucle fermée.

1. ``JournalWatcher`` suit le journal système (priorité « err ») et le journal
   de l'utilisateur ; toute erreur ou trace Python d'un outil djerba-* devient
   un incident (dédupliqué par empreinte) et une notification.
2. ``propose_patch`` donne au modèle le script en cause et l'erreur, et lui
   demande le script corrigé complet ; le démon root le vérifie dans le bac à
   sable Git (syntaxe, shellcheck, exécution sans privilèges) et annule tout
   en cas d'échec (git reset --hard).
3. Le déploiement (``sys.deploy_patch``) n'a lieu qu'après accord explicite de
   l'utilisateur (aperçu du diff + mot de passe administrateur), dans
   /usr/local/bin : un retour arrière (``sys.rollback_patch``) rétablit le
   script du paquet.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from djerba_ai.core import ipc
from djerba_ai.core.llm import LLMError
from djerba_ai.core.memory import Memory

SCRIPT = re.compile(r"/usr/(?:local/)?bin/(djerba-[a-z0-9-]+)")
SIGNAL = re.compile(r"Traceback|Error|Erreur|erreur|failed|échec|ne peut|impossible|exception", re.IGNORECASE)


def target_of(entry: dict) -> str:
    message = str(entry.get("MESSAGE", ""))
    match = SCRIPT.search(message)
    if match:
        return f"/usr/bin/{match.group(1)}"
    for key in ("SYSLOG_IDENTIFIER", "_COMM"):
        name = str(entry.get(key, ""))
        if name.startswith("djerba-") and Path(f"/usr/bin/{name}").exists():
            return f"/usr/bin/{name}"
    unit = str(entry.get("_SYSTEMD_UNIT", entry.get("_SYSTEMD_USER_UNIT", "")))
    if unit.startswith("djerba-apps"):
        return "/usr/bin/djerba-apps"
    if unit.startswith("djerba-anonsurf"):
        return "/usr/bin/djerba-anonsurf"
    return ""


def relevant(entry: dict) -> bool:
    identifier = str(entry.get("SYSLOG_IDENTIFIER", entry.get("_COMM", "")))
    unit = str(entry.get("_SYSTEMD_UNIT", entry.get("_SYSTEMD_USER_UNIT", "")))
    message = str(entry.get("MESSAGE", ""))
    ours = identifier.startswith("djerba") or unit.startswith("djerba") or "djerba_ai" in message \
        or bool(SCRIPT.search(message))
    if not ours or identifier == "djerba-ai":
        return False
    try:
        priority = int(entry.get("PRIORITY", 6))
    except (TypeError, ValueError):
        priority = 6
    return priority <= 3 or bool(SIGNAL.search(message))


def fingerprint(target: str, message: str) -> str:
    normalized = re.sub(r"0x[0-9a-f]+|\d+|/tmp/\S+", "#", message.lower())[:300]
    return hashlib.sha1(f"{target}|{normalized}".encode()).hexdigest()


class JournalWatcher(threading.Thread):
    def __init__(self, memory: Memory, on_incident: Callable[[int, dict], None]) -> None:
        super().__init__(daemon=True, name="djerba-journal")
        self.memory = memory
        self.on_incident = on_incident
        self.processes: list[subprocess.Popen] = []

    def _follow(self, argv: list[str]) -> None:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.processes.append(process)
        assert process.stdout is not None
        for line in process.stdout:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not relevant(entry):
                continue
            target = target_of(entry)
            message = str(entry.get("MESSAGE", ""))
            source = str(entry.get("_SYSTEMD_UNIT") or entry.get("_SYSTEMD_USER_UNIT")
                         or entry.get("SYSLOG_IDENTIFIER") or "journal")
            incident_id, new = self.memory.record_incident(fingerprint(target, message), source, target, message)
            if new:
                self.on_incident(incident_id, {"source": source, "target": target, "message": message})

    def run(self) -> None:
        if shutil.which("journalctl") is None:
            return
        system = threading.Thread(target=self._follow, daemon=True,
                                  args=(["journalctl", "-f", "-n", "0", "-o", "json", "-p", "err"],))
        system.start()
        self._follow(["journalctl", "--user", "-f", "-n", "0", "-o", "json"])

    def stop(self) -> None:
        for process in self.processes:
            process.terminate()


CODE_BLOCK = re.compile(r"```(?:bash|sh|python|python3)?\s*\n(.*?)```", re.DOTALL)

PATCH_PROMPT = """Le script {name} de Djerba OS (chemin {target}) a produit cette erreur :

{error}

Voici son contenu complet actuel :

```
{content}
```

Corrige la cause de l'erreur. Contraintes :
- garde exactement la même interface (commandes, options, sorties JSON) ;
- garde « set -euo pipefail » pour un script bash ;
- ne change rien d'autre que le nécessaire ;
- réponds UNIQUEMENT avec le script complet corrigé, dans un seul bloc de code.
"""


def propose_patch(memory: Memory, client, incident_id: int) -> dict:
    """Génère un correctif avec le modèle et le fait vérifier dans le bac à sable root."""
    incident = memory.incident(incident_id)
    if incident is None:
        return {"success": False, "message": "incident introuvable"}
    target = incident.get("target") or ""
    if not target or not Path(target).is_file():
        return {"success": False, "message": "aucun script Djerba identifié pour cet incident"}
    effective = Path("/usr/local/bin") / Path(target).name
    content = (effective if effective.exists() else Path(target)).read_text(errors="replace")
    prompt = PATCH_PROMPT.format(name=Path(target).name, target=target, error=incident["message"][:3000],
                                 content=content[:60000])
    try:
        reply = client.chat([{"role": "system", "content": "Tu es un ingénieur Debian rigoureux."},
                             {"role": "user", "content": prompt}])
    except LLMError as exc:
        return {"success": False, "message": f"moteur d'IA indisponible : {exc}"}
    match = CODE_BLOCK.search(reply["content"])
    if not match:
        return {"success": False, "message": "le modèle n'a pas fourni de script complet"}
    proposal = match.group(1)
    if proposal.strip() == content.strip():
        return {"success": False, "message": "le modèle n'a proposé aucune modification"}
    try:
        result = ipc.call("sys.test_patch", {"target": target, "content": proposal,
                                             "reason": incident["message"].splitlines()[0][:200]})
    except ipc.RPCError as exc:
        return {"success": False, "message": str(exc)}
    status = "correctif prêt" if result.get("success") else "correctif rejeté"
    memory.update_incident(incident_id, status, json.dumps(result, ensure_ascii=False)[:20000])
    result["message"] = ("Correctif vérifié dans le bac à sable : prêt à être déployé." if result.get("success")
                         else "Le correctif n'a pas passé les vérifications : il a été annulé (git reset --hard).")
    return result
