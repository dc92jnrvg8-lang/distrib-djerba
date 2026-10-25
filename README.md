# 🏝️ Djerba OS

**Djerba OS** est une distribution Linux live et installable, légère, sécurisée et
taillée pour le jeu, construite sur **Ubuntu 24.04 LTS** (« noble ») avec un
**noyau à faible latence**, le bureau **KDE Plasma sous Wayland**, l'installateur
**Calamares** et une identité visuelle « lagon de Djerba » en verre dépoli.

L'ISO produite est un **Live-CD hybride** (CD/DVD ou clé USB), amorçable en
**UEFI x86_64** (Secure Boot via shim et GRUB signés par Canonical, plus BIOS
hérité) et en **UEFI ARM64**.

| | x86_64 | ARM64 |
|---|---|---|
| Amorçage | UEFI (Secure Boot) + BIOS, iso-hybrid | UEFI, iso-hybrid |
| Noyau | `linux-image-lowlatency` (repli `generic`) | `linux-image-lowlatency` (repli `generic`) |
| Bureau | Plasma 5.27 LTS Wayland, SDDM | Plasma 5.27 LTS Wayland, SDDM |
| Jeu | GameMode, MangoHud, Vulkan, Steam, Wine 32/64 bits | GameMode, MangoHud, Vulkan |
| Applications | Firefox (dépôt Mozilla), Flathub via Discover | idem |
| Installation | Calamares, BTRFS `@`/`@home`, zstd, sans swap disque | idem |

---

## Sommaire

