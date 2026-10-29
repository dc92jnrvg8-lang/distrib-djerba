# SPDX-License-Identifier: GPL-3.0-or-later
"""Rollback au démarrage (Btrfs + UEFI) : le système revient seul au dernier
démarrage réussi après deux échecs consécutifs.

* **Démarrage réussi** : ``djerba-boot-ok.service`` attend la **fin** du
  démarrage (``systemctl is-system-running --wait``), puis exige : état
  « running » ou « degraded » ; cible par défaut atteinte
  (``graphical.target`` sur le bureau) ; aucune unité critique en échec
  (``dbus``, ``systemd-logind`` ; absentes = ignorées ; le démon de l'agent
  n'en fait pas partie) ; gestionnaire de connexion, s'il est activé, actif
  et **stable** (aucun redémarrage pendant 30 s).
* **Délai maximal** : 15 min (``DJERBA_BOOT_DEADLINE``). Un démarrage lent
  qui aboutit avant n'est pas un échec ; au-delà, il est marqué en échec
  (avis « Démarrage bloqué ») et un arrêt propre ne le rattrape plus ; s'il
  finit par aboutir et qu'il est sain, il est validé normalement.
* **Démarrage raté** : démarrage qui n'atteint pas la validation et ne se
  termine pas par un arrêt propre avant le délai maximal — panique noyau,
  init absent, blocage, mode de secours (``emergency``/``rescue``), unité
  critique ou gestionnaire de connexion en échec. Un **arrêt propre**
  demandé avant la fin du démarrage et avant le délai
  (``djerba-boot-guard.service``, ``ExecStop``, état « stopping ») rend au
  compteur la valeur qu'il avait avant ce démarrage.
* **Générations** : ``@djerba-last-good`` (la plus récente, cible du
  rollback automatique) et jusqu'à 2 plus anciennes ``@djerba-good-<date>``,
  espacées d'au moins 24 h (``DJERBA_BOOT_GENERATION_SPACING``), chacune avec
  son entrée GRUB (choix manuel). Le nouveau clone est fait **avant** de
  remplacer l'ancien : il y a toujours un état de repli.
* **Dernier bon état** : après chaque démarrage réussi, le sous-volume racine
  est cloné en ``@djerba-last-good`` (clone **inscriptible**, coût nul grâce
  au copy-on-write) ; son ``/etc/fstab`` pointe vers lui-même pour « / »
  seulement (``/home`` et les autres sous-volumes ne sont jamais clonés ni
  restaurés) et ses noyau et initrd sont exposés sous des noms stables
  (``/boot/djerba-vmlinuz``, ``/boot/djerba-initrd.img``, copies reflink).
* **Compteur GRUB** : GRUB ne sait pas écrire sur Btrfs ; le compteur
  ``djerba_boot_tries`` vit donc sur l'ESP (FAT), ``EFI/djerba/grubenv``.
  Chaque démarrage normal l'incrémente (0 → 1 → 2), un démarrage réussi le
  remet à 0. À 2 (deux échecs), GRUB démarre l'entrée
  « dernier démarrage réussi ». Tant que le système tourne sur ce clone, le
  compteur reste à 2 (pas de retour automatique sur l'état cassé) et un avis
  est déposé pour la session (``NOTICE``, lu par le démon utilisateur).
* **ESP en lecture seule** : le compteur ne peut pas être remis à zéro ; le
  dernier bon état est tout de même rafraîchi (un rollback éventuel
  reviendrait donc à ce démarrage sain), la commande échoue (unité en échec,
  système « degraded ») et un avis est déposé.
* **grubenv corrompu** : GRUB ne lit ni n'écrit le compteur (aucun rollback,
  démarrage normal) ; à la validation suivante, le bloc est recréé.
* **Rendre définitif** : ``djerba-ai rollback --make-permanent`` (root)
  renomme l'ancien ``@`` en ``@djerba-broken-<date>`` (conservé) et recrée
  ``@`` à partir de la génération sur laquelle le système tourne ; au redémarrage suivant, le système normal est
  l'état restauré.

Non pris en charge (documenté) : racine chiffrée par LUKS (GRUB devrait
déverrouiller le volume), démarrage BIOS (pas d'ESP pour le compteur),
racine non Btrfs. Dans ces cas, rien n'est généré et rien n'est modifié.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from djerba_ai.core.snapshots import BtrfsBackend

LAST_GOOD = "@djerba-last-good"
ENV_RELATIVE = "EFI/djerba/grubenv"
ENTRY_ID = "djerba-last-good"
KERNEL = "boot/djerba-vmlinuz"
INITRD = "boot/djerba-initrd.img"
MARKER = "etc/djerba-last-good"
ESP = Path(os.environ.get("DJERBA_ESP", "/boot/efi"))
RUN = Path("/run/djerba-boot")
# Avis pour la session (lisible par tous, sans secret) : lu par le démon utilisateur, notifié une fois.
NOTICE = Path("/var/lib/djerba-boot/notice.json")
MANAGER_RETRIES = 30
# Générations : @djerba-last-good (la plus récente, cible du rollback automatique) + KEPT_GENERATIONS plus
# anciennes (@djerba-good-<date>, entrées GRUB manuelles), espacées d'au moins GENERATION_SPACING secondes.
GOOD_PREFIX = "@djerba-good-"
KEPT_GENERATIONS = 2
GENERATION_SPACING = 86400
# Délai maximal d'un démarrage : au-delà, il compte comme un échec même s'il finit par un arrêt propre.
BOOT_DEADLINE = 900
# Le gestionnaire de connexion doit rester actif sans redémarrer pendant ce délai (boucle de plantage).
STABILITY_DELAY = 30
# Unités sans lesquelles la session ne s'ouvre pas : en échec = démarrage raté (absentes = ignorées).
# Le démon de l'agent n'en fait pas partie : l'OS fonctionne sans l'agent (invariant 16).
CRITICAL_UNITS = ("dbus.service", "systemd-logind.service")

Runner = Callable[..., subprocess.CompletedProcess]


def _run(argv: list[str], timeout: int | None = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def _version_key(name: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def newest_kernel(root: Path) -> tuple[Path, Path] | None:
    """(noyau, initrd) le plus récent de ``root/boot`` dont l'initrd existe."""
    kernels = sorted((root / "boot").glob("vmlinuz-*"), key=lambda path: _version_key(path.name), reverse=True)
    for kernel in kernels:
        initrd = root / "boot" / f"initrd.img-{kernel.name.removeprefix('vmlinuz-')}"
        if initrd.exists():
            return kernel, initrd
    return None


