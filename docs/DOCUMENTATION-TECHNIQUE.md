# Djerba OS — documentation technique complète

> Document de référence destiné à un développeur ou à une IA qui reprend le
> projet. Il décrit **chaque dossier, chaque fichier, chaque chemin installé et
> chaque fonctionnalité**, l'architecture, les contrats internes, la chaîne de
> construction, les tests, la CI et les limites connues.
>
> Dépôt : `dc92jnrvg8-lang/distrib-djerba` — branche de travail
> `claude/djerba-os-builder`. Langue du projet : **français** (code commenté,
> messages, interface, commits). Licence : GPL-3.0-or-later.

---

## Sommaire

1. [Vue d'ensemble](#1-vue-densemble)
2. [Principes et contraintes du projet](#2-principes-et-contraintes-du-projet)
3. [Arborescence du dépôt (fichier par fichier)](#3-arborescence-du-dépôt-fichier-par-fichier)
4. [Chaîne de construction de l'ISO](#4-chaîne-de-construction-de-liso)
5. [Système live et installé : ce que contient l'ISO](#5-système-live-et-installé--ce-que-contient-liso)
6. [Paquet `djerba-branding`](#6-paquet-djerba-branding)
7. [Paquet `djerba-tools` : outils en ligne de commande](#7-paquet-djerba-tools--outils-en-ligne-de-commande)
8. [Agent IA `djerba_ai` : architecture](#8-agent-ia-djerba_ai--architecture)
9. [Agent IA : module par module](#9-agent-ia--module-par-module)
10. [Capacités de l'agent (265 outils)](#10-capacités-de-lagent-265-outils)
11. [Configuration centrale (`/etc/djerba/config.toml`)](#11-configuration-centrale-etcdjerbaconfigtoml)
12. [Démon root `djerba-ai-sysd` et protocole JSON-RPC](#12-démon-root-djerba-ai-sysd-et-protocole-json-rpc)
13. [Sécurité : modèle de menace et défenses](#13-sécurité--modèle-de-menace-et-défenses)
14. [Multi-bureau (KDE Plasma / GNOME)](#14-multi-bureau-kde-plasma--gnome)
15. [Santé de dpkg et réparation automatique](#15-santé-de-dpkg-et-réparation-automatique)
16. [Chemins installés et données à l'exécution](#16-chemins-installés-et-données-à-lexécution)
17. [Services systemd, polkit, autostart](#17-services-systemd-polkit-autostart)
18. [Tests](#18-tests)
19. [CI/CD GitHub Actions et publication](#19-cicd-github-actions-et-publication)
20. [Limites connues, dette technique et pistes d'amélioration](#20-limites-connues-dette-technique-et-pistes-damélioration)
21. [Guide pour contribuer (humain ou IA)](#21-guide-pour-contribuer-humain-ou-ia)

---

## 1. Vue d'ensemble

**Djerba OS** est une distribution Linux dérivée de **Debian 13 « trixie »**,
livrée sous forme d'**ISO live hybride installable** (amd64 et arm64) :

| Élément | Choix |
|---|---|
| Socle | Debian 13 trixie (main, contrib, non-free, non-free-firmware), noyau Debian 6.12 avec `preempt=full` |
| Bureau | **KDE Plasma 6.3** (Qt 6.8), Wayland, gestionnaire de connexion **SDDM** ; **GNOME 48** optionnel (catalogue) |
| Construction | `live-build` 1:20250505 dans un conteneur Docker Debian 13 (`Dockerfile`) |
| Installateur | **Calamares 3.3** (thème et modules Djerba, page « Applications » générée depuis le catalogue) |
| Amorçage | GRUB EFI signé (shim, Secure Boot) + grub-pc (BIOS) sur amd64, iso-hybrid |
| Identité | paquet Debian `djerba-branding` (os-release par diversion dpkg, thèmes Plymouth/SDDM/GRUB/Plasma) |
| Outils | paquet Debian `djerba-tools` : assistant d'accueil, catalogue d'apps, mode anonyme (Tor), mode discret (look Windows), mode jeu, **agent IA « Djerba »** |
| Agent IA | Python 3.13 + PyQt6/QML ; moteurs (registre `core/engines.py`) : Ollama local, Ollama distant, Jarvis, Anthropic, API compatibles OpenAI/Gemini (relayés par le démon root) ; serveur MCP pour Jarvis ; 265 capacités |
| CI | GitHub Actions : lints, construction ISO amd64/arm64, tests de contenu, démarrage QEMU UEFI, tests en session live avec réseau, publication en pré-release |

Fonctionnalités principales visibles par l'utilisateur :

* **Session live** avec connexion automatique (utilisateur `djerba`, mot de passe `live`), assistant d'accueil, installation par Calamares.
* **Catalogue d'applications** (`djerba-apps`) : Steam, Lutris, Heroic, Bottles, Wine, Discord, OBS, LibreOffice, KeePassXC, Tor Browser, **bureau GNOME**… installées à l'installation ou au premier démarrage.
* **Mode jeu** (Djerba Game Center) : profil d'énergie, effets KWin, tearing, MangoHud, GE-Proton.
* **Mode anonyme** (inspiré d'AnonSurf) : tout le trafic via Tor avec coupe-circuit nftables.
* **Mode discret** (inspiré de kali-undercover) : apparence Windows 10/11 réversible à l'identique.
* **Agent IA Djerba** : discussion, barre Spotlight (Meta+Espace), anneau d'action HUD (Super+Espace), tableau de bord, Studio de génération d'applications, auto-réparation, auto-extension (capacités générées testées par Pytest en bac à sable), mémoire chiffrée, défense active (IDS + nftables, AppArmor généré).
* **Réparation automatique de dpkg** (transaction interrompue, conflits de fichiers) avec notification « Réparer ».

---

## 2. Principes et contraintes du projet

* **Uniquement des paquets Debian (APT)** dans l'image ; jamais de `pip` global (PEP 668). Les dépendances Python supplémentaires de l'agent vont dans un venv interne `/usr/share/djerba-ai/venv`.
* **Tout ce qui est propre à Djerba est livré par des paquets `.deb`** construits dans ce dépôt (`packages/`), installés par live-build via `config/packages.chroot/`.
* **Bash strict** : chaque script Bash commence par `set -euo pipefail` (vérifié par les lints) et passe `shellcheck`.
* **Python** : compilé avec `-W error` dans les lints ; `pyflakes` propre ; Python ≥ 3.11 (3.13 dans Debian 13).
* **Séparation des privilèges** : l'interface et l'agent tournent en utilisateur ; toute action root passe par le démon `djerba-ai-sysd` (JSON-RPC sur socket UNIX, schémas Pydantic stricts, appelants vérifiés, polkit, audit chaîné).
* **Aucune donnée sensible en clair** : clé d'API lisible seulement par root (`/etc/djerba/config.toml` 0600), mémoire/sessions/bus de contexte chiffrés (AES-256-GCM), secrets masqués avant journalisation.
* **Ne jamais bloquer le bureau** : intégrations isolées (mode dégradé silencieux), maintenance de dpkg hors transaction.
* **Tests avant tout** : `tests/run_local_lints.sh` (barrière de qualité) est exécuté localement, par `build.sh` et par la CI.

---

## 3. Arborescence du dépôt (fichier par fichier)

Légende : *(gén.)* = généré à la construction, non versionné.

### 3.1 Racine

| Chemin | Rôle |
|---|---|
| `README.md` | Documentation utilisateur et développeur (démarrage rapide, fonctionnalités, agent, CI, dépannage). |
| `docs/DOCUMENTATION-TECHNIQUE.md` | Ce document. |
| `build.sh` | Orchestrateur de construction : contrôles (root, outils, live-build), `apt-get update` avec reprises, lints, génération des assets, construction des `.deb`, `lb clean/config/build` (reprise par étapes), copie de l'ISO + SHA-256 dans `artifacts/`. Option `--docker` pour tout faire dans le conteneur. |
| `Dockerfile` | Image de construction Debian 13 : live-build, debootstrap, xorriso, chargeurs signés, outils Qt 6, QEMU/OVMF/AAVMF, `python3-pydantic`, `python3-cryptography`, `python3-seccomp`, `pytest`, `apparmor`, `python3-pyflakes`… |
| `.gitignore` | Exclut `build/`, `artifacts/`, `cache/`, `chroot/`, sorties de live-build, assets générés, `config/packages.chroot/`, `tests/out/`. |
| `djerba-linux-iso.zip` | Archive héritée de la toute première version (≈ 42 Ko), extraite par `bootstrap.yml` ; inutilisée par la construction actuelle (candidate à suppression avec ce workflow). |
| `.github/workflows/build-and-test.yml` | Pipeline principal (voir §19). |
| `.github/workflows/bootstrap.yml` | Workflow historique « Extraire Djerba Linux » : décompresse un `*.zip` poussé à la racine (amorçage initial du dépôt). Déclenché sur push d'un zip ou manuellement. |

### 3.2 `auto/` — scripts live-build

| Fichier | Rôle |
|---|---|
| `auto/config` | Appelé par `lb config` : distribution trixie, zones d'archive, `--bootloaders grub-efi` (+ grub-pc amd64), `--apt-recommends false`, paramètres noyau (`preempt=full`, quiet splash…), nom/version de l'ISO. Variables : `DJERBA_ARCH` (amd64/arm64), `DJERBA_VERSION`. |
| `auto/build` | Appelé par `lb build` : journalise dans `build.log`. |
| `auto/clean` | Appelé par `lb clean` : nettoie aussi les fichiers générés. |

### 3.3 `config/` — configuration live-build

#### `config/package-lists/`
| Fichier | Contenu |
|---|---|
| `01-djerba-base.list.chroot` | Socle : live-boot/live-config (+ recommandations indispensables), systemd-resolved, libnss-resolve, NetworkManager, UFW, AppArmor, zram-tools, firmware, outils système, Tor, nftables, ClamAV/ClamTk, unattended-upgrades… |
| `02-djerba-desktop.list.chroot` | KDE Plasma 6 (plasma-desktop, kwin-wayland, sddm, dolphin, konsole, kate, discover + backends Flatpak), Calamares, polices, PipeWire, Qt 6 QML (modules utilisés par les outils), Xwayland… |
| `03-djerba-gaming.list.chroot` | GameMode, MangoHud, Vulkan, outils jeu de base. |

#### `config/hooks/normal/` — hooks exécutés dans le chroot
| Hook | Rôle |
|---|---|
| `0100-base-setup.hook.chroot` | APT résilient (`Acquire::Retries 10`, délais), recommandations désactivées, AC supplémentaires éventuelles, locales (fr_FR, en_US), services activés, droits. |
| `0200-performance.hook.chroot` | zram (zstd, 50 %, priorité 100), sysctl mémoire/ordonnancement (swappiness, dirty ratios), E/S, réseau (BBR, fq), GameMode, `kernel.split_lock_mitigate = 0`. |
| `0300-eyecandy.hook.chroot` | Thème Plymouth `djerba` par défaut, thème SDDM Djerba, polices, GTK. |
| `0400-security.hook.chroot` | UFW (entrées refusées, activé au boot), sysctl noyau durci (`kptr_restrict=2`, `dmesg_restrict=1`, `kexec_load_disabled=1`, `unprivileged_bpf_disabled=1`, `ptrace_scope=1`, `sysrq=176`), AppArmor, core dumps désactivés, mises à jour automatiques. |
| `0500-software.hook.chroot` | Firefox depuis le dépôt APT officiel Mozilla (avec 20 reprises), remote Flathub système. |
| `1000-…` à `9020-…` *(gén.)* | Hooks standard copiés par live-build (mtab, cryptsetup, locales, caches apt-file/xapian/plocate, nettoyage machine-id, clés SSH, pyc…) — non versionnés. |

#### `config/includes.chroot/` — fichiers copiés tels quels dans l'image
| Chemin | Rôle |
|---|---|
| `etc/apt/keyrings/` | Clé du dépôt Mozilla. |
| `etc/apt/sources.list.d/mozilla.sources` | Dépôt APT Mozilla (Firefox natif). |
| `etc/apt/preferences.d/djerba-mozilla` | Épinglage : Firefox Mozilla prioritaire sur firefox-esr. |
| `etc/calamares/settings.conf` | Séquence de l'installateur (welcome, locale, keyboard, partition, users, netinstall « Applications », summary, exec…). |
| `etc/calamares/modules/*.conf` | `bootloader`, `displaymanager` (SDDM), `fstab`, `grubcfg`, `initramfs`, `locale`, `mount`, `networkcfg`, `packages` (retrait des paquets live), `partition` (btrfs par défaut, swap zram), `services-systemd`, `shellprocess_target-cleanup` (retire l'autologin live, la règle sudo live…), `unpackfs`, `users`, `welcome`. `netinstall.conf` *(gén.)* par `tools/catalog-to-netinstall.py`. |
| `usr/lib/calamares/modules/djerbaapps/` | Module Calamares Python : applique le choix d'applications (écrit la file `pending.json` de `djerba-apps` dans la cible ; installation au premier démarrage par `djerba-apps-firstboot.service`). |
| `etc/flatpak/remotes.d/flathub.conf` | Remote Flathub système. |
| `etc/polkit-1/rules.d/99-djerba-installer.rules` | Session live : l'utilisateur live lance Calamares sans mot de passe. |
| `usr/share/polkit-1/actions/org.djerba.installer.policy` | Action polkit `org.djerba.installer`. |
| `usr/share/applications/djerba-install.desktop` | Lanceur « Installer Djerba OS ». |
| `etc/sddm.conf.d/10-wayland-autologin.conf` | Autologin `djerba` en session `plasma` (supprimé de la cible par Calamares). |
| `etc/skel/.config/*` | Réglages par défaut : `kdeglobals` (thème, couleurs), `kwinrc`, `plasmarc`, `ksplashrc`, `kscreenlockerrc`, `baloofilerc` (indexation légère), `djerba-welcomerc` (assistant au démarrage). |
| `etc/sudoers.d/live-user` | sudo sans mot de passe pour l'utilisateur live (session live seulement). |
| `etc/xdg/plasma-workspace/env/10-djerba-vm-cursor.sh` | En VM : curseur logiciel KWin (évite le curseur invisible). |
| `usr/local/share/ca-certificates/local-proxy-*.crt` | **Jamais versionné** (exclu) : AC d'un proxy local éventuel. |

Autres : `config/preseed/djerba.cfg.chroot` (préréglages debconf), `config/packages.chroot/` *(gén.)* (les `.deb` Djerba), `config/bootloaders/`, thèmes GRUB/Plymouth/SDDM/Calamares *(gén.)* par le générateur d'assets.

### 3.4 `assets_generator/` — identité visuelle

| Fichier | Rôle |
|---|---|
| `generate_assets.py` | Produit de façon déterministe, à partir d'un jeu de **jetons de design** (couleurs turquoise/corail/sable, nuit), logo SVG, icônes hicolor, fonds d'écran (Djerba, Djerba Horizon, Discret), thèmes globaux Plasma, thème GRUB (splash + polices), thème Plymouth, thème SDDM, thème Calamares. Sorties sous `build/djerba-branding/` (intégrées au paquet `djerba-branding`) et `config/` (ignorées par git). Dépendances APT : python3-jinja2, python3-cairosvg, python3-pil, outils Qt. |
| `templates/sddm.qml.j2` | Écran de connexion SDDM (QML). |
| `templates/plymouth.qml.j2` | Animation de démarrage (rendue en images pour Plymouth). |
| `templates/grub-splash.png.j2` | Fond du thème GRUB. |
| `templates/calamares-theme.j2` | Branding Calamares (`branding.desc`, QML du diaporama). |

### 3.5 `packages/` — paquets Debian de la distribution

| Chemin | Rôle |
|---|---|
| `packages/build-packages.sh` | Construit chaque `packages/<nom>/` en `.deb` : copie `root/`, ajoute les assets générés (branding), substitue `@VERSION@` dans `control.in` **et les scripts de maintenance**, normalise les droits (dossiers 0755, fichiers 0644, exécutables 0755 dans `usr/bin`, `usr/sbin`, `usr/libexec`, `usr/lib/djerba`, sudoers 0440), génère `/usr/share/djerba-ai/trusted-callers.sha256` (empreinte de `/usr/bin/djerba-ai`), calcule `Installed-Size`, `dpkg-deb --root-owner-group`. Usage : `packages/build-packages.sh [--version V] [--output DIR]` (défaut : `config/packages.chroot/`). Version Debian : `2026.10-dev` → `2026.10~dev`. |
| `packages/djerba-branding/` | Voir §6. |
| `packages/djerba-tools/` | Voir §7 à §17. |

### 3.6 `tools/`

| Fichier | Rôle |
|---|---|
| `tools/catalog-to-netinstall.py` | Génère `config/includes.chroot/etc/calamares/modules/netinstall.conf` (page « Applications » de Calamares) depuis `apps.yaml`, en ne gardant que les applications disponibles pour l'architecture construite. Usage : `--arch amd64 [--catalog F] [--output F]`. |

### 3.7 `tests/`

| Fichier | Rôle |
|---|---|
| `run_local_lints.sh` | Barrière de qualité : shellcheck sur tous les scripts (détection par shebang), `set -euo pipefail`, `py_compile -W error`, YAML/INI, tests de l'agent, XML polkit, `visudo`, qmllint si présent. |
| `test-yaml-ini.py` | Validateur syntaxique et structurel des YAML (catalogue, workflows) et INI/.desktop/configs. |
| `test-djerba-ai.py` | ~99 tests unitaires et d'intégration de l'agent (voir §18). |
| `test-features.sh` | Contrôles de l'ISO produite (≈ 160) : structure (xorriso), contenu du squashfs, puis chroot (polkit, imports Python/PyQt6, QML, services activés, dpkg cohérent…). |
| `test-iso-qemu.sh` | Démarrage UEFI headless (OVMF/AAVMF) jusqu'à `djerba login:` sur la console série, capture d'écran QMP. |
| `test-live-session.sh` | Démarre l'ISO, se connecte au getty série et lance `live-session-checks.py` (`--network` pour l'accès Internet). |
| `live-session-checks.py` | 35 contrôles hors ligne + 11 avec réseau exécutés dans la VM (DNS, Tor, agent IA, démon root, bac à sable, audit, bus de contexte, télémétrie, Ghost Runner, HUD, IDS, mode discret, mode jeu, dpkg, identité Plasma, installation GNOME + démarrage de GNOME Shell avec l'extension). |
| `tests/out/` *(gén.)* | Journaux QEMU, captures. |

---

## 4. Chaîne de construction de l'ISO

```
build.sh [--docker] [--arch amd64|arm64] [--version V]
 ├─ contrôles préalables (root, live-build ≥ 1:20250505, outils)
 ├─ apt-get update (reprises)
 ├─ tests/run_local_lints.sh
 ├─ assets_generator/generate_assets.py   → build/djerba-branding/, config/…
 ├─ tools/catalog-to-netinstall.py        → netinstall.conf
 ├─ packages/build-packages.sh            → config/packages.chroot/djerba-*.deb
 ├─ lb clean ; lb config (auto/config) ; lb build (reprise par étapes)
 └─ artifacts/djerba-os-<version>-<arch>.iso + .sha256
```

* Construction **native seulement** : arm64 sur hôte arm64 (runner `ubuntu-24.04-arm`), amd64 sur amd64.
* Le cache APT de live-build (`cache/`) est restauré/sauvegardé en CI.
* En local : `sudo ./build.sh --docker` (l'image `djerba-os-builder:local` est construite depuis le `Dockerfile` ; dockerd doit tourner).

---

## 5. Système live et installé : ce que contient l'ISO

* **Réseau** : NetworkManager + **systemd-resolved** ; `/etc/resolv.conf` → `../run/systemd/resolve/stub-resolv.conf` ; `libnss-resolve` (corrige un ancien bug de résolution DNS vide).
* **Sécurité** : UFW actif (entrées refusées), sysctl durci, AppArmor (profils chargés une fois installé ; le live ne les charge pas — comportement Debian), root verrouillé, ClamAV + ClamTk, `unattended-upgrades`.
* **Performances** : zram, réglages mémoire/E/S/réseau, GameMode, `preempt=full`.
* **Session live** : autologin Plasma Wayland, sudo sans mot de passe, assistant d'accueil, lanceur d'installation.
* **Installation (Calamares)** : partitionnement (btrfs par défaut), utilisateurs, page « Applications » (catalogue), nettoyage de la cible (autologin et sudo live retirés), file d'installation des applications appliquée au premier démarrage avec réseau.
* **Premier démarrage** : `djerba-apps-firstboot.service` installe les applications choisies (réessaie toutes les 2 min tant que le réseau manque) ; l'assistant d'accueil affiche la progression.

---

## 6. Paquet `djerba-branding`

Arborescence : `packages/djerba-branding/`

| Chemin | Rôle |
|---|---|
| `DEBIAN/control.in` | `Depends: base-files, grub-common` ; `Recommends: plymouth, sddm, plasma-workspace` ; **`Replaces: desktop-base`**, **`Breaks: desktop-base (<< 13.0.4)`**, **`Conflicts: desktop-base (<< 13.0.4)`**. |
| `DEBIAN/preinst` | `dpkg-maintscript-helper rm_conffile /etc/xdg/kcm-about-distrorc "@VERSION@~"` (ancien conffile qui entrait en conflit avec desktop-base) ; **diversion** de `/usr/lib/os-release` (base-files) vers `/usr/lib/os-release.debian`. |
| `DEBIAN/postinst` | rm_conffile ; régénère `/etc/os-release` (copie live-build enrichie d'IMAGE_ID/BUILD_ID) ; cache d'icônes ; `update-grub` sur système installé ; **filet de sécurité non bloquant** : programme `djerba-dpkg-repair --auto` via `systemd-run --on-active=60` (jamais `dpkg --configure -a` dans un script de maintenance : verrou). |
| `DEBIAN/postrm` | rm_conffile ; retire la diversion d'os-release à la suppression. |
| `DEBIAN/conffiles` | `/etc/xdg/djerba/kcm-about-distrorc`, `/etc/default/grub.d/djerba.cfg`, `/etc/xdg/djerba/kickoffrc`. |
| `root/etc/default/grub.d/djerba.cfg` | Nom de distribution, thème GRUB, paramètres noyau du système installé. |
| `root/etc/xdg/djerba/kcm-about-distrorc` | « À propos de ce système » : logo `/usr/share/pixmaps/djerba-os.png`, site, variante « Basé sur Debian 13 ». |
| `root/etc/xdg/djerba/kickoffrc` | Lien « Accueil » du lanceur Kickoff. |
| `root/etc/xdg/plasma-workspace/env/djerba-xdg.sh` | Préfixe `XDG_CONFIG_DIRS` par `/etc/xdg/djerba` (sourcé par startplasma, idempotent). |
| `root/usr/lib/environment.d/60-djerba-xdg.conf` | Même préfixe pour les services de session lancés par systemd. |
| *(gén.)* | `/usr/lib/os-release` (`ID=djerba`, `ID_LIKE=debian`), logo et icônes, fonds d'écran, thèmes globaux Plasma « Djerba », « Djerba Horizon », « Discret », thèmes GRUB/Plymouth/SDDM. |

**Pourquoi `/etc/xdg/djerba` ?** `/etc/xdg/kcm-about-distrorc` appartient à `desktop-base` (Debian), tiré notamment par `gdm3` et `gnome-control-center`. Le livrer depuis `djerba-branding` provoquait « trying to overwrite '/etc/xdg/kcm-about-distrorc', which is also in package djerba-branding » et cassait dpkg (et donc Discover et l'installation de GNOME). Debian interdit de dérouter un conffile (Charte 10.7.4) ; la solution retenue est un dossier XDG prioritaire, sans conflit.

---

## 7. Paquet `djerba-tools` : outils en ligne de commande

`packages/djerba-tools/DEBIAN/control.in` — `Depends` : `djerba-branding (= version)`, python3, PyQt6 + modules QML Qt 6, pkexec, libnotify-bin, tor, nftables, curl, systemd-resolved, plasma-workspace, kde-cli-tools, power-profiles-daemon, git, shellcheck, python3-pydantic (≥ 2), python3-cryptography, python3-seccomp, python3-pytest(-timeout), python3-gi, apparmor… ; `Recommends` : flatpak, torsocks, linux-cpupower, lm-sensors ; `Suggests` : tailscale, python3-nautilus, gnome-shell-extension-prefs.

Scripts de maintenance :
* `postinst` : crée `/var/lib/djerba/apps`, la configuration (`Store().ensure()`), `/var/log/djerba` (root:adm 0750) et `audit.log` (0640), `/var/lib/djerba-ai-runs` (0711), sysctl, venv interne, active `djerba-apps-firstboot.service`, `djerba-ai-sysd.socket`, `djerba-ai-ids.service`, **`djerba-dpkg-repair.timer`**, `/etc/apparmor.d/djerba-generated`, `systemctl --global enable djerba-ai.service`.
* `prerm` : désactive IDS (et supprime sa table nftables), minuterie dpkg… ; `postrm` : nettoyage.
* `conffiles` : autostarts (`djerba-welcome`, `djerba-ai-tray`, `djerba-dpkg-watch`), `/etc/tor/djerba-anonsurf.torrc`, `/etc/apt/apt.conf.d/80djerba-dpkg-repair`.

### 7.1 Exécutables (`/usr/bin`)

| Commande | Langage | Fonction |
|---|---|---|
| `djerba-ai` | Python | Agent IA (voir §8). Usage : `djerba-ai [--page chat|system|selfheal|studio|tools|settings]`, `--hud`, `--spotlight`, `--ask "…"`, `--ask-file ACTION F…`, `--ask-selection C NOM` (stdin), `--clipboard`, `--tray`, `ask "…"` (terminal), `tools [recherche]`, `run OUTIL ['{json}'] [--verbose]`, `health`, `doctor`, `run-app SLUG [--live]`, `daemon`, `policy [prudent|autonome|total]`, `pause [raison]`, `resume`, `transactions [N]`, `undo ID [--force]`, `undo --since DATE`, `rollback --make-permanent`, `session-report gnome [unité]`. |
| `djerba-apps` | Python | Catalogue d'applications (`/usr/share/djerba/apps.yaml`) : `list [--json]`, `install ID…`, `remove ID…`, `apply-selection FICHIER [--installer]`, `apply-pending [--wait-network S]`, `status [--json]`, progression JSON (`--json-progress`, événements start/progress/log/finish/waiting/error/complete). Verrou global, multiarch i386, debconf, Flatpak (Flathub), `APT::Install-Recommends=true`, `DPkg::Lock::Timeout=600`, **exclusions** (`apt.exclude` → `paquet-`), **vérification post-installation** (`verify:`), **réparation de dpkg** avant/après installation. État : `/var/lib/djerba/apps/{pending,last-run}.json`, `/run/djerba-apps/status.json`. |
| `djerba-anonsurf` | Bash | Mode anonyme : `start|stop|restart|toggle|status|change|myip [--json] [--verbose]`. Coupe-circuit nftables d'abord (table `inet djerba_anonsurf`), TCP → TransPort 9040, DNS → DNSPort 5353, LAN/DHCP autorisés, IPv6/UDP/ICMP Internet rejetés ; annulation complète si Tor ne s'amorce pas ; `change` = NEWNYM. Service `djerba-anonsurf-tor.service`, torrc `/etc/tor/djerba-anonsurf.torrc` (profil AppArmor de Tor respecté). |
| `djerba-undercover` | Bash | Mode discret : `on|off|toggle|status [--style 10|11] [--json] [--verbose]`. Sauvegarde puis restauration exacte des fichiers Plasma (kdeglobals, kwinrc, plasmarc, plasmashellrc, appletsrc, kcminputrc, ksplashrc, kscreenlockerrc, GTK…) en arrêtant proprement plasmashell. N'écrit que dans `~/.config`. |
| `djerba-gamemode` | Python | Moteur du mode jeu : `on|off|toggle`, `status [--json]`, `profile performance|balanced|power-saver`, `effects on|off`, `tearing on|off`, `mangohud on|off|preset|fps-limit N` (`~/.config/environment.d/90-djerba-mangohud.conf`, `~/.config/MangoHud/MangoHud.conf`), `proton list|install-ge|remove` (GE-Proton vérifié SHA-512). |
| `djerba-game-center` | Python + QML | Interface du mode jeu (lanceurs Steam/Big Picture/Lutris/Heroic/Bottles/RetroArch/GOverlay, réglages). QML : `/usr/share/djerba-game-center/ui/main.qml`. |
| `djerba-welcome` | Python + QML | Assistant d'accueil : pages Accueil (installation, suivi des apps), Applications, Jeu, Confidentialité, Système ; `--install` lance Calamares. QML : `/usr/share/djerba-welcome/ui/main.qml`. |

### 7.2 Exécutables internes (`/usr/libexec`)

| Fichier | Fonction |
|---|---|
| `djerba-ai-sysd` | Démon root de l'agent (activé par socket). |
| `djerba-ai-ids` | Service root de détection d'intrusion. |
| `djerba-dpkg-repair` | Santé de dpkg : `--check [--json]` (sans privilège), `--auto` / `--repair [--json]` (root), `--schedule` (crochet APT), `--watch` (session). Voir §15. |
| `djerba-boot` | Rollback au démarrage : `ok`, `status`, `make-permanent [--force]`, `grub-snippet counter|entry`. Voir §15 bis. |

### 7.3 Données (`/usr/share`)

| Chemin | Rôle |
|---|---|
| `djerba/apps.yaml` | Catalogue (version 1) : catégories `gaming`, `multimedia`, `office`, `security`, `desktop` ; applications : steam, lutris, heroic, protonup-qt, bottles, protontricks, wine, retroarch, dolphin-emu, prismlauncher, discord, vlc, obs-studio, kdenlive, gimp, krita, spotify, libreoffice, thunderbird, clamtk, keepassxc, torbrowser, bleachbit, **gnome-desktop**. Champs : `id`, `name`, `category`, `icon`, `description`, `selected`, `preinstalled`, `architectures`, `notice`, `apt: {packages, foreign_architectures, debconf, exclude}` **ou** `flatpak: ID`, `verify: [chemins]`. |
| `djerba-ai/kwin/active-window.js` | Script KWin : fenêtre active → D-Bus `org.djerba.ai.Context`. |
| `djerba-ai/integrations/gedit/` | Greffon Gedit (`djerba.plugin`, `djerba.py`) copié pour l'utilisateur sous GNOME. |
| `djerba-ai/monitoring/` | `prometheus.yml`, `grafana-dashboard.json`, `uptime-kuma.md` (supervision de l'agent). |
| `djerba-ai/trusted-callers.sha256` *(gén.)* | Empreinte des points d'entrée autorisés à appeler le démon root. |
| `djerba-ai/venv` *(créé au postinst)* | venv interne (`--system-site-packages`). |
| `gnome-shell/extensions/djerba@djerba-os.org/` | Extension GNOME Shell 46–49 (`metadata.json`, `extension.js` ESM) : fenêtre active → bus de contexte, bouton de barre supérieure ouvrant le HUD ; toutes les méthodes protégées par try/catch. |
| `kio/servicemenus/djerba-ai.desktop` | Menu contextuel Dolphin (« Djerba : résumer / expliquer / ranger… » → `djerba-ai --ask-file`). |
| `nautilus-python/extensions/djerba_ai_nautilus.py` | Même menu pour Nautilus. |
| `polkit-1/actions/org.djerba.ai.policy`, `org.djerba.tools.policy` | Actions polkit (voir §17). |
| `applications/*.desktop` | Lanceurs : Djerba (agent), Spotlight, presse-papiers, Mode anonyme, Applications, Game Center, Mode discret, Bienvenue. |
| `djerba-welcome/ui/main.qml`, `djerba-game-center/ui/main.qml` | Interfaces QML. |

### 7.4 Configuration système livrée

| Chemin | Rôle |
|---|---|
| `/etc/apt/apt.conf.d/80djerba-dpkg-repair` | `DPkg::Post-Invoke` → `djerba-dpkg-repair --schedule` (toujours `|| true`). |
| `/etc/tor/djerba-anonsurf.torrc` | Configuration Tor du mode anonyme. |
| `/etc/xdg/autostart/djerba-welcome.desktop` | Assistant au démarrage (KDE, condition `djerba-welcomerc`). |
| `/etc/xdg/autostart/djerba-ai-tray.desktop` | Agent résident dans la zone de notification (KDE). |
| `/etc/xdg/autostart/djerba-dpkg-watch.desktop` | Veille dpkg de session (tous bureaux). |
| `/usr/lib/sysctl.d/60-djerba-ai.conf` | `kernel.yama.ptrace_scope = 1`. |

---

## 8. Agent IA `djerba_ai` : architecture

Code : `packages/djerba-tools/root/usr/lib/python3/dist-packages/djerba_ai/`.

```
             ┌──────────────────────── session utilisateur ────────────────────────┐
 Interface   │ gui/app.py (PyQt6+QML) · gui/instance.py (instance unique, socket)  │
             │ Spotlight · HUD (ui/hud.py) · Studio · Tableau de bord · Tray        │
             ├──────────────────────────────────────────────────────────────────────┤
 Agent       │ core/agent.py (boucle agentique, niveaux de risque, confirmations)    │
             │ core/engine.py (couche cognitive : schémas, handshake, profils,       │
             │                 sessions chiffrées, bascule à chaud)                  │
             │ core/llm.py (Ollama local / Tailscale / Cloud via root ; transport    │
             │              persistant, reprises, repli d'adresse)                   │
             │ core/guard.py (anti-injection) · core/dlp.py · core/memory.py         │
             │ tools/ (265 capacités) · core/executor.py                              │
             ├──────────────────────────────────────────────────────────────────────┤
 Démon user  │ core/daemon.py (djerba-ai.service) : adaptateur de bureau, bus de     │
             │ contexte, télémétrie, auto-réparation, chien de garde, maintenance    │
             └──────────────────────────────┬───────────────────────────────────────┘
                                            │ JSON-RPC 2.0 / socket UNIX
                                            │ /run/djerba-ai/sysd.sock (SO_PEERCRED,
                                            │ appelant vérifié, polkit, audit)
             ┌──────────────────────────────▼───────────────────────────────────────┐
 Root        │ core/sysd.py (djerba-ai-sysd) : outils root, correctifs, plugins      │
             │ testés (core/sandbox.py : systemd/unshare/gVisor + seccomp), venv,    │
             │ API cloud (clé lue en root), audit chaîné (core/audit.py),            │
             │ AppArmor (security/apparmor.py), rapports de session                  │
             │ security/ids.py (djerba-ai-ids) : journal → règles → nftables          │
             └──────────────────────────────────────────────────────────────────────┘
```

Flux d'une demande :
1. **Ingestion** : `CognitiveEngine.ingest()` → schéma `UserPrompt` (NFC, invisibles et contrôles retirés, 16 000 car. max) → `guard.inspect_user()`.
2. **Contexte** : instantané système (`core/context.py`) + bus de contexte chiffré (`core/contextbus.py`) + mémoire/leçons pertinentes (`core/memory.py`, embeddings).
3. **Poignée de main** (`handshake`) : latence, taille du modèle, fenêtre de contexte, support des outils/de la réflexion via `/api/show` → profil `compact` / `standard` / `extended` de l'invite système ; les outils sont retirés si le modèle ne les gère pas.
4. **Inférence** : `ProviderManager.chat()` (repli transparent sur le local si le distant échoue en 5 s) ; réponse normalisée `ChatResult` (Pydantic).
5. **Outils** : chaque appel est validé (schéma de l'outil), classé par **niveau de risque** (lecture : faible ; modification : confirmation ; destructif/root : élevé, saisie « CONFIRMER »), exécuté côté utilisateur ou par le démon root ; les sorties d'outils sont **non fiables** (`wrap_untrusted`) et marquent la session « tainted » si elles contiennent une injection.
6. **Persistance** : session chiffrée écrite de façon atomique et asynchrone (`SessionStore`), audit côté root.

---

## 9. Agent IA : module par module

### 9.1 `core/`

| Module | Responsabilité | API principale |
|---|---|---|
| `agent.py` | Boucle agentique au-dessus du moteur cognitif : tours outil/réponse, confirmations, niveaux de risque, événements pour l'interface. | `Agent.run(prompt, conversation, on_event, confirm)` |
| `engine.py` | Couche d'abstraction cognitive : ingestion durcie, handshake, profils d'invite, `SessionStore` (chiffré), bascule à chaud entre moteurs (`swap`). | `CognitiveEngine`, `handshake`, `profile_for`, `PromptRejected` |
| `llm.py` | Fournisseurs : `LocalOllamaProvider`, `RemoteTailscaleProvider`, `CloudAPIProvider` (via sysd), `ProviderManager` (repli). **Transport persistant** `Transport`/`StreamResponse` : réserve de connexions keep-alive (4 par hôte), HTTP/2 via httpx+h2 sur TLS, délais connexion 5 s / 60 s entre fragments (premier fragment : `first_byte_timeout`, 600 s local, 120 s Tailscale), **3 reprises à attente exponentielle** sur `RemoteDisconnected`/reset, requête minimale (sans outils ni réflexion) en dernier recours, `relocate()` (127.0.0.1 → ::1 → `tailscale ip -4` → adresses globales, mémorisé), corps `/api/chat` validé par `OllamaChatRequest`. Aussi : `cloud_request`/`cloud_models` (OpenAI SSE, exécutés en root), `notify_desktop`. | voir API listée |
| `schemas.py` | Contrat Pydantic v2 unique : `UserPrompt`, `ToolCall`, `ChatMessage`, `ChatResult`, `Capabilities`, `SessionState`, **`OllamaChatRequest`/`OllamaMessage`/`OllamaToolSpec`**, paramètres de chaque méthode RPC (`RPC_PARAMS`), `parse_rpc`. Modèles « frozen », champs inconnus refusés. | |
| `guard.py` | Anti-injection : détection de consignes cachées, rôles usurpés, caractères invisibles ; `wrap_untrusted` encadre les contenus externes. | `inspect`, `inspect_user`, `wrap_untrusted`, `neutralize`, `Verdict` |
| `dlp.py` | Prévention des fuites à deux niveaux (motifs + entropie + jugement) avant envoi au cloud ; coffre de jetons. | `analyze`, `mask`, `judge` |
| `memory.py` | Mémoire permanente SQLite chiffrée (`~/.local/share/djerba/memory.db`) : conversations, faits, leçons, incidents, index sémantique. | `Memory` |
| `embeddings.py` | Embeddings Ollama (`/api/embed`, même transport) ou hachage local de repli ; cosinus, découpage. | `embed`, `ollama_embed`, `hashed`, `cosine`, `chunks` |
| `crypto.py` | AES-256-GCM (python3-cryptography), clé par utilisateur, écriture atomique. | `Box`, `write_atomic` |
| `context.py` | Instantané de configuration système donné à chaque requête. | `snapshot`, `collect` |
| `contextbus.py` | Bus de contexte permanent, horodaté, chiffré (`/run/user/UID/djerba-ai/context.bin`) : échantillonneur (CPU, mémoire, disques, réseau, erreurs du journal), `WindowBridge` (service D-Bus `org.djerba.ai.Context`, fenêtre active via KWin/Mutter, en mode dégradé si indisponible). | `ContextBus`, `read`, `context_block` |
| `daemon.py` | Démon utilisateur : `safe_sync` du bureau, bus de contexte, exporteur Prometheus, chien de garde (disque, services), auto-réparation, maintenance quotidienne (heure configurable), mise à jour du modèle. | `main` |
| `selfheal.py` | Surveillance du journal : erreur d'un outil `djerba-*` → incident → correctif proposé, testé en bac à sable, déployé dans `/usr/local/bin` (prioritaire), annulable. | `JournalWatcher`, `propose_patch` |
| `extend.py` | Auto-évolution : quand aucune capacité ne convient, l'agent écrit une capacité + tests Pytest, validées par sysd (3 essais). | `create_capability` |
| `ghost.py` | Ghost Runner : tâches de fond dans des unités systemd `--user` dédiées (`djerba-ghost-*`). | `run`, `run_tool`, `tasks`, `logs`, `stop` |
| `sysd.py` | Démon root (voir §12). | `Dispatcher`, `Server`, `main` |
| `sandbox.py` | Runtime hybride du bac à sable (root) : `systemd-run` (namespaces, cgroups v2, seccomp), `unshare` + `seccomp_launch.py` en repli, gVisor si présent ; dossiers de travail dans `/var/lib/djerba-ai-runs`. | `Sandbox`, `probe`, `choose` |
| `seccomp_launch.py` | Filtre seccomp-bpf (réseau non-UNIX, ptrace, montages, namespaces, modules noyau, bpf, io_uring… refusés). | `install`, `main` |
| `audit.py` | Registre d'audit immuable `/var/log/djerba/audit.log` : JSON par ligne, **chaîne de hachages**, attribut append-only, tête dans `/var/lib/djerba-ai/audit.head`, rotation interne. | `record`, `tail`, `verify` |
| `trust.py` | Vérification de l'appelant du démon root au-delà de SO_PEERCRED : exécutable et script comparés au manifeste `trusted-callers.sha256`. | `verify`, `check`, `OPEN_METHODS` |
| `ipc.py` | JSON-RPC 2.0 ligne par ligne sur socket UNIX, flux de notifications (`call_stream`). | `call`, `call_stream`, `RPCError` |
| `config.py` | Configuration centrale (voir §11) : schéma, validation, TOML, vue publique. | `Store`, `Config`, `SCHEMA` |
| `telemetry.py` | Métriques Prometheus natives (`http://127.0.0.1:9464/metrics`, `/health`). | `Registry`, `Exporter` |
| `health.py` | Bilan de santé sans root. | `report` |
| `executor.py` | Exécution des outils côté utilisateur. | `Executor` |
| `shortcuts.py` | Raccourcis globaux Plasma (kglobalaccelrc + D-Bus KGlobalAccel). | `apply`, `validate` |
| `gpu.py` | Détection rendu 3D ou repli 2D (orbe de l'interface). | `detect` |
| `sanitize.py` | Masquage des secrets avant écriture. | `sanitize` |
| **`dpkgstate.py`** | Santé de dpkg (voir §15). | `check`, `repair`, `auto`, `schedule`, `watch`, `conflicts`, `busy` |
| **`transactions.py`** | Journal de transactions du démon root (voir §15 bis) : sauvegarde avant action, relevé des chemins touchés, sondes avant/après, **rollback automatique** sur régression ou échec partiel, undo, undo depuis une date, conflits, rétention. | `Journal`, `Origin`, `TransactionError` |
| **`snapshots.py`** | Sauvegardes : `BtrfsBackend` (instantanés, `btrfs send --no-data` → chemins exacts), `FilesBackend` (copie de /etc, /usr/local, /boot + manifeste) ; restauration commune ; `tree_digest`. | `detect`, `restore`, `parse_dump`, `Changes` |
| **`policy.py`** | **Moteur de politique** (démon root) : niveaux 0–3, interdits en dur, listes blanches, modes, contenu non fiable, plafonds par moteur, débit, boucles, disjoncteur ; `check()` côté agent (repli local prudent). Voir §13 bis. | `Policy`, `Rules`, `Decision`, `Origin`, `check` |
| **`engines.py`** | **Registre des moteurs** (phase 3) : moteurs intégrés et enregistrés (`/etc/djerba/engines.json` 0600, vue publique `engines.public.json`), fabrique de fournisseurs, relais root (`engine_request`), appairage Jarvis, mesure du profil de capacité. Voir §13 ter. | `EngineStore`, `catalog`, `find`, `make_provider`, `pair_jarvis`, `measure` |
| **`mcp.py`** | **Serveur MCP** (Djerba serveur d'outils pour Jarvis) : appairage par code, jetons hachés, révocation, débit, anti-force brute ; chaque appel passe par la politique. | `ClientStore`, `MCPServer`, `serve` |
| **`probes.py`** | Sondes de santé déterministes (unités en échec, dpkg, NetworkManager, route, résolveur, unités ciblées) et détection de régression. | `collect`, `regressions` |
| **`bootguard.py`** | Rollback au démarrage : clone `@djerba-last-good`, compteur GRUB sur l'ESP, entrée de restauration, rendre définitif. | `BootGuard`, `main` |

### 9.2 `gui/`

| Fichier | Rôle |
|---|---|
| `app.py` | Application PyQt6 : `Backend` exposé au QML (état, messages, confirmations, moteurs, modèles, réglages, télémétrie, audit, Studio), `Tray`, flou KWin, fournisseur d'icônes, écoute de l'instance unique. |
| `instance.py` | Instance unique sans Qt (socket `/run/user/UID/djerba-ai/gui.sock`) : `--hud`, `--ask`, `--page`, requêtes des intégrations (`--ask-file`, `--ask-selection`). |
| `qml/main.qml` | Fenêtre principale (navigation : Discussion, Système, Auto-réparation, Studio, Capacités, Réglages, Tableau de bord). |
| `qml/ChatPage.qml`, `DashboardPage.qml`, `SettingsPage.qml`, `StudioPage.qml`, `Onboarding.qml` | Pages. |
| `qml/Spotlight.qml` | Barre flottante (Meta+Espace). |
| `qml/Hud.qml` | Anneau d'action (Super+Espace). |
| `qml/Orb3D.qml`, `Orb2D.qml` | Orbe animé (Qt Quick 3D ou repli 2D). |
| `qml/ActionButton.qml`, `Body.qml`, `Glass.qml`, `Pill.qml`, `Switch2.qml`, `Title.qml` | Composants. |

### 9.3 `ui/`

| Fichier | Rôle |
|---|---|
| `adapter.py` | Abstraction du bureau : `KdeAdapter` (script KWin, KGlobalAccel, kdeglobals, outils externes Kate), `GnomeAdapter` (extension, raccourcis GSettings, libération de Super+Espace, color-scheme, greffon Gedit), `GenericAdapter` ; `detect`, `sync` (chaque étape isolée), **`safe_sync`** (ne lève jamais). État : `~/.local/state/djerba-ai/desktop.json`. |
| `hud.py` | Actions de l'anneau : familles d'applications (navigateur, fichiers, terminal, éditeur, jeu, bureautique, courrier, multimédia, réglages), catalogue déterministe + suggestions de l'agent validées (`HudAction`). |
| **`session_guard.py`** | Mode dégradé silencieux : `guarded()`, `report()` (journal local + RPC `session.report`), `collect_gnome()` (rapport de plantage GNOME avec extrait du journal). |

### 9.4 `security/`

| Fichier | Rôle |
|---|---|
| `apparmor.py` | Profils AppArmor générés pour chaque capacité/application de l'IA (`/etc/apparmor.d/djerba-generated/`), vérification de syntaxe, chargement. |
| `ids.py` | IDS : `journalctl -f -o json` → règles à fenêtre glissante (force brute SSH 5/5 min, balayage de ports 15 ports/60 s, force brute locale sudo/polkit/connexion, refus AppArmor des profils `djerba-*`) → blocage nftables à durée de vie (table `inet djerba_ids`, 60 min) ou alerte ; état publié dans `/run/djerba-ai/ids.json` ; audit. |
| `netpolicy.py` | Confiance zéro réseau pour le code généré (accès réseau accordé explicitement depuis le tableau de bord : `security.network_grants`). |
| `plugin_runner.py` | Exécution isolée d'une capacité générée dans un processus distinct. |

### 9.5 `tools/`

| Fichier | Rôle |
|---|---|
| `base.py` | `BaseTool` (nom, catégorie, description, paramètres, `required`, `binaries`, `requires_root`, `dangerous`, `schema()` au format outils Ollama/OpenAI, `validate`, `run`), `CommandTool`, `FunctionTool`, `ok`/`fail`, `run_command`. |
| `__init__.py` | Registre par introspection, recherche par jetons, plugins (`load_plugins`, `IsolatedPluginTool`). |
| `agentic/`, `apps/`, `desktop/`, `devtools/` (+ `app_generator.py`, `templates/`), `hardware/`, `maintenance/`, `os/` (`files.py`, `guard.py`, `packages.py`, `permissions.py`, `search.py`), `performance/` (+ `game_optimizer.py`), `security/`, `system/` | Familles de capacités (voir §10). |

---

## 10. Capacités de l'agent (265 outils)

Notation : `*` = exécuté par le démon root, `!` = dangereux (confirmation). Les outils root sont `mutates` (exécutés dans une transaction annulable) sauf les 15 outils de lecture : apparmor_status, auth_failures, btrfs_scrub_status, btrfs_usage, grub_entries, listening_ports, nft_ruleset, smart_health, smart_scan, snapshot_create, snapshot_delete, snapshot_list, suid_binaries, ufw_status, unattended_upgrades_log.

* **agentic (46)** : transactions_list, transaction_undo, engines_list, engine_use, audit_log, audit_verify, brain_switch!, capability_create, context_now, conversations_list, daemon_status, dlp_vault, engine_handshake, engine_status, feedback, ghost_list, ghost_logs, ghost_run, ghost_stop, health_report, incident_analyze, incidents_list, learning_stats, lesson_record, lessons_list, memory_forget!, memory_recall, memory_remember, model_list, model_pull!, model_recommend, model_set, ollama_install!, patch_deploy!, patch_list, patch_propose, patch_rollback!, self_update*!, settings_get, settings_history, settings_set!, tools_categories, tools_search, venv_install!, web_fetch, web_search.
* **apps (27)** : apps_catalog, apps_install*!, apps_remove*!, apps_retry_pending*, apps_status, apt_history, apt_install*!, apt_remove*!, default_apps, default_browser_set, flatpak_install*!, flatpak_list, flatpak_remotes, flatpak_remove*!, flatpak_search, heroic_install*!, lutris_install*!, multilib_enable*, multilib_status, open_discover, open_store, package_files, package_owner, proton_install_ge, proton_list, steam_install*!, wine_install*!.
* **desktop (26)** : audio_default, audio_devices, colorscheme_apply, colorschemes_list, dark_mode, desktop_apps, keyboard_layout, lock_screen, mute_toggle, night_light, notify, open_app, open_welcome, screen_mode!, screen_scale, screen_vrr, screens_list, screenshot, theme_apply!, themes_list, undercover_off, undercover_on, undercover_status, volume_get, volume_set, wallpaper_set.
* **devtools (19)** : app_delete!, app_export, app_generator, app_history, app_iterate, app_open_folder, app_revert!, app_run, apps_generated_list, compile_c, compile_rust, git_status, plugin_disable, plugin_enable!, plugin_generate, plugins_list, python_check, shellcheck_file, studio_templates.
* **hardware (20)** : battery, block_devices, bluetooth_connect, bluetooth_devices, bluetooth_disconnect, bluetooth_pair, bluetooth_power, bluetooth_scan, bluetooth_status, gamepads, hardware_summary, interface_details, network_connectivity, network_devices, pci_devices, rfkill_list, rfkill_unblock, usb_devices, wifi_connect, wifi_list.
* **maintenance (31)** : apt_autoremove*!, apt_clean*, apt_fix_broken*!, apt_full_upgrade*!, apt_policy, apt_search, apt_show, apt_update*, apt_upgradable, boot_analysis, btrfs_balance*!, btrfs_scrub_start*, btrfs_scrub_status*, disk_usage, dpkg_configure_pending*, failed_units, flatpak_unused*!, flatpak_update*, fstrim_all*, journal_errors, journal_unit, journal_vacuum*, largest_dirs, package_installed, service_disable*!, service_enable*!, service_restart*!, service_status, smart_health*, smart_scan*, user_cache_clean!.
* **os (18)** : os_chmod, os_dir_create, os_dir_list, os_file_read, os_file_search, os_file_write!, os_index_build, os_path_copy, os_path_delete!, os_path_move!, os_pkg_install*, os_pkg_purge*!, os_pkg_remove*!, os_pkg_update*, os_semantic_search, os_sys_chmod*!, os_sys_chown*!, os_sys_file_write*! (chemins vitaux refusés par `tools/os/guard.py`).
* **performance (27)** : cgroup_cpu_weight, cgroup_top, cpu_frequencies, cpu_governor, cpupower_governor*, drop_caches*, fps_limit, game_optimizer, gamemode_status, gpu_info, io_schedulers, kill_process!, kwin_compositor, kwin_effects, kwin_tearing, load_average, mangohud_toggle, memory_usage, power_profile_get, power_profile_set, renice_process, swappiness_get, swappiness_set*, temperatures, top_cpu, top_memory, zram_status.
* **security (30)** : active_connections, admin_users, anonsurf_new_identity*, anonsurf_start*, anonsurf_status, anonsurf_stop*, antivirus_scan, antivirus_update*, antivirus_version, apparmor_status*, auth_failures*, dns_flush*, dns_query, dns_status, listening_ports*, mac_randomize*!, mac_restore*!, nft_ruleset*, password_strength, public_ip, secure_delete!, security_updates, suid_binaries*, ufw_allow_port*!, ufw_delete_rule*!, ufw_deny_port*, ufw_disable*!, ufw_enable*, ufw_status*, unattended_upgrades_log*.
* **system (21)** : btrfs_usage*, cgroup_tree, grub_entries*, grub_timeout*!, grub_update*, hostname_set*!, initramfs_update*, kernel_cmdline, locale_status, os_info, poweroff!, reboot!, sddm_autologin*!, sddm_theme, snapshot_create*, snapshot_delete*!, snapshot_list*, suspend!, time_status, timezone_set*, users_list.

Lister/rechercher : `djerba-ai tools [mot]` ; exécuter : `djerba-ai run NOM '{"param": …}' [--verbose]`.

---

## 11. Configuration centrale (`/etc/djerba/config.toml`)

Fichier root 0600, écrit uniquement par le démon root (`config.set`) ; vue publique sans secrets `/etc/djerba/config.public.json` (0644). Clés (valeur par défaut) :

| Clé | Défaut | Rôle |
|---|---|---|
| `llm.provider` | `local` | `local` / `tailscale` / `cloud` (ancien réglage, repris si `llm.engine` est vide) |
| `llm.engine` | `""` | Moteur actif du registre (`local`, `tailscale`, `cloud` ou identifiant enregistré) |
| `llm.fallback_to_local` | `true` | Repli transparent sur Ollama local |
| `llm.remote_timeout` | `5` | Délai (s) du contrôle de santé distant |
| `llm.context_window` | `8192` | `num_ctx` |
| `llm.embedding_model` | `nomic-embed-text` | Embeddings |
| `llm.local.endpoint` | `http://127.0.0.1:11434` | Ollama local |
| `llm.local.model` | `""` (choisi selon la RAM) | Modèle local |
| `llm.tailscale.host` / `.port` / `.model` | `""` / `11434` / `qwen2.5:32b` | Ollama distant (MagicDNS ou 100.x) |
| `llm.cloud.service` / `.endpoint` / `.model` | `openai` / `""` / `gpt-4o-mini` | API compatible OpenAI |
| `llm.cloud.api_key` | `""` (**secret**, jamais exposé) | Clé d'API |
| `policy.mode` | `autonome` | Mode d'autonomie (`prudent`, `autonome`, `total`) — **seulement** par `policy.set_mode` (administrateur) |
| `policy.engine_caps` | `[]` | Plafond de niveau par type de moteur (`cloud:1`…) — **seulement** par `policy.set_caps` |
| `agent.autonomy` | `ask` | Ancien réglage (remplacé par `policy.mode`, conservé pour compatibilité) |
| `agent.web_search`, `agent.self_heal`, `agent.auto_extend`, `agent.auto_update_model` | `true` | Fonctions de l'agent |
| `agent.maintenance_hour` | `12` | Maintenance quotidienne |
| `agent.plugins_approved` | `[]` | Capacités générées approuvées |
| `shortcuts.spotlight` / `shortcuts.clipboard` | `Meta+Space` / `Meta+Shift+C` | Raccourcis |
| `system.sandbox_timeout` / `sandbox_attempts` | `15` / `3` | Tests du bac à sable |
| `system.watchdog`, `system.disk_alert_percent`, `system.audit_max_mb` | `true`, `10`, `10` | Surveillance |
| `sandbox.backend` / `memory_mb` / `cpu_percent` | `auto` / `512` / `50` | Bac à sable |
| `security.ids` / `ids_block_minutes` / `network_grants` | `true` / `60` / `[]` | Défense active |
| `context.interval` / `context.window_titles` | `5` / `true` | Bus de contexte |
| `telemetry.enabled` / `telemetry.port` | `true` / `9464` | Prometheus |
| `ui.onboarded` | `false` | Assistant de premier lancement |
| `mcp.enabled` / `mcp.port` | `false` / `8765` | Serveur MCP lié à l'adresse Tailscale |

---

## 12. Démon root `djerba-ai-sysd` et protocole JSON-RPC

* Socket : `/run/djerba-ai/sysd.sock` (activation `djerba-ai-sysd.socket`), service `djerba-ai-sysd.service`.
* Enveloppe JSON-RPC 2.0, une requête par ligne ; paramètres nommés validés par `schemas.RPC_PARAMS` (types exacts, champs inconnus refusés).
* Contrôles : processus isolés (scopes `djerba-app-*`, `djerba-ghost-*`) refusés ; **appelant de confiance** (`trust.verify`) sauf `OPEN_METHODS` (`sys.ping`, `sys.metrics`, `config.get`) ; **polkit** par action ; **audit** avant (demande) et après (action/erreur/refus), sauf méthodes `QUIET`.

| Méthode | Polkit | Effet |
|---|---|---|
| `sys.ping`, `sys.metrics` | — | Version, métriques |
| `sys.exec_tool {name, params, verbose}` | `org.djerba.ai.exec` | Exécute un outil marqué root |
| `sys.test_patch {target, content, reason}` | `org.djerba.ai.patch` | Teste un correctif d'un outil `djerba-*` en bac à sable (Git) |
| `sys.deploy_patch {patch_id}` / `sys.rollback_patch {name}` | `org.djerba.ai.deploy` | Déploiement atomique dans `/usr/local/bin`, retour arrière |
| `sys.list_patches` | — | Correctifs proposés et déployés |
| `sys.install_ollama` | `org.djerba.ai.setup` | Ollama officiel (SHA-256 vérifié) + service |
| `sys.test_plugin {name, content, tests, requirements}` | `org.djerba.ai.extend` | Capacité générée : syntaxe, pyflakes, chargement, Pytest (15 s/test), venv temporaire, Git, nettoyage en cas d'échec |
| `sys.venv_install {packages}` | `org.djerba.ai.setup` | Dépendances dans le venv interne |
| `sys.sandbox_probe {backend}` | `org.djerba.ai.extend` | Sonde du bac à sable |
| `config.get` / `config.set {key, value}` | — / `org.djerba.ai.configure` | Configuration centrale |
| `llm.cloud_chat {messages, tools}` / `llm.cloud_health` / `llm.cloud_models` | `org.djerba.ai.cloud` | Relais API cloud, jetons en notifications `llm.token` |
| `audit.tail {lines}` / `audit.verify` | `org.djerba.ai.audit` | Lecture et vérification de la chaîne |
| `security.status` / `security.unblock {ip}` / `security.apparmor_load {kind, name, network}` | audit / configure / extend | IDS, AppArmor |
| **`session.report {desktop, component, detail}`** | — (appelant de confiance, 30/h/uid) | Écrit `/var/log/djerba/<bureau>-crash.log` (root:adm 0640, rotation 1 Mio) |
| `tx.list {limit, since}` / `tx.show {id}` | `org.djerba.ai.audit` | Journal de transactions |
| `tx.undo {id, force}` / `tx.undo_since {since}` | `org.djerba.ai.undo` | Annulation depuis la sauvegarde |
| `policy.check {tool, params, origin, commit}` | — (+ `org.djerba.ai.confirm` / `critical` si requis) | Décision de la politique pour un outil de l'utilisateur |
| `policy.status` | — | Mode, pause, plafonds, derniers refus |
| `engines.list` | — | Registre public des moteurs (sans secrets) |
| `engines.save {engine, secret}` / `engines.remove {id}` / `engines.set_profile {id, profile}` / `engines.pair_jarvis {endpoint, code, …}` | `org.djerba.ai.configure` (+ politique) | Gestion des moteurs ; le jeton Jarvis reste root |
| `llm.engine_chat {id, messages, tools, json_mode}` / `llm.engine_health {id}` | `org.djerba.ai.cloud` | Relais vers un moteur à secret (Jarvis, Anthropic, API en ligne), jetons `llm.token` |
| `mcp.pair_begin {name, cap}` / `mcp.revoke {client}` | `org.djerba.ai.configure` | Code d'appairage MCP, révocation |
| `mcp.pair_complete {code, name}` / `mcp.verify {token}` | — (appelant de confiance) | Échange code → jeton, vérification de jeton (serveur MCP) |
| `mcp.list` | `org.djerba.ai.audit` | Appelants MCP |
| `policy.set_mode {mode}` / `policy.set_caps {caps}` / `policy.resume` | `org.djerba.ai.policy` (auth_admin) | Changement de la politique, reprise après pause |
| `policy.pause {reason}` | `org.djerba.ai.undo` | Disjoncteur : plus aucune action de niveau ≥ 1 |

Les méthodes qui modifient le système (`sys.exec_tool` d'un outil `mutates`, correctifs, `sys.install_ollama`, `sys.venv_install`, `config.set`, `security.apparmor_load`) acceptent un champ facultatif `origin {engine, session}` et s'exécutent **dans une transaction** (§15 bis).

---

## 13. Sécurité : modèle de menace et défenses

| Menace | Défense |
|---|---|
| Injection de prompt (pages web, fichiers, titres de fenêtres, sorties d'outils) | `guard.py`, contenus encadrés comme non fiables, session « tainted », confirmations renforcées |
| Action destructrice par l'IA | Niveaux de risque, confirmations, « CONFIRMER » pour le haut risque, chemins vitaux refusés, polkit côté root |
| Programme local qui détourne l'agent | `ptrace_scope=1`, appelant vérifié par empreinte, SO_PEERCRED, processus isolés refusés |
| Fuite de secrets | Clé d'API root seulement, DLP avant cloud, masquage avant journalisation, chiffrement AES-GCM |
| Code généré malveillant | Bac à sable (namespaces, seccomp, cgroups, sans réseau), Pytest obligatoire, AppArmor généré, réseau en confiance zéro, exécution dans un processus distinct |
| Falsification des traces | Audit chaîné append-only, vérification `audit.verify` |
| Intrusion réseau / force brute | IDS + nftables temporaires, UFW |
| Fuite hors Tor (mode anonyme) | Coupe-circuit nftables appliqué avant Tor, IPv6/UDP bloqués, annulation si Tor échoue |

---

## 13 bis. Politique d'autonomie (phase 2)

* **L'agent demande, le démon décide** : `core/policy.py` s'exécute dans `djerba-ai-sysd`. Les outils root sont contrôlés à l'exécution (`sys.exec_tool`), les méthodes qui modifient le système aussi (`METHOD_TOOLS` : correctifs, Ollama, venv, réglages, AppArmor) ; les outils de l'utilisateur sont soumis par l'agent à `policy.check` (sans démon : décision locale plafonnée au niveau 1). Le modèle ne fait que **proposer** des appels.
* **Politique versionnée** `/usr/share/djerba-ai/policy.toml` (livrée par le paquet, lecture seule, chemin interdit à tous les outils) : niveau de chaque outil, racines de l'écriture root (`/etc`, `/usr/local`, `/opt`, `/srv`), plafonds (20 actions/min par session, 60 en 10 min, même action 3 fois/h au plus). Un test vérifie que chaque outil root qui modifie le système a un niveau explicite et qu'aucun nom inconnu n'y figure.
* **Niveaux** : 0 lecture (libre) ; 1 réversible et local (autonome, transaction) ; 2 système sensible (autonome, transaction, vérification, rollback, notification après coup) ; 3 irréversible (`secure_delete`, `os_path_delete`, `memory_forget`, `app_delete`, `snapshot_delete` : refusé sauf mode « total », authentification administrateur à chaque fois). Outil root inconnu → niveau 2.
* **Interdits en dur** (code, tous modes, après résolution des liens et de `..`) : `/boot`, ESP, GRUB, Secure Boot (`/etc/secureboot`, `/var/lib/shim-signed`), `/etc/shadow`, `/etc/gshadow`, sudo, polkit, PAM, `/etc/security`, `/etc/djerba`, `/etc/apparmor.d`, code et politique de l'agent, démon root, journaux (audit, transactions), `/.snapshots`, `/proc`, `/sys`, `/dev`.
* **Modes** (`policy.mode`) : « prudent » (confirmation **hors bande** par polkit `org.djerba.ai.confirm`, mot de passe de l'utilisateur, pour tout niveau ≥ 1), « autonome » (défaut : aucune confirmation jusqu'au niveau 2), « total » (niveau 3 possible, `org.djerba.ai.critical`). Changement : `policy.set_mode` (`org.djerba.ai.policy`, auth_admin) — `config.set` refuse toute clé `policy.*`.
* **Contenu non fiable** (`origin.tainted`, posé par l'agent quand la garde anti-injection détecte une sortie d'outil, une page web, un fichier ou une réponse de moteur suspecte) : niveau plafonné à 1 jusqu'à la fin de la tâche.
* **Plafond par moteur** (`policy.engine_caps`, ex. `cloud:1`) : même décision pour tous les moteurs à plafond égal.
* **Disjoncteur** : `djerba-ai pause [raison]` (`/var/lib/djerba-ai/paused`), `djerba-ai resume` (administrateur) ; boucles (même action répétée) et débits bornés.
* **Traçabilité** : chaque refus est inscrit au journal d'audit (`refus politique`, niveau, mode, moteur, session, contenu non fiable), compté (`djerba_policy_refusals_total`) et listé par `djerba-ai policy`.

## 13 ter. Moteurs d'IA unifiés, Jarvis et MCP (phase 3)

* **Registre** (`core/engines.py`) : moteurs intégrés `local`, `tailscale`, `cloud` (issus de `llm.*`) et moteurs enregistrés (`ollama`, `jarvis`, `anthropic`, `openai`, `gemini`, `openai_compat`) dans `/etc/djerba/engines.json` (root 0600, secrets) ; vue publique `engines.public.json`. Moteur actif : `llm.engine`. CLI : `djerba-ai engines [list|add|pair-jarvis|use|test|measure|remove]`.
* **Interface unique** : Ollama (local ou distant) en direct ; moteurs à secret par `RelayProvider` → `llm.engine_chat` du démon root (le jeton ne quitte jamais root). Anthropic : API Messages en flux SSE (`anthropic_request`).
* **Les outils appartiennent à Djerba** : modèle sans appels d'outils natifs → repli « JSON contraint » (`core/engine.py`, `structured_chat` : consigne décrivant les outils, `format: json`, validation stricte, 3 essais). Suite de parité : même scénario, mêmes actions sur chaque moteur.
* **Profil de capacité** mesuré à l'ajout (`measure` : appel d'outil, JSON, code) → « Très adapté » (plafond 3), « Adapté » (2), « Limité : réparations simples seulement » (1) ; plafond appliqué par la politique (`engine_cap`).
* **Confidentialité** : DLP sur tout message vers un moteur distant ; règles `share` par moteur (contexte système, mémoire) ; réponse anormale (outil non proposé, indices d'injection) → tâche « tainted » (niveau 1).
* **Bascule à chaud** : le moteur est relu à chaque tâche, l'historique de la conversation suit ; moteur distant injoignable → repli Ollama local (`llm.fallback_to_local`).
* **MCP** (`core/mcp.py`, `djerba-ai mcp-serve`, unité utilisateur `djerba-ai-mcp.service`, inactif tant que `mcp.enabled` est faux) : JSON-RPC sur HTTP lié à l'adresse Tailscale ; appairage par code à 6 chiffres / QR (`djerba-ai mcp pair NOM`), jetons hachés, révocation, 60 appels/min, origine `mcp-<client>` avec plafond (2 par défaut). Contrat complet : `docs/JARVIS-CONTRAT.md`.

## 14. Multi-bureau (KDE Plasma / GNOME)

* **KDE Plasma** (défaut) : script KWin `active-window.js` chargé par D-Bus, raccourcis KGlobalAccel, outils externes Kate, menu Dolphin, tray, autostarts.
* **GNOME** (optionnel, entrée catalogue `gnome-desktop`) : paquets `gnome-session`, `gnome-session-xsession`, `gnome-shell`, `gnome-settings-daemon`, `mutter`, `xwayland`, `dbus-user-session`, `gnome-keyring`, `gsettings-desktop-schemas`, `gnome-control-center`, `gnome-console`, `gnome-text-editor`, `nautilus`, `python3-nautilus`, `gedit`, `gnome-shell-extension-prefs`, `xdg-desktop-portal-gnome` ; **`gdm3` exclu** (SDDM reste le gestionnaire de connexion) ; vérification post-installation (`/usr/share/wayland-sessions/gnome.desktop`, binaires, schéma `org.gnome.shell`, Xwayland).
* **Extension** `djerba@djerba-os.org` (GNOME 46–49) : fenêtre active → `org.djerba.ai.Context.ActiveWindow`, bouton HUD ; erreurs contenues.
* **Mode dégradé** : `safe_sync`, `guarded` ; rapports dans `~/.local/state/djerba-ai/gnome-crash.log` et `/var/log/djerba/gnome-crash.log`.
* **Plantage de GNOME Shell/gnome-session** : compléments systemd utilisateur `org.gnome.Shell@{wayland,x11}.service.d/djerba-crash-report.conf` (`OnFailure=`) et `gnome-session-failed.target.d/` → `djerba-gnome-crash.service` → `djerba-ai session-report gnome ${MONITOR_UNIT}`.
* **Cause historique du « retour à l'écran de connexion »** : l'installation GNOME tirait `desktop-base` → conflit dpkg avec l'ancien `djerba-branding` → GNOME à moitié installé. Corrigé (§6, §15) et vérifié en CI (installation + démarrage de GNOME Shell avec l'extension).

---

## 15. Santé de dpkg et réparation automatique

Module `core/dpkgstate.py`, exécutable `/usr/libexec/djerba-dpkg-repair`.

* **Détection (sans privilège)** : `dpkg --audit`, fichiers en attente dans `/var/lib/dpkg/updates`, transactions en cours ignorées (verrous fcntl de `lock-frontend`/`lock` repérés dans `/proc/locks`).
* **Réparation (root)** : attente du verrou (10 min max), `dpkg --force-confdef --force-confold --configure -a`, `apt-get -f install` ; pour un conflit « trying to overwrite … which is also in package djerba-* » : `dpkg -i --force-overwrite` **sur la seule archive concernée** ; conflits entre paquets tiers seulement signalés. Journal : `/var/log/djerba/dpkg-repair.log`.
* **Déclencheurs** : crochet APT `DPkg::Post-Invoke` (exécuté même après un échec ; programme `--auto` via `systemd-run --on-active=5`), postinst de `djerba-branding`, `djerba-dpkg-repair.timer` (2 min après le démarrage puis toutes les 6 h), `djerba-apps` avant/après installation.
* **Interface** : veille de session (`--watch`, autostart) → notification critique « Gestionnaire de paquets à réparer » avec actions **Réparer** (pkexec, action `org.djerba.dpkg-repair`) / Plus tard ; deux relevés consécutifs requis (pas d'alerte pendant une installation normale).
* Variables de test : `DJERBA_DPKG_DIR`, `DJERBA_PROC_LOCKS`, `DJERBA_DPKG_REPAIR_LOG`, `DJERBA_SYSTEMD_RUN_DIR`.

---

## 15 bis. Réversibilité : transactions, instantanés, rollback au démarrage

* **Transaction** (`core/transactions.py`) pour toute action root qui modifie le système : sondes « avant » (`core/probes.py`) et liste des paquets → **sauvegarde** (si elle échoue, l'action n'est pas exécutée) → action → relevé des chemins touchés et du delta de paquets → sondes « après ». Régression (nouvelle unité en échec, dpkg incohérent, NetworkManager, route ou résolveur perdus, unité ciblée arrêtée) **ou** échec de l'action avec modifications partielles → **annulation automatique** (tout ou rien).
* **Sauvegarde** (`core/snapshots.py`) :
  * **Btrfs** : instantanés en lecture seule `<sommet>/@djerba-snapshots/<id>-avant` (conservé) et `-apres` (supprimé dès le relevé) ; le sommet du volume (`subvolid=5`) est monté dans `/run/djerba-ai/btrfs` ; `btrfs send --no-data -p avant après | btrfs receive --dump` donne les chemins exacts, sans déclaration par l'outil.
  * **Fichiers** (live, ext4, xfs) : copie de `/etc` et `/usr/local` (+ `/boot` pour les outils d'amorçage) dans `/var/lib/djerba-ai/tx/<id>/avant`, manifeste d'empreintes ; les paquets hors de ces dossiers sont annulés par APT (purge des paquets ajoutés, réinstallation des versions retirées ou changées).
  * Jamais restaurés : `/var/log`, caches, `/var/lib/djerba-ai*`, `/var/lib/systemd`, listes APT, baux réseau, `/home`, `.snapshots`…
* **Journal** : SQLite root 0600 `/var/lib/djerba-ai/transactions.db` (`PRAGMA user_version` = 1) — id `tAAAAMMJJhhmmssXXXX`, dates, uid, **moteur d'IA** (`kind:modèle`, transmis par l'agent) et **session** (conversation), méthode, outil, paramètres masqués, sauvegarde, chemins, paquets, sondes, régressions, état (`appliquée`, `sans effet`, `échec`, `annulée`, `annulée automatiquement`, `annulation partielle`, `expirée`). Rétention : 60 dernières transactions, 30 jours.
* **Undo** : `djerba-ai transactions [N]`, `djerba-ai undo ID [--force]`, `djerba-ai undo --since AAAA-MM-JJ[THH:MM]` (antichronologique) ; outils de l'agent `transactions_list`, `transaction_undo`. Refus si une transaction plus récente encore appliquée a touché les mêmes chemins.
* **Rollback au démarrage** (`core/bootguard.py`, `/usr/libexec/djerba-boot`, Btrfs + UEFI, racine non chiffrée) : `djerba-boot-ok.timer` (3 min après l'amorçage) → si le démarrage est sain, compteur remis à 0 et clone inscriptible `@djerba-last-good` rafraîchi (fstab pointé sur lui, noyau/initrd aux noms stables `boot/djerba-vmlinuz`, `boot/djerba-initrd.img`). `/etc/grub.d/09_djerba_bootcount` incrémente `djerba_boot_tries` dans `EFI/djerba/grubenv` sur l'ESP ; à 2, GRUB démarre l'entrée `djerba-last-good` (`/etc/grub.d/42_djerba_rollback`). `djerba-ai rollback --make-permanent` (pkexec, `org.djerba.boot-rollback`) conserve l'état restauré (l'ancien `@` est gardé en `@djerba-broken-<date>`).

## 16. Chemins installés et données à l'exécution

| Chemin | Propriétaire / droits | Contenu |
|---|---|---|
| `/etc/djerba/config.toml` | root 0600 | Configuration centrale |
| `/etc/djerba/config.public.json` | root 0644 | Vue publique |
| `/var/log/djerba/` | root:adm 0750 | `audit.log` (0640, append-only, chaîné), `dpkg-repair.log`, `gnome-crash.log`, `kde-crash.log`… |
| `/var/lib/djerba-ai/` | root 0700 | `audit.head`, `sandbox/` (dépôt Git du bac à sable), correctifs, `transactions.db` (0600), `tx/<id>/` (sauvegardes sans Btrfs) |
| `<sommet Btrfs>/@djerba-snapshots/`, `@djerba-last-good`, `@djerba-broken-*` | root | Instantanés des transactions, dernier bon état, anciens systèmes après rollback |
| `/boot/efi/EFI/djerba/grubenv` | root | Compteur de démarrages lu/écrit par GRUB |
| `/var/lib/djerba-ai-runs/` | root 0711 | Dossiers de travail éphémères du bac à sable |
| `/var/lib/djerba/apps/` | root | `pending.json`, `last-run.json` |
| `/run/djerba-ai/` | root | `sysd.sock`, `ids.json` |
| `/run/djerba-apps/` | root | `status.json`, `lock` |
| `/etc/apparmor.d/djerba-generated/` | root | Profils générés |
| `/usr/local/bin/` | root | Correctifs déployés (prioritaires dans le PATH) |
| `/usr/share/djerba-ai/venv/` | root | venv interne |
| `~/.local/share/djerba/` | utilisateur | `memory.db` (chiffré), `plugins/`, applications du Studio, index |
| `~/.local/state/djerba-ai/` | utilisateur | `desktop.json`, sessions chiffrées, `<bureau>-crash.log` |
| `/run/user/UID/djerba-ai/` | utilisateur | `gui.sock` (instance unique), `context.bin` (bus chiffré) |
| `~/.config/kate/externaltools/djerba-*.ini` | utilisateur | Outils externes Kate |
| `~/.local/share/gedit/plugins/djerba.*` | utilisateur | Greffon Gedit |
| `~/.config/environment.d/90-djerba-mangohud.conf` | utilisateur | MangoHud global (mode jeu) |
| `http://127.0.0.1:9464/metrics`, `/health` | — | Télémétrie |

---

## 17. Services systemd, polkit, autostart

**Système** (`/usr/lib/systemd/system/`) :
* `djerba-ai-sysd.socket` + `djerba-ai-sysd.service` — démon root activé par socket.
* `djerba-ai-ids.service` — IDS.
* `djerba-anonsurf-tor.service` — Tor du mode anonyme.
* `djerba-apps-firstboot.service` — installation des applications choisies au premier démarrage.
* `djerba-dpkg-repair.service` + `.timer` — vérification/réparation de dpkg.
* `djerba-boot-ok.service` + `.timer` — validation du démarrage (compteur GRUB, `@djerba-last-good`).

**Utilisateur** (`/usr/lib/systemd/user/`) :
* `djerba-ai.service` — démon utilisateur (`WantedBy=graphical-session.target`, activé globalement).
* `djerba-ai-mcp.service` — serveur MCP (activé globalement, sans effet tant que `mcp.enabled` est faux).
* `djerba-gnome-crash.service` + compléments `org.gnome.Shell@wayland.service.d/`, `org.gnome.Shell@x11.service.d/`, `gnome-session-failed.target.d/`.
* `djerba-ghost-*` (transitoires) — Ghost Runner ; `djerba-app-*` (scopes) — applications du Studio.

**Actions polkit** : `org.djerba.ai.{exec,patch,deploy,setup,configure,cloud,audit,extend,undo,confirm,critical,policy}`, `org.djerba.apps`, `org.djerba.anonsurf`, `org.djerba.dpkg-repair`, `org.djerba.boot-rollback`, `org.djerba.installer`.

**Autostart** : `djerba-welcome` (KDE), `djerba-ai-tray` (KDE), `djerba-dpkg-watch` (tous bureaux).

---

## 18. Tests

| Niveau | Commande | Contenu |
|---|---|---|
| Lints | `tests/run_local_lints.sh` | shellcheck, `set -euo pipefail`, py_compile, YAML/INI, tests de l'agent, polkit XML, visudo |
| Agent | `python3 tests/test-djerba-ai.py` (dans le conteneur Debian 13 en root : `docker run --privileged -e DJERBA_AI_ROOT_TESTS=1 -e QT_QPA_PLATFORM=offscreen -v $PWD:/build -w /build djerba-os-builder:local python3 tests/test-djerba-ai.py`) | ~99 tests : registre, sécurité, mémoire, agent (faux Ollama), démon root et protocole, correctifs en bac à sable, Studio, configuration, masquage, chiffrement, fournisseurs et repli, audit, outils os, auto-extension, bac à sable des plugins, raccourcis, GPU, instance unique, couche cognitive, anti-injection, durcissement du démon, bus de contexte/télémétrie/Ghost, bureau (KDE/GNOME), HUD, défense active, chargement QML ; **transport Ollama** (connexion fermée sans réponse, reprises, requête minimale, keep-alive, flux bloqué, repli d'adresse, validation du corps, embeddings) ; **dpkg** (analyse des conflits, transactions en cours, réparation, crochet APT, **reproduction réelle du conflit desktop-base avec dpkg et les vrais scripts du paquet**) ; **mode dégradé multi-bureau** (étapes GNOME en échec, `safe_sync`, `session.report`, extension, catalogue GNOME). Environnement isolé par variables `DJERBA_*` et `XDG_*` temporaires. |
| ISO | `tests/test-features.sh ISO` | ≈ 160 contrôles (159 appels `check`) de contenu + chroot |
| Démarrage | `tests/test-iso-qemu.sh ISO` | UEFI headless jusqu'à l'invite de connexion |
| Session live | `tests/test-live-session.sh ISO [--network]` | 46 contrôles avec réseau (35 hors ligne + Tor/DNS/Firefox/GNOME) |

---

## 19. CI/CD GitHub Actions et publication

Fichier : `.github/workflows/build-and-test.yml` — déclenché sur push, pull request, manuel ; `concurrency` avec annulation de l'exécution précédente.

Matrice : `x86_64` (`ubuntu-24.04`, KVM) et `arm64` (`ubuntu-24.04-arm`). Étapes :
1. Checkout, libération d'espace disque, calcul de la version (`AAAA.MM.<n>`).
2. Outils hôte, **lints**, génération des assets.
3. Cache live-build, **construction de l'image Docker et de l'ISO**.
4. **Tests de l'agent** dans le conteneur Debian 13 (root, bac à sable réel).
5. **Tests de contenu de l'ISO**.
6. **Démarrage QEMU UEFI**.
7. **Session live avec réseau** (amd64).
8. Découpage de l'ISO si > 2 Gio, **publication** : assets de la pré-release roulante **`djerba-os-dev`** (`gh release upload --clobber`), ou release « Djerba OS vX » sur un tag `v*` (pas d'artefacts ISO : quota de stockage).
9. Journaux QEMU en artefacts (7 jours).

Téléchargement : https://github.com/dc92jnrvg8-lang/distrib-djerba/releases/tag/djerba-os-dev (`djerba-os-<version>-<arch>.iso` + `.sha256`).

---

## 20. Limites connues, dette technique et pistes d'amélioration

**Limites connues (constatées ou assumées)**
* **Mise à niveau depuis 2026.10.30 dans une seule transaction** (Discover met à niveau `djerba-branding` et installe `desktop-base` ensemble) : apt décompresse `desktop-base` en premier → échec une fois ; la réparation automatique (crochet APT + `djerba-dpkg-repair`) rétablit l'état ensuite. Une solution parfaite exigerait une modification de `desktop-base` (Breaks/Replaces côté Debian).
* **HTTP/2** seulement vers un point d'accès TLS avec `python3-httpx` + `python3-h2` installés ; Ollama parle HTTP/1.1 en clair.
* **GNOME** testé en CI par installation + `gnome-shell --headless` ; une ouverture de session GNOME complète via SDDM n'est pas encore automatisée.
* **arm64** : construit, testé (contenu, démarrage) mais la session live avec réseau n'est jouée que sur amd64.
* **AppArmor** non chargé en session live (comportement Debian) : profils générés vérifiés une fois installé.
* `~/.config/environment.d/90-djerba-mangohud.conf` (mode jeu) s'applique aussi à GNOME : MangoHud apparaît dans les applications Vulkan (GTK 4) — à restreindre aux jeux.
* Pas de traduction anglaise de l'interface (français uniquement).
* `djerba-linux-iso.zip` à la racine : héritage inutilisé.
* `djerba-dpkg-repair` n'est pas dans le PATH (chemin complet `/usr/libexec/…`) : un lien `/usr/sbin/djerba-dpkg-repair` serait plus pratique.

**Pistes d'amélioration**
1. Test CI d'ouverture de session GNOME réelle via SDDM (changement d'autologin + capture QMP + `loginctl`).
2. Session live avec réseau aussi sur arm64.
3. Internationalisation (gettext / Qt Linguist) : anglais, arabe.
4. Mises à jour delta / dépôt APT Djerba signé (aujourd'hui : paquets intégrés à l'ISO).
5. Tableau de bord : graphiques historiques (Prometheus local), export de l'audit.
6. Agent : streaming des appels d'outils, planification multi-étapes visible, évaluation automatique des modèles locaux.
7. Sécurité : profils AppArmor pour `djerba-ai` lui-même et pour le démon root ; signature des correctifs déployés.
8. Installateur : chiffrement complet du disque par défaut (LUKS2 + TPM2).
9. Catalogue : bureaux supplémentaires (XFCE, Cinnamon) sur le même modèle que GNOME (`exclude`, `verify`).

---

## 21. Guide pour contribuer (humain ou IA)

1. **Lire** `README.md` puis ce document ; les modules ont tous une docstring d'en-tête détaillée.
2. **Construire l'environnement** : `docker build -t djerba-os-builder:local .` (Debian 13).
3. **Avant chaque commit** : `tests/run_local_lints.sh` (dans le conteneur pour disposer de pydantic 2, seccomp…), puis les tests de l'agent en root dans le conteneur.
4. **Construire l'ISO** : `sudo ./build.sh --docker` (long) ; contrôler avec `tests/test-features.sh` et `tests/test-iso-qemu.sh`.
5. **Conventions** :
   * français partout (code commenté, messages, commits) ; densité de commentaires et style des fichiers existants ;
   * Bash : `set -euo pipefail`, shellcheck propre ; Python : pyflakes propre, `from __future__ import annotations`, typage ;
   * toute nouvelle méthode root = schéma dans `schemas.RPC_PARAMS` + traitement dans `sysd.Dispatcher.handle` + action polkit si nécessaire + test ;
   * toute nouvelle capacité = classe `BaseTool` (ou `command_tool`/`function_tool`) dans la bonne famille de `tools/`, avec `dangerous`/`requires_root` corrects ;
   * tout nouveau fichier installé = sous `packages/<paquet>/root/…`, conffile si dans `/etc`, contrôle ajouté à `tests/test-features.sh` ;
   * ne jamais versionner de certificat de proxy local (`config/includes.chroot/usr/local/share/ca-certificates/local-proxy-*.crt`) ;
   * ne jamais dérouter un conffile ; ne jamais revendiquer un fichier d'un paquet Debian (utiliser un dossier prioritaire ou une diversion de fichier non-conffile).
6. **Valider en CI** : pousser sur la branche de travail ; vérifier les 2 jobs (x86_64, arm64) et la publication sur `djerba-os-dev`.