1. [Démarrage rapide](#démarrage-rapide)
2. [Architecture du dépôt](#architecture-du-dépôt)
3. [Chaîne de construction](#chaîne-de-construction)
4. [Tests](#tests)
5. [CI/CD GitHub Actions](#cicd-github-actions)
6. [Le système Djerba OS](#le-système-djerba-os)
7. [Identité visuelle](#identité-visuelle)
8. [Choix techniques et écarts assumés](#choix-techniques-et-écarts-assumés)
9. [Personnalisation](#personnalisation)
10. [Dépannage](#dépannage)
11. [Licences](#licences)

---

## Démarrage rapide

Pré-requis : un hôte Linux **de la même architecture que l'ISO visée**
(x86_64 pour amd64, aarch64 pour arm64), Docker, ~40 Go d'espace libre et une
connexion Internet.

```bash
git clone https://github.com/dc92jnrvg8-lang/distrib-djerba.git
cd distrib-djerba

# Construit l'image Docker, puis lance lints + assets + live-build dedans.
sudo ./build.sh --docker

# Résultat
ls artifacts/
#   djerba-os-AAAA.MM-amd64.iso  djerba-os-AAAA.MM-amd64.iso.sha256  ...

# Tests
sudo docker run --rm --privileged -v "$PWD:/build" -w /build djerba-os-builder:local \
    ./tests/test-features.sh artifacts/djerba-os-*-amd64.iso
sudo docker run --rm --privileged -v "$PWD:/build" -w /build djerba-os-builder:local \
    ./tests/test-iso-qemu.sh artifacts/djerba-os-*-amd64.iso
```

Écrire l'ISO sur une clé USB :

```bash
sudo dd if=artifacts/djerba-os-AAAA.MM-amd64.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

Options de `build.sh` :

| Option | Effet |
|---|---|
| `--arch amd64\|arm64` | architecture cible (défaut : celle de l'hôte) |
| `--docker` | construit `Dockerfile` puis relance le build dans le conteneur (mode privilégié) |
| `--skip-lints` | ne relance pas `tests/run_local_lints.sh` |
| `--skip-assets` | ne régénère pas l'identité visuelle |
| `--purge` | vide aussi le cache de paquets live-build |
| `--retries N` | nombre de tentatives de `lb build` (reprise par étapes, défaut 3) |

Variables d'environnement : `DJERBA_VERSION`, `DJERBA_LOCALE` (défaut
`fr_FR.UTF-8`), `DJERBA_KEYMAP` (défaut `fr`).

---

## Architecture du dépôt

```
.
├── .github/workflows/build-and-test.yml   # CI : matrice x86_64 / arm64 natifs
├── assets_generator/
│   ├── generate_assets.py                 # jetons de design -> tous les visuels
│   └── templates/
│       ├── grub-splash.png.j2             # scène SVG (GRUB, fonds Plasma/SDDM/Plymouth)
│       ├── plymouth.qml.j2                # motion design de l'animation de boot (QtQuick)
│       ├── sddm.qml.j2                    # thème SDDM (QML Qt 5)
│       └── calamares-theme.j2             # branding.desc + show.qml + stylesheet.qss
├── auto/
│   ├── config                             # lb config : noble, miroirs, EFI, squashfs…
│   ├── build                              # lb build + MKSQUASHFS_OPTIONS
│   └── clean                              # lb clean + fichiers régénérés
├── config/
│   ├── package-lists/                     # 01 base · 02 bureau · 03 jeu (#if natifs)
│   ├── hooks/normal/                      # 0100 → 0500 (hooks chroot)
│   ├── preseed/djerba.cfg.chroot          # réponses debconf (clavier, locales, Steam)
│   └── includes.chroot/                   # arborescence copiée telle quelle dans le système
│       ├── etc/apt/{keyrings,sources.list.d,preferences.d}/   # dépôt Mozilla, pas de snap
│       ├── etc/calamares/settings.conf + modules/*.conf
│       ├── etc/flatpak/remotes.d/flathub.conf
│       ├── etc/netplan/01-network-manager-all.yaml
│       ├── etc/polkit-1/rules.d/99-djerba-installer.rules
│       ├── etc/sddm.conf.d/10-wayland-autologin.conf
│       ├── etc/sudoers.d/live-user
│       ├── etc/skel/.config/                # Plasma sombre, flou KWin, Inter…
│       ├── etc/xdg/autostart/djerba-welcome.desktop
│       ├── usr/local/bin/djerba-welcome     # assistant d'accueil (PyQt6)
│       ├── usr/local/share/djerba-welcome/ui/main.qml
│       └── usr/share/{applications,polkit-1/actions}/
├── tests/
│   ├── run_local_lints.sh                 # shellcheck, py_compile, YAML/INI, XML, visudo
│   ├── test-yaml-ini.py                   # validateur strict mount.conf / flathub.conf / …
│   ├── test-features.sh                   # inspection ISO + squashfs + chroot
│   └── test-iso-qemu.sh                   # boot UEFI headless (OVMF / AAVMF)
├── build.sh                               # orchestrateur
├── Dockerfile                             # environnement de build reproductible
└── README.md
```

Les visuels générés (`config/bootloaders/`, thèmes Plymouth/SDDM, branding
Calamares, fond d'écran, assets de l'assistant) et les fichiers régénérés par
`lb config` sont ignorés par git : ils sont reproduits à chaque construction.

> Les fichiers `djerba-linux-iso.zip` et `.github/workflows/bootstrap.yml`
> appartiennent à l'ancien prototype Arch (archiso) ; ils ne participent pas
> à la construction de Djerba OS.

---

## Chaîne de construction

```
build.sh
 ├─ contrôles (root, live-build ≥ 2023, ubuntu-keyring…)
 ├─ apt-get update en boucle (résilience runners ARM64)
 ├─ noyau : lowlatency publié pour l'architecture ? sinon generic
 ├─ tests/run_local_lints.sh   (dont tests/test-yaml-ini.py)
 ├─ assets_generator/generate_assets.py
 ├─ lb clean → lb config (auto/config) → lb build (auto/build, 3 tentatives)
 └─ artifacts/djerba-os-<version>-<arch>.iso + .sha256 + .packages
```

### Pourquoi le live-build de Debian ?

Le paquet `live-build` d'Ubuntu (y compris 24.04 → 26.04) est figé sur la
branche **3.0~a57** de 2013 : il ne sait pas produire d'image EFI (aucune étape
`binary_grub-efi`). Le **live-build moderne de Debian** (`1:20250505`, paquet
`Architecture: all`, dépendant seulement de `cpio` et `debootstrap`) prend en
charge `--bootloaders grub-efi`, le Secure Boot avec les binaires signés
d'**Ubuntu** (`gcdx64.efi.signed`, `shimx64.efi.signed`) et les listes de
paquets conditionnelles.

Le `Dockerfile` l'installe **par APT** depuis Debian trixie avec un épinglage
strict (`Pin-Priority: -1` pour tout trixie, `990` pour `live-build` seul) :
tout le reste de l'environnement reste Ubuntu 24.04, sans `pip` (PEP 668).

Le mode `ubuntu` ayant disparu de live-build, `auto/config` décrit noble
explicitement : miroirs `archive.ubuntu.com` / `security.ubuntu.com` (amd64) ou
`ports.ubuntu.com/ubuntu-ports` (arm64), trousseau `ubuntu-keyring`,
composantes `main restricted universe multiverse`, saveur de noyau Ubuntu.

### Options clés de `lb config`

| Option | Valeur |
|---|---|
| `--bootloaders` | `grub-efi grub-pc` (amd64) · `grub-efi` (arm64) |
| `--uefi-secure-boot` | `enable` (amd64) · `auto` (arm64) |
| `--binary-images` | `iso-hybrid` |
| `--linux-flavours` | `lowlatency` ou `generic` |
| `--initramfs` | `live-boot` |
| `--apt-recommends` | `false` (système léger, dépendances listées explicitement) |
| `--chroot-squashfs-compression-type` | `zstd` |
| `--bootappend-live` | `boot=live components quiet splash username=djerba hostname=djerba locales=… keyboard-layouts=… console=ttyS0,115200n8 console=tty0` |

Compression (`auto/build`) :

```
MKSQUASHFS_OPTIONS="-comp zstd -b 1M -Xcompression-level 19 -mem 4G -processors 2"
```

### Mécanismes natifs utilisés (aucun contournement)

* **Listes conditionnelles** : `#if ARCHITECTURES amd64` / `#if
  LINUX_FLAVOURS_WITH_ARCH lowlatency` dans `config/package-lists/`.
* **Multiarch i386** (Steam, Wine 32 bits) : les entrées `paquet:i386` font
  exécuter `dpkg --add-architecture i386` par live-build lui-même.
* **Debconf** : `config/preseed/*.cfg.chroot`.
* **Thème GRUB** : `config/bootloaders/grub-pc/` (splash, `live-theme/`,
  `config.cfg`) recopié par live-build pour GRUB BIOS **et** EFI.
* **Fichiers système** : `config/includes.chroot/` (copiés avec
  `rsync --chown=0:0`).
* **Retraits sur la cible** : modules Calamares `packages` (paquets live) et
  `shellprocess@target-cleanup` (autologin, sudoers et polkit live).

---

## Tests

| Script | Quand | Ce qu'il garantit |
|---|---|---|
| `tests/test-yaml-ini.py` | avant toute compilation | YAML strict (clés dupliquées, tabulations) et conforme aux schémas Calamares 3.3 pour `mount.conf` (sous-volumes `/@` → `/`, `/@home` → `/home`, options zstd/ssd/noatime/discard=async) et `fstab.conf` ; INI strict (`configparser`) pour `flathub.conf` (`[remote "flathub"]`, URL officielle), SDDM, `.desktop`, fichiers KDE ; netplan ; cohérence de `settings.conf` |
| `tests/run_local_lints.sh` | avant toute compilation | `shellcheck` sur tous les scripts, présence de `set -euo pipefail`, bit exécutable, compilation Python stricte, XML polkit, `visudo -c`, `qmllint` si disponible, + le test YAML/INI |
| `tests/test-features.sh` | après le build | ~70 contrôles : EFI/Secure Boot/BIOS, thème GRUB, paramètres de boot, squashfs zstd 1 Mio, noyau, Plasma Wayland, Calamares, thèmes, jeu, Firefox, absence de snap, droits (`0440`, `0600`) puis, dans un **chroot overlay** du squashfs : `visudo`, polkit, imports PyQt6/D-Bus, remote Flathub, épinglage Mozilla, services systemd, locale, **chargement QML réel** de l'assistant |
| `tests/test-iso-qemu.sh` | après le build | démarrage **UEFI natif** (OVMF q35 / AAVMF virt), CD-ROM SCSI, KVM sinon TCG, console série `ttyS0`/`ttyAMA0` : succès sur `djerba login:`, échec sur panique, shell initramfs, média introuvable ; capture d'écran QMP de la session Plasma |

Les journaux et la capture sont écrits dans `tests/out/` (publiés en artefacts
CI).

---

## CI/CD GitHub Actions

`.github/workflows/build-and-test.yml` exécute une **matrice native** :

| `arch` | runner | Délai QEMU |
|---|---|---|
| `x86_64` | `ubuntu-latest` | 900 s (KVM) |
| `arm64` | `ubuntu-24.04-arm` | 2400 s (TCG possible) |

Étapes : checkout → `jlumbroso/free-disk-space@main` → outils hôte (APT avec
retry) → **lints** (dont `test-yaml-ini.py`) → **génération des assets** →
restauration du **cache de paquets live-build** (`cache/packages.*`, clé
dépendant des listes et d'`auto/config`) → **image Docker + `./build.sh`** →
**tests fonctionnels** → **test QEMU** → artefacts (ISO, journaux, capture).

Sur un tag `v*`, le job `release` publie une **GitHub Release** avec les ISO
(x86_64 et arm64), leurs sommes SHA-256 et les manifestes de paquets. Les
fichiers de plus de 2 Gio (limite GitHub) sont découpés en `*.iso.partNN` :

```bash
cat djerba-os-1.0.0-amd64.iso.part* > djerba-os-1.0.0-amd64.iso
sha256sum -c djerba-os-1.0.0-amd64.iso.sha256
```

---

## Le système Djerba OS

### Session live

* Utilisateur `djerba` (mot de passe `live`, défaut de live-config ; sudo sans mot de passe ; connexion automatique dans
  **Plasma Wayland**), nom d'hôte `djerba`, français par défaut.
* L'assistant **Djerba Welcome** s'ouvre au démarrage : présentation, état
  réseau (D-Bus NetworkManager), matériel détecté, accès rapides (Steam,
  Discover, Firefox, Paramètres) et bouton **Installer Djerba OS**.
* L'assistant est supervisé : si le rendu OpenGL plante (pilote défaillant,
  machine virtuelle sans accélération 3D — cas constaté avec llvmpipe sur
  Xorg dans QEMU), il se relance automatiquement avec le rendu logiciel de
  QtQuick et un verre teinté sans shaders.
* L'installateur est lancé par `pkexec` via l'action polkit
  `org.djerba.installer` (`exec.allow_gui`), autorisée sans mot de passe pour
  l'utilisateur live par `99-djerba-installer.rules` : Calamares (Qt 5)
  s'affiche via XWayland géré par KWin, sans rendu logiciel forcé.

### Installation (Calamares 3.3)

* Table **GPT**, partition EFI de 512 Mio, racine **BTRFS** avec sous-volumes
  `@` (`/`) et `@home` (`/home`).
* Options de montage écrites dans `/etc/fstab` :
  `compress=zstd,noatime` + `ssd,discard=async` sur SSD (`autodefrag` sur HDD).
* **Aucun swap disque** : `zramswap` (zstd, 50 % de la RAM).
* `/tmp` en tmpfs, LUKS2 proposé, GRUB EFI signé + shim (`efiBootloaderId:
  djerba`, chargeur de secours EFI installé).
* Retrait automatique des paquets live et des réglages propres à la session
  live (autologin, sudoers, polkit, autostart).

### Performances (hook 0200)

* zram zstd, `vm.swappiness=180`, `vm.page-cluster=0`, watermarks ajustés.
* `vm.max_map_count=2147483642` (Proton / jeux récents),
  `kernel.split_lock_mitigate=0`.
* Réseau `fq` + **BBR**, TCP Fast Open.
* Ordonnanceurs d'E/S : NVMe `none`, SSD `mq-deadline`, HDD `bfq`.
* **GameMode** (gouverneur `performance`, renice, softrealtime), groupe
  `gamemode` pour l'utilisateur.

### Sécurité (hook 0400)

* **UFW** actif au démarrage : entrées refusées, sorties autorisées.
* `sysctl` durci (kptr/dmesg restreints, BPF non privilégié désactivé,
  ptrace limité, redirections ICMP refusées, rp_filter…), en conservant les
  espaces de noms utilisateur nécessaires à Flatpak et Steam.
* **AppArmor** actif, **mises à jour de sécurité automatiques**, root
  verrouillé, core dumps désactivés.
* Pas de `snapd` (épinglé à `-1`) ; Firefox provient du **dépôt APT officiel
  Mozilla** (clé `35BA A0B3 3E9E B396 F59C A838 C0BA 5CE6 DC63 15A3`).

### Logiciels (hook 0500)

* Firefox + traduction française depuis `packages.mozilla.org`.
* Remote **Flathub** ajouté au niveau système à partir de
  `/etc/flatpak/remotes.d/flathub.conf` (URL lue avec `configparser`, puis
  `flatpak remote-add`), métadonnées AppStream préchargées pour Discover.

---

## Identité visuelle

Palette « lagon de Djerba » (`COLORS` dans `generate_assets.py`) :

| Jeton | Couleur | Usage |
|---|---|---|
| `night` | `#070B18` | fond principal |
| `deep` | `#0A1F3D` | eau profonde |
| `lagoon` | `#0E5E7E` | lagon |
| `turquoise` | `#00C2CB` | accent |
| `aqua` | `#5EEAD4` | écume, succès |
| `sand` | `#F4E1C1` | sable, avertissements |
| `coral` | `#FF7A59` | soleil couchant, erreurs |
| `white` | `#F8FAFC` | texte |
| `muted` | `#94A3B8` | texte secondaire |

Typographie : **Inter**. Logo : soleil couchant sur trois vagues dans un carré
de verre.

* **GRUB** : scène SVG 1920×1080 (halos, vagues, logo, panneau de verre),
  menu centré, surbrillance turquoise en 9 tranches, compte à rebours.
* **Plymouth** : la scène QtQuick `plymouth.qml.j2` (ondes concentriques, arc
  turquoise, contre-arc corail, écume en orbite) est rendue en 60 images
  (backend logiciel Qt, alpha reconstitué par matting noir/blanc) puis jouée à
  25 i/s par un thème *script* ; barre de progression, messages et saisie de
  mot de passe LUKS en verre.
* **SDDM** : horloge, carte de connexion en verre dépoli (`FastBlur` +
  `OpacityMask`), particules d'écume, fond en « Ken Burns », animation
  d'erreur.
* **Calamares** : barre latérale nuit/turquoise, QSS cohérente, diaporama QML
  de cinq cartes de verre.
* **Plasma** : Look-and-Feel `org.djerba.desktop` (Breeze sombre accentué
  turquoise, fond d'écran `Djerba` en 4K ; éléments non redéfinis hérités
  nativement de Breeze), flou et contraste KWin.
* **Djerba Welcome** : verre dépoli calculé une seule fois puis échantillonné
  par chaque panneau (`ShaderEffectSource`), particules, animations
  d'entrée/survol pilotées par le moteur de rendu pour rester à 60 i/s.

Régénérer et prévisualiser :

```bash
QT_QPA_PLATFORM=offscreen python3 assets_generator/generate_assets.py --version dev
xdg-open config/bootloaders/grub-pc/splash.png
```

---

## Choix techniques et écarts assumés

Ces points découlent de ce que publie réellement Ubuntu 24.04 ; ils sont
documentés ici plutôt que masqués par des contournements.

| Demande | Réalité Ubuntu 24.04 | Choix Djerba OS |
|---|---|---|
| KDE Plasma 6 | noble fournit **Plasma 5.27 LTS** ; les PPA Kubuntu `backports`/`backports-extra` ne publient rien pour noble | Plasma 5.27 LTS Wayland ; l'assistant d'accueil est en Qt 6 |
| `gamescope` | **absent** des dépôts noble (toutes composantes) | GameMode + MangoHud installés ; gamescope reste installable par Flathub (extension `org.freedesktop.Platform.VulkanLayer.gamescope` pour Steam Flatpak) |
| `qml6-module-qtquick-effects` | QtQuick.Effects n'existe qu'à partir de Qt 6.5 (noble : Qt 6.4) | `qml6-module-qt5compat-graphicaleffects` (FastBlur, OpacityMask, RectangularGlow) |
| `qml6-module-qtsvg` | n'existe pas en Qt 6.4 | `libqt6svg6` (greffon image SVG) |
| `kpmcore` | le paquet s'appelle `libkpmcore12` | `libkpmcore12` |
| `cairo` (outil) | pas de paquet de ce nom | `python3-cairo` + `python3-cairosvg` |
| `live-build` `--bootloader grub-efi` | live-build d'Ubuntu sans EFI | live-build Debian moderne épinglé (voir plus haut) ; `--bootloader` est un alias accepté de `--bootloaders` |
| Options BTRFS dans `fstab.conf` | depuis Calamares 3.3, `fstab.conf` n'accepte que `crypttabOptions` et `tmpOptions` ; les options de montage sont lues dans `mount.conf` (`mountOptions`) puis **écrites dans `/etc/fstab` par le module fstab** | options déclarées dans `mount.conf`, `fstab.conf` conforme au schéma 3.3 |
| `flathub.conf` dans `remotes.d` | Flatpak ne lit nativement que `*.flatpakrepo` dans ce répertoire | `flathub.conf` (INI strict demandé) est la source de vérité lue par le hook 0500, qui enregistre le remote avec `flatpak remote-add` |
| Session et greeter Wayland | SDDM 0.20 de noble utilise un greeter X11 (comme Kubuntu 24.04) ; live-config écrit par défaut un autologin X11 | greeter X11 (Xorg minimal), **session Plasma Wayland** via `10-wayland-autologin.conf` ; l'autologin X11 de live-config est désactivé (`LIVE_CONFIG_NOX11AUTOLOGIN`) ; session X11 conservée en secours |
| `--apt-recommends false` | live-config, Plasma et SDDM dépendent de recommandations (`user-setup`, `libpam-systemd`, Xorg…) | recommandations indispensables listées explicitement et couvertes par `test-features.sh` |
| Steam/Wine 32 bits sur ARM64 | indisponibles | exclus par `#if ARCHITECTURES amd64` |
| `ttyS0` en ARM64 | la machine `virt` expose un PL011 | `ttyAMA0` en arm64, `ttyS0` en x86_64 |

---

## Personnalisation

* **Paquets** : ajoutez une ligne dans `config/package-lists/*.list.chroot`
  (blocs `#if ARCHITECTURES amd64` … `#endif` pour une architecture).
* **Langue / clavier de la session live** : `DJERBA_LOCALE=en_US.UTF-8
  DJERBA_KEYMAP=us ./build.sh --docker`.
* **Couleurs, textes, diaporama** : `COLORS`, `TAGLINE`, `SLIDES` dans
  `assets_generator/generate_assets.py`.
* **Partitionnement** : `config/includes.chroot/etc/calamares/modules/` puis
  `python3 tests/test-yaml-ini.py`.

---

## Dépannage

| Symptôme | Piste |
|---|---|
| `live-build moderne (1:2023xxxx+) requis` | utilisez `./build.sh --docker` (le Dockerfile installe la bonne version) |
| `debootstrap` / `mount` refusés | le conteneur doit être `--privileged` (fait par `--docker` et la CI) |
| Coupure réseau pendant `lb build` | `build.sh` relance `lb build` qui reprend à la dernière étape terminée ; APT est configuré avec `Acquire::Retries=10` |
| Construction arm64 sur hôte x86_64 | non supportée : utilisez un hôte/runner ARM64 natif |
| L'ISO ne démarre pas en Secure Boot | vérifiez `tests/test-features.sh` (« Shim signé ») ; désactivez Secure Boot pour un firmware non Microsoft |
| L'assistant ne s'ouvre plus | `~/.config/djerba-welcomerc` → `Autostart=true`, ou lancez `djerba-welcome` |
| Installer sans l'assistant | menu « Installer Djerba OS » ou `djerba-welcome --install` |

---

## Licences

* Code de ce dépôt (scripts, configurations, générateur, QML) : **GPL-3.0-or-later**.
* Visuels générés (logo, fonds, thèmes) : **CC-BY-SA-4.0**.
* Les paquets inclus restent sous leurs licences respectives (Ubuntu,
  Mozilla, Flathub…).
* **Steam** (x86_64) est préinstallé depuis la composante *multiverse*
  d'Ubuntu ; la question debconf du lanceur est préremplie
  (`config/preseed/djerba.cfg.chroot`). L'utilisation de Steam reste soumise à
  l'[accord de souscription Steam](https://store.steampowered.com/subscriber_agreement/)
  présenté par Valve lors de la connexion au compte.