def point_fstab(root: Path, subvolume: str) -> bool:
    """Ligne de « / » du fstab de ``root`` : ``subvol=`` remplacé par ``subvolume``."""
    fstab = root / "etc" / "fstab"
    try:
        lines = fstab.read_text().splitlines(keepends=True)
    except OSError:
        return False
    changed = False
    for index, line in enumerate(lines):
        fields = line.split()
        if len(fields) >= 4 and not line.lstrip().startswith("#") and fields[1] == "/":
            options = re.sub(r"(^|,)subvol=[^,]*", "", fields[3]).strip(",")
            options = ",".join(item for item in (options, f"subvol=/{subvolume}") if item)
            options = re.sub(r"(^|,)subvolid=\d+", "", options).strip(",")
            lines[index] = line.replace(fields[3], options, 1)
            changed = True
    if changed:
        fstab.write_text("".join(lines))
    return changed


class BootGuard:
    def __init__(self, backend: BtrfsBackend | None = None, esp: Path = ESP, runner: Runner = _run,
                 run_dir: Path = RUN, notice: Path = NOTICE, sleep: Callable[[float], None] = time.sleep,
                 deadline: int | None = None, spacing: int | None = None) -> None:
        self._backend = backend
        self.esp = esp
        self.runner = runner
        self.run_dir = run_dir
        self.notice_path = notice
        self.sleep = sleep
        self.last_error = ""
        self.repaired = False
        self.health = ""
        # Réglables par l'environnement de l'unité (tests) ; valeurs par défaut ci-dessus.
        self.deadline = deadline if deadline is not None else int(os.environ.get("DJERBA_BOOT_DEADLINE",
                                                                                 BOOT_DEADLINE))
        self.spacing = spacing if spacing is not None else int(os.environ.get("DJERBA_BOOT_GENERATION_SPACING",
                                                                              GENERATION_SPACING))

    @property
    def backend(self) -> BtrfsBackend | None:
        if self._backend is None:
            self._backend = BtrfsBackend.detect(runner=self.runner)
        return self._backend

    @property
    def env_file(self) -> Path:
        return self.esp / ENV_RELATIVE

    # ------------------------------------------------------------ compteur
    def env_state(self) -> str:
        """« absent », « corrompu » ou « valide » (bloc d'environnement GRUB sur l'ESP)."""
        if not self.env_file.exists():
            return "absent"
        return "valide" if self.runner(["grub-editenv", str(self.env_file), "list"]).returncode == 0 else "corrompu"

    def tries(self) -> int:
        result = self.runner(["grub-editenv", str(self.env_file), "list"])
        match = re.search(r"^djerba_boot_tries=(\d+)$", result.stdout, re.MULTILINE)
        return int(match.group(1)) if match else 0

    def set_tries(self, value: int) -> bool:
        """Écrit le compteur ; en cas d'échec, ``last_error`` dit pourquoi (ESP absente, lecture seule…)."""
        self.last_error = ""
        if not self.esp.is_dir():
            self.last_error = f"ESP absente ({self.esp})"
            return False
        try:
            self.env_file.parent.mkdir(parents=True, exist_ok=True)
            if self.env_state() == "corrompu":
                # Bloc illisible : GRUB l'ignore (ni compteur ni rollback). Recréé plutôt que réparé.
                self.env_file.unlink()
                self.repaired = True
        except OSError as exc:
            self.last_error = f"{self.env_file} : {exc.strerror or exc}"
            return False
        steps = [["grub-editenv", str(self.env_file), "set", f"djerba_boot_tries={value}"]]
        if not self.env_file.exists():
            steps.insert(0, ["grub-editenv", str(self.env_file), "create"])
        for argv in steps:
            result = self.runner(argv)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()[:200]
                self.last_error = detail or f"grub-editenv {argv[2]} : code {result.returncode}"
                return False
        return True

    # ------------------------------------------------------------ état
    def current_root(self) -> str:
        return self.runner(["findmnt", "-n", "-o", "FSROOT", "/"]).stdout.strip().strip("/")

    def booted_from_last_good(self) -> bool:
        """Le système tourne sur une génération enregistrée (@djerba-last-good ou @djerba-good-*)."""
        name = self.current_root()
        return name == LAST_GOOD or name.startswith(GOOD_PREFIX)

    def unit(self, name: str) -> dict[str, str]:
        result = self.runner(["systemctl", "show", "-p", "LoadState", "-p", "ActiveState", "-p", "NRestarts",
                              name])
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def healthy_boot(self) -> bool:
        """Démarrage réussi : état « running »/« degraded », cible par défaut atteinte (graphical.target sur
        le bureau), aucune unité critique en échec, gestionnaire de connexion actif et stable. ``health`` dit
        pourquoi en cas d'échec."""
        self.health = ""
        state = self.runner(["systemctl", "is-system-running"]).stdout.strip()
        if state not in ("running", "degraded"):
            self.health = f"état du système : {state or 'inconnu'}"
            return False
        target = self.runner(["systemctl", "get-default"]).stdout.strip() or "multi-user.target"
        if self.runner(["systemctl", "is-active", target]).stdout.strip() != "active":
            self.health = f"cible {target} non atteinte"
            return False
        for name in CRITICAL_UNITS:
            info = self.unit(name)
            if info.get("LoadState") == "loaded" and info.get("ActiveState") == "failed":
                self.health = f"{name} en échec"
                return False
        manager = self.runner(["systemctl", "is-enabled", "display-manager.service"]).stdout.strip()
        if manager not in ("enabled", "static", "alias"):
            return True
        for _attempt in range(MANAGER_RETRIES):
            active = self.runner(["systemctl", "is-active", "display-manager.service"]).stdout.strip()
            if active != "activating":
                break
            self.sleep(2)
        if active != "active":
            self.health = f"gestionnaire de connexion : {active}"
            return False
        restarts = self.unit("display-manager.service").get("NRestarts", "0")
        self.sleep(STABILITY_DELAY)
        after = self.unit("display-manager.service")
        if after.get("ActiveState") != "active" or after.get("NRestarts", "0") != restarts:
            self.health = "gestionnaire de connexion instable (redémarrages)"
            return False
        return True

    def notice(self, ident: str, title: str, body: str, root: Path | None = None) -> None:
        """Avis pour la session (dernier événement seulement) ; ``root`` : autre racine que celle en cours."""
        path = self.notice_path if root is None else root / self.notice_path.relative_to(self.notice_path.anchor)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(path.parent, 0o755)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"id": ident, "title": title, "body": body,
                                             "date": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False))
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        except OSError as exc:
            print(f"djerba-boot : avis non enregistré ({exc})", flush=True)

    def _flag(self, name: str) -> None:
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / name).write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"))
        except OSError:
            pass

    # ------------------------------------------------------------ dernier bon état
    def generations(self) -> list[str]:
        """Générations anciennes conservées (@djerba-good-<date>), de la plus récente à la plus ancienne."""
        backend = self.backend
        if backend is None:
            return []
        return sorted((path.name for path in backend.top.glob(f"{GOOD_PREFIX}*")
                       if re.fullmatch(rf"{re.escape(GOOD_PREFIX)}\d{{14}}", path.name)), reverse=True)

    @staticmethod
    def clone_date(root: Path) -> float | None:
        try:
            match = re.search(r"clone du (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", (root / MARKER).read_text())
            return time.mktime(time.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")) if match else None
        except (OSError, ValueError):
            return None

    def _delete(self, path: Path) -> str:
        result = self.runner(["btrfs", "subvolume", "delete", str(path)], timeout=300)
        return "" if result.returncode == 0 else (result.stderr or result.stdout).strip()[:200]

    def refresh_last_good(self, rotate: bool = True) -> str:
        """Nouveau clone d'abord (l'ancien reste la cible tant que le nouveau n'est pas prêt), puis
        l'ancien devient une génération conservée (si assez espacée de la précédente) ou est supprimé."""
        backend = self.backend
        if backend is None:
            return "racine non Btrfs : rien à faire"
        target, fresh = backend.top / LAST_GOOD, backend.top / f"{LAST_GOOD}.nouveau"
        if fresh.exists() and (error := self._delete(fresh)):
            return f"clone inachevé non supprimé : {error}"
        result = self.runner(["btrfs", "subvolume", "snapshot", str(backend.top / backend.source), str(fresh)],
                             timeout=300)
        if result.returncode != 0:
            return f"clone impossible : {result.stderr.strip()[:200]}"
        point_fstab(fresh, LAST_GOOD)
        kernel = newest_kernel(fresh)
        if kernel:
            for source, name in zip(kernel, (KERNEL, INITRD)):
                self.runner(["cp", "--reflink=auto", "-f", str(source), str(fresh / name)], timeout=300)
        (fresh / MARKER).write_text(f"clone du {time.strftime('%Y-%m-%d %H:%M:%S')} de /{backend.source}\n")
        kept = ""
        if target.exists():
            date = self.clone_date(target)
            newest = self.clone_date(backend.top / self.generations()[0]) if self.generations() else None
            if rotate and date is not None and (newest is None or date - newest >= self.spacing):
                kept = f"{GOOD_PREFIX}{time.strftime('%Y%m%d%H%M%S', time.localtime(date))}"
                if (backend.top / kept).exists():
                    kept = ""
                else:
                    os.rename(target, backend.top / kept)
                    point_fstab(backend.top / kept, kept)
            if not kept and (error := self._delete(target)):
                self._delete(fresh)
                return f"suppression de l'ancien clone impossible : {error}"
        os.rename(fresh, target)
        current = self.current_root()
        for name in self.generations()[KEPT_GENERATIONS:]:
            if name != current:
                self._delete(backend.top / name)
        return f"dernier bon état enregistré ({LAST_GOOD})" + (f" ; génération conservée : {kept}" if kept else "")

    def mark_success(self, wait: bool = False) -> tuple[bool, str]:
        """Démarrage réussi : compteur remis à zéro et dernier bon état rafraîchi.

        ``wait`` : attend la fin du démarrage. Au-delà de ``deadline`` secondes, le démarrage est marqué en
        échec (un arrêt propre ne le rattrape plus) ; s'il finit par aboutir, il est jugé normalement.
        """
        if wait:
            result = self.runner(["systemctl", "is-system-running", "--wait"], timeout=self.deadline)
            if result.returncode == 127 and "timed out" in result.stderr:
                self._flag("echec")
                print(f"djerba-boot : démarrage non terminé après {self.deadline} s : compté comme un échec",
                      flush=True)
                self.notice(f"bloque:{time.strftime('%Y-%m-%d %H:%M')}", "Démarrage bloqué",
                            f"Le démarrage n'est pas terminé après {self.deadline // 60} min : il compte comme "
                            "un échec. Après deux échecs, Djerba OS redémarre sur le dernier état sain.")
                self.runner(["systemctl", "is-system-running", "--wait"], timeout=None)
                (self.run_dir / "echec").unlink(missing_ok=True)
        if not self.healthy_boot():
            self._flag("echec")
            return False, f"démarrage incomplet ({self.health}) : compteur conservé"
        if self.booted_from_last_good():
            # Pas de retour automatique sur l'état qui a échoué : l'utilisateur décide.
            counter = self.set_tries(2)
            backend = self.backend
            marker = backend.top / self.current_root() / MARKER if backend else None
            try:
                origin = marker.read_text().strip() if marker else ""
            except OSError:
                origin = ""
            self.notice(f"restauration:{origin}", "Djerba OS a été restauré",
                        "Les deux derniers démarrages n'ont pas abouti : le système a redémarré sur le dernier "
                        f"état sain ({origin or LAST_GOOD}). Vos fichiers personnels (/home) n'ont pas été "
                        "touchés. Pour conserver cet état : « djerba-ai rollback --make-permanent ».")
            message = ("système restauré au dernier démarrage réussi : « djerba-ai rollback --make-permanent » "
                       "pour le conserver")
            self._flag("ok")
            return (True, message) if counter else (False, f"{message} ; compteur non écrit ({self.last_error})")
        counter = self.set_tries(0)
        error = self.last_error
        message = self.refresh_last_good()
        if self.backend and (self.backend.top / LAST_GOOD).exists() and not self.grub_has_entry():
            self.runner(["update-grub"], timeout=600)
            # Le clone reprend le grub.cfg qui contient désormais compteur et entrées (utile après promotion) ;
            # sans nouvelle rotation : la génération conservée vient d'être choisie.
            message = self.refresh_last_good(rotate=False) + message.partition(LAST_GOOD + ")")[2]
        # Drapeau posé une fois tout le travail fait (clone, entrée GRUB) : il signale un démarrage validé.
        self._flag("ok")
        if not counter:
            self.notice(f"esp:{time.strftime('%Y-%m-%d')}", "Compteur de démarrage non remis à zéro",
                        f"La partition EFI n'accepte pas l'écriture ({error}). Le dernier état sain a été "
                        "enregistré ; vérifiez la partition /boot/efi (fsck) ou ouvrez Djerba.")
            return False, f"compteur non remis à zéro ({error}) ; {message}"
        if self.repaired:
            message += " ; bloc d'environnement GRUB corrompu recréé"
        return True, message

    def record_start(self) -> str:
        """Au démarrage : valeur du compteur après l'incrément de GRUB (pour l'arrêt propre)."""
        value = self.tries()
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / "debut").write_text(f"{value}\n")
        except OSError as exc:
            return f"compteur au démarrage non relevé ({exc})"
        return f"compteur au démarrage : {value}"

    def clean_shutdown(self) -> str:
        """Arrêt propre avant la validation : ce démarrage ne compte pas comme un échec."""
        if (self.run_dir / "ok").exists():
            return "démarrage déjà validé"
        if (self.run_dir / "echec").exists():
            return "démarrage en échec : compteur conservé"
        if self.runner(["systemctl", "is-system-running"]).stdout.strip() != "stopping":
            # Arrêt de l'unité sans arrêt du système (bascule en mode de secours, isolate) : c'est un échec.
            return "pas d'arrêt du système en cours : compteur conservé"
        if self.booted_from_last_good():
            return "sur le dernier bon état : compteur conservé"
        try:
            start = int((self.run_dir / "debut").read_text())
        except (OSError, ValueError):
            return "compteur au démarrage inconnu : conservé"
        if start <= 0 or self.tries() != start:
            return "compteur inchangé"
        if not self.set_tries(start - 1):
            return f"compteur non rétabli ({self.last_error})"
        return f"arrêt propre avant la fin du démarrage : compteur ramené à {start - 1}"

    def grub_has_entry(self, config: Path = Path("/boot/grub/grub.cfg")) -> bool:
        """grub.cfg contient exactement les entrées des générations présentes (ni manquante, ni périmée)."""
        try:
            found = set(re.findall(r"--id (djerba-(?:last-good|good-\d{14}))\b", config.read_text(errors="replace")))
        except OSError:
            return False
        return found == {ENTRY_ID} | {name.lstrip("@") for name in self.generations()}

    def normal_root(self) -> str:
        """Sous-volume du système normal. Sur le clone, la racine montée est @djerba-last-good : le nom
        d'origine est celui inscrit dans le marqueur du clone (« clone du … de /@ »)."""
        backend = self.backend
        if backend is None:
            return "@"
        if backend.source != LAST_GOOD and not backend.source.startswith(GOOD_PREFIX):
            return backend.source
        try:
            match = re.search(r" de /(\S+)\s*$", (backend.top / backend.source / MARKER).read_text())
        except OSError:
            match = None
        name = match.group(1) if match else "@"
        return "@" if name == LAST_GOOD or name.startswith(GOOD_PREFIX) else name

    def make_permanent(self, force: bool = False) -> str:
        backend = self.backend
        if backend is None:
            raise RuntimeError("racine non Btrfs")
        if not force and not self.booted_from_last_good():
            raise RuntimeError("le système ne tourne pas sur le dernier bon état")
        name = self.normal_root()
        source = backend.top / name
        if not source.exists() or name == LAST_GOOD or name.startswith(GOOD_PREFIX):
            raise RuntimeError(f"sous-volume normal introuvable ({name})")
        # Génération sur laquelle le système tourne (la plus récente ou une plus ancienne choisie dans GRUB).
        running = self.current_root()
        origin = running if running == LAST_GOOD or running.startswith(GOOD_PREFIX) else LAST_GOOD
        broken = backend.top / f"@djerba-broken-{time.strftime('%Y%m%d%H%M%S')}"
        os.rename(source, broken)
        result = self.runner(["btrfs", "subvolume", "snapshot", str(backend.top / origin), str(source)],
                             timeout=300)
        if result.returncode != 0:
            os.rename(broken, source)
            raise RuntimeError(f"restauration impossible : {result.stderr.strip()[:200]}")
        point_fstab(source, name)
        (source / MARKER).unlink(missing_ok=True)
        self.set_tries(0)
        self.notice(f"definitif:{broken.name}", "État restauré conservé",
                    "Au prochain démarrage, Djerba OS démarre sur l'état restauré. L'ancien système est "
                    f"conservé dans {broken.name}.", root=source)
        return (f"état restauré rendu définitif ; l'ancien système est conservé dans {broken.name} "
                "(à supprimer une fois rassuré). Redémarrez.")

    # ------------------------------------------------------------ GRUB
    def grub_snippet(self, part: str, environ: dict | None = None) -> str:
        """Code GRUB pour /etc/grub.d (``counter`` : 09_djerba_bootcount ; ``entry`` : 42_djerba_rollback)."""
        environ = os.environ if environ is None else environ
        facts = self.facts()
        if facts is None:
            return ""
        if part == "counter":
            return (f"# Djerba OS — compteur de démarrages (sur l'ESP : GRUB n'écrit pas sur Btrfs)\n"
                    f"search --no-floppy --fs-uuid --set=djerba_esp {facts['esp_uuid']}\n"
                    "if [ -n \"${djerba_esp}\" ]; then\n"
                    f"  load_env --file (${{djerba_esp}})/{ENV_RELATIVE} djerba_boot_tries\n"
                    "  if [ \"${djerba_boot_tries}\" = \"2\" ]; then\n"
                    f"    set default=\"{ENTRY_ID}\"\n"
                    "  elif [ \"${djerba_boot_tries}\" = \"1\" ]; then\n"
                    "    set djerba_boot_tries=2\n"
                    f"    save_env --file (${{djerba_esp}})/{ENV_RELATIVE} djerba_boot_tries\n"
                    "  else\n"
                    "    set djerba_boot_tries=1\n"
                    f"    save_env --file (${{djerba_esp}})/{ENV_RELATIVE} djerba_boot_tries\n"
                    "  fi\n"
                    "fi\n")
        options = " ".join(item for item in (environ.get("GRUB_CMDLINE_LINUX", ""),
                                             environ.get("GRUB_CMDLINE_LINUX_DEFAULT", "")) if item)
        entries = [(LAST_GOOD, ENTRY_ID, "dernier démarrage réussi (restauration)")]
        backend = self.backend
        for name in self.generations():
            if backend is not None and (backend.top / name / KERNEL).exists():
                stamp = name.removeprefix(GOOD_PREFIX)
                label = f"état sain du {stamp[6:8]}/{stamp[4:6]}/{stamp[:4]} {stamp[8:10]}:{stamp[10:12]}"
                entries.append((name, name.lstrip("@"), label))
        text = ""
        for subvolume, ident, label in entries:
            text += (f"menuentry 'Djerba OS — {label}' --class djerba --id {ident} {{\n"
                     "  insmod part_gpt\n  insmod btrfs\n"
                     f"  search --no-floppy --fs-uuid --set=root {facts['root_uuid']}\n"
                     f"  echo 'Restauration : {label}…'\n"
                     f"  linux /{subvolume}/{KERNEL} root=UUID={facts['root_uuid']} rootflags=subvol=/{subvolume} ro "
                     f"{options}\n"
                     f"  initrd /{subvolume}/{INITRD}\n"
                     "}\n")
        return text

    def facts(self) -> dict | None:
        """Conditions : racine Btrfs non chiffrée, ESP montée, dernier bon état présent."""
        backend = self.backend
        if backend is None or not (backend.top / LAST_GOOD / KERNEL).exists():
            return None
        root = self.runner(["findmnt", "-n", "-o", "UUID,SOURCE", "/"]).stdout.split()
        esp = self.runner(["findmnt", "-n", "-o", "UUID", str(self.esp)]).stdout.strip()
        if len(root) < 2 or not esp:
            return None
        device = re.sub(r"\[.*\]$", "", root[1])
        kinds = self.runner(["lsblk", "-s", "-n", "-o", "TYPE", device]).stdout.split()
        if "crypt" in kinds:
            return None
        return {"root_uuid": root[0], "esp_uuid": esp}


