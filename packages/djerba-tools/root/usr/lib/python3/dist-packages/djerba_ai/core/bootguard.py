# SPDX-License-Identifier: GPL-3.0-or-later
"""Rollback au démarrage (Btrfs + UEFI) : le système revient seul au dernier
démarrage réussi après deux échecs consécutifs.

* **Dernier bon état** : après chaque démarrage réussi (``djerba-boot-ok``,
  3 min après l'amorçage, système « running » ou « degraded » et gestionnaire
  de connexion actif), le sous-volume racine est cloné en
  ``@djerba-last-good`` (clone **inscriptible**, coût nul grâce au
  copy-on-write) ; son ``/etc/fstab`` pointe vers lui-même et ses noyau et
  initrd sont exposés sous des noms stables (``/boot/djerba-vmlinuz``,
  ``/boot/djerba-initrd.img``, copies reflink).
* **Compteur GRUB** : GRUB ne sait pas écrire sur Btrfs ; le compteur
  ``djerba_boot_tries`` vit donc sur l'ESP (FAT), ``EFI/djerba/grubenv``.
  Chaque démarrage normal l'incrémente (0 → 1 → 2), un démarrage réussi le
  remet à 0. À 2 (deux échecs), GRUB démarre l'entrée
  « dernier démarrage réussi ». Tant que le système tourne sur ce clone, le
  compteur reste à 2 (pas de retour automatique sur l'état cassé).
* **Rendre définitif** : ``djerba-ai rollback --make-permanent`` (root)
  renomme l'ancien ``@`` en ``@djerba-broken-<date>`` et recrée ``@`` à partir
  du clone ; au redémarrage suivant, le système normal est l'état restauré.

Non pris en charge (documenté) : racine chiffrée par LUKS (GRUB devrait
déverrouiller le volume), démarrage BIOS (pas d'ESP pour le compteur),
racine non Btrfs. Dans ces cas, rien n'est généré et rien n'est modifié.
"""

from __future__ import annotations

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

Runner = Callable[..., subprocess.CompletedProcess]


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
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
    def __init__(self, backend: BtrfsBackend | None = None, esp: Path = ESP, runner: Runner = _run) -> None:
        self._backend = backend
        self.esp = esp
        self.runner = runner

    @property
    def backend(self) -> BtrfsBackend | None:
        if self._backend is None:
            self._backend = BtrfsBackend.detect(runner=self.runner)
        return self._backend

    @property
    def env_file(self) -> Path:
        return self.esp / ENV_RELATIVE

    # ------------------------------------------------------------ compteur
    def tries(self) -> int:
        result = self.runner(["grub-editenv", str(self.env_file), "list"])
        match = re.search(r"^djerba_boot_tries=(\d+)$", result.stdout, re.MULTILINE)
        return int(match.group(1)) if match else 0

    def set_tries(self, value: int) -> bool:
        if not self.esp.is_dir():
            return False
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.env_file.exists():
            self.runner(["grub-editenv", str(self.env_file), "create"])
        return self.runner(["grub-editenv", str(self.env_file), "set", f"djerba_boot_tries={value}"]).returncode == 0

    # ------------------------------------------------------------ état
    def booted_from_last_good(self) -> bool:
        result = self.runner(["findmnt", "-n", "-o", "FSROOT", "/"])
        return result.stdout.strip().strip("/") == LAST_GOOD

    def healthy_boot(self) -> bool:
        state = self.runner(["systemctl", "is-system-running"]).stdout.strip()
        if state not in ("running", "degraded"):
            return False
        manager = self.runner(["systemctl", "is-enabled", "display-manager.service"]).stdout.strip()
        if manager in ("enabled", "static", "alias"):
            return self.runner(["systemctl", "is-active", "display-manager.service"]).stdout.strip() == "active"
        return True

    # ------------------------------------------------------------ dernier bon état
    def refresh_last_good(self) -> str:
        backend = self.backend
        if backend is None:
            return "racine non Btrfs : rien à faire"
        target = backend.top / LAST_GOOD
        if target.exists():
            result = self.runner(["btrfs", "subvolume", "delete", str(target)], timeout=300)
            if result.returncode != 0:
                return f"suppression de l'ancien clone impossible : {result.stderr.strip()[:200]}"
        result = self.runner(["btrfs", "subvolume", "snapshot", str(backend.top / backend.source), str(target)],
                             timeout=300)
        if result.returncode != 0:
            return f"clone impossible : {result.stderr.strip()[:200]}"
        point_fstab(target, LAST_GOOD)
        kernel = newest_kernel(target)
        if kernel:
            for source, name in zip(kernel, (KERNEL, INITRD)):
                self.runner(["cp", "--reflink=auto", "-f", str(source), str(target / name)], timeout=300)
        (target / MARKER).write_text(f"clone du {time.strftime('%Y-%m-%d %H:%M:%S')} de /{backend.source}\n")
        return f"dernier bon état enregistré ({LAST_GOOD})"

    def mark_success(self) -> str:
        """Démarrage réussi : compteur remis à zéro et dernier bon état rafraîchi."""
        if not self.healthy_boot():
            return "démarrage incomplet : compteur conservé"
        if self.booted_from_last_good():
            # Pas de retour automatique sur l'état qui a échoué : l'utilisateur décide.
            self.set_tries(2)
            return ("système restauré au dernier démarrage réussi : « djerba-ai rollback --make-permanent » "
                    "pour le conserver")
        self.set_tries(0)
        message = self.refresh_last_good()
        if self.backend and (self.backend.top / LAST_GOOD).exists() and not self.grub_has_entry():
            self.runner(["update-grub"], timeout=600)
        return message

    def grub_has_entry(self, config: Path = Path("/boot/grub/grub.cfg")) -> bool:
        try:
            return ENTRY_ID in config.read_text(errors="replace")
        except OSError:
            return False

    def make_permanent(self, force: bool = False) -> str:
        backend = self.backend
        if backend is None:
            raise RuntimeError("racine non Btrfs")
        if not force and not self.booted_from_last_good():
            raise RuntimeError("le système ne tourne pas sur le dernier bon état")
        source = backend.top / backend.source
        broken = backend.top / f"@djerba-broken-{time.strftime('%Y%m%d%H%M%S')}"
        os.rename(source, broken)
        result = self.runner(["btrfs", "subvolume", "snapshot", str(backend.top / LAST_GOOD), str(source)],
                             timeout=300)
        if result.returncode != 0:
            os.rename(broken, source)
            raise RuntimeError(f"restauration impossible : {result.stderr.strip()[:200]}")
        point_fstab(source, backend.source)
        (source / MARKER).unlink(missing_ok=True)
        self.set_tries(0)
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
        return (f"menuentry 'Djerba OS — dernier démarrage réussi (restauration)' --class djerba --id {ENTRY_ID} {{\n"
                "  insmod part_gpt\n  insmod btrfs\n"
                f"  search --no-floppy --fs-uuid --set=root {facts['root_uuid']}\n"
                f"  echo 'Restauration du dernier démarrage réussi…'\n"
                f"  linux /{LAST_GOOD}/{KERNEL} root=UUID={facts['root_uuid']} rootflags=subvol=/{LAST_GOOD} ro "
                f"{options}\n"
                f"  initrd /{LAST_GOOD}/{INITRD}\n"
                "}\n")

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
        print(f"djerba-boot : {guard.mark_success()}", flush=True)
        return 0
    if command == "make-permanent":
        try:
            print(guard.make_permanent(force="--force" in argv))
        except (RuntimeError, OSError) as exc:
            print(f"djerba-boot : {exc}", flush=True)
            return 1
        return 0
    if command == "status":
        print(f"compteur : {guard.tries()} ; sur le dernier bon état : {guard.booted_from_last_good()}")
        return 0
    print("Usage : djerba-boot ok | status | make-permanent [--force] | grub-snippet counter|entry")
    return 2