def main(argv: list[str]) -> int:
    guard = BootGuard()
    command = argv[0] if argv else ""
    if command == "grub-snippet" and len(argv) == 2 and argv[1] in ("counter", "entry"):
        print(guard.grub_snippet(argv[1]), end="")
        return 0
    if os.geteuid() != 0:
        print("djerba-boot : doit s'exécuter en root", flush=True)
        return 1
    if command == "ok":
        success, message = guard.mark_success(wait="--wait" in argv)
        print(f"djerba-boot : {message}", flush=True)
        return 0 if success else 1
    if command == "start":
        print(f"djerba-boot : {guard.record_start()}", flush=True)
        return 0
    if command == "clean-shutdown":
        print(f"djerba-boot : {guard.clean_shutdown()}", flush=True)
        return 0
    if command == "make-permanent":
        try:
            print(guard.make_permanent(force="--force" in argv))
        except (RuntimeError, OSError) as exc:
            print(f"djerba-boot : {exc}", flush=True)
            return 1
        return 0
    if command == "status":
        print(f"compteur : {guard.tries()} (grubenv {guard.env_state()}) ; "
              f"sur le dernier bon état : {guard.booted_from_last_good()} ; "
              f"générations : {', '.join([LAST_GOOD, *guard.generations()])}")
        return 0
    print("Usage : djerba-boot ok [--wait] | start | clean-shutdown | status | make-permanent [--force] | "
          "grub-snippet counter|entry")
    return 2

