# 🏝️ Djerba OS

**Djerba OS** est une distribution Linux live et installable, légère, sûre et
taillée pour le jeu. Elle est construite sur **Debian 13 « trixie »** avec ses
propres paquets (`djerba-branding`, `djerba-tools`), le bureau **KDE Plasma 6
sous Wayland**, l'installateur **Calamares 3.3** et une identité visuelle
« lagon de Djerba » en verre dépoli.

L'ISO produite est un **Live-CD hybride** (CD/DVD ou clé USB), amorçable en
**UEFI x86_64** (Secure Boot via shim et GRUB signés Debian, plus BIOS hérité)
et en **UEFI ARM64**.

| | x86_64 | ARM64 |
|---|---|---|
| Base | Debian 13 trixie (`main contrib non-free non-free-firmware`) | idem |
| Amorçage | UEFI (Secure Boot) + BIOS, iso-hybrid | UEFI, iso-hybrid |
| Noyau | `linux-image-amd64` 6.12, `preempt=full` | `linux-image-arm64` 6.12, `preempt=full` |
| Bureau | Plasma 6.3 Wayland, SDDM 0.21 (Qt 6) | idem |
| Jeu | GameMode, MangoHud, vkBasalt, Vulkan ; Steam, Lutris, Heroic… au choix | idem, sans Steam ni Wine 32 bits |
| Vie privée | mode anonyme (Tor + coupe-circuit), mode discret façon Windows 10/11 | idem |
| Sécurité | UFW, ClamAV + ClamTk, AppArmor, mises à jour automatiques | idem |
| Installation | Calamares, choix des applications, BTRFS `@`/`@home`, zstd, sans swap disque | idem |

---

## Sommaire

1. [Démarrage rapide](#démarrage-rapide)
2. [Le système Djerba OS](#le-système-djerba-os)
3. [Architecture du dépôt](#architecture-du-dépôt)
4. [Chaîne de construction](#chaîne-de-construction)
5. [Tests](#tests)
6. [CI/CD GitHub Actions](#cicd-github-actions)
7. [Identité visuelle](#identité-visuelle)
8. [Choix techniques et limites connues](#choix-techniques-et-limites-connues)
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

# Construit l'image Docker, puis lance lints + assets + paquets + live-build dedans.
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

## Le système Djerba OS

### Une vraie distribution, pas un thème

Djerba OS s'appuie sur Debian comme Ubuntu ou Kali le font, avec sa propre
identité livrée **par des paquets Debian** construits dans ce dépôt :

* **`djerba-branding`** : `/usr/lib/os-release` (`ID=djerba`, `ID_LIKE=debian`)
  installé par une **diversion dpkg** du fichier de `base-files` (mécanisme
  officiel ; les conffiles comme `/etc/issue` ne pouvant pas être déroutés
  selon la Charte Debian 10.7.4, ils sont réécrits par un hook), logo et
  icônes, fonds d'écran, thèmes globaux Plasma, Plymouth, SDDM, GRUB
  (`/etc/default/grub.d/djerba.cfg`), page « À propos de ce système »
  (`kcm-about-distrorc`).
* **`djerba-tools`** : assistant d'accueil, gestionnaire d'applications, mode
  anonyme, mode discret, mode jeu, actions polkit et services systemd.

Résultat : « À propos de ce système », GRUB, la console, `neofetch`/`fastfetch`
et les outils système affichent **Djerba OS**, aussi bien en live qu'après
installation.

### Session live

* Utilisateur `djerba` (mot de passe `live`, défaut de live-config ; sudo sans
  mot de passe), connexion automatique dans **Plasma Wayland**, français par
  défaut.
* L'assistant **Djerba Welcome** s'ouvre au démarrage avec un bouton
  **Installer Djerba OS**. Il est supervisé : si le rendu OpenGL plante (pilote
  défaillant, VM sans 3D), il se relance avec le rendu logiciel de QtQuick.
* L'installateur est lancé par `pkexec` via l'action polkit
  `org.djerba.installer`, autorisée sans mot de passe pour l'utilisateur live.

### Installation (Calamares 3.3)

1. Bienvenue (vérifications : espace, mémoire, alimentation, Internet).
2. Langue, fuseau, clavier, partitionnement, utilisateur.
3. **Applications** : une page à cases à cocher (module `netinstall`) générée
   depuis le catalogue `apps.yaml` : Steam, Lutris, Heroic Games Launcher,
   ProtonUp-Qt, Bottles, Protontricks, Wine, RetroArch, Dolphin, Prism
   Launcher, Discord, VLC, OBS Studio, Kdenlive, GIMP, Krita, Spotify,
   LibreOffice, Thunderbird, ClamTk, KeePassXC, Tor Browser, BleachBit.
4. Résumé lisible (thème sombre complet), installation avec diaporama.

Le module Python **`djerbaapps`** transmet la sélection au système installé
(`djerba-apps apply-selection`) : les paquets Debian disponibles hors ligne
sont installés immédiatement ; le reste (Flatpak, dépôts réseau) est mis en
file d'attente et installé **au premier démarrage** par
`djerba-apps-firstboot.service`, qui attend le réseau et réessaie en cas
d'échec. L'assistant affiche la progression.

Disque : GPT, EFI 512 Mio, **BTRFS** avec sous-volumes `@` (`/`) et `@home`
(`/home`), `compress=zstd,noatime` + `ssd,discard=async` sur SSD ; **aucun
swap disque** (`zramswap` zstd) ; LUKS2 proposé ; paquets et réglages live
retirés sur la cible.

### Premier démarrage et assistant

L'assistant **Djerba Welcome** (PyQt6 + QML) propose cinq pages :

| Page | Contenu |
|---|---|
| Accueil | installation (live), progression des applications choisies, accès rapides |
| Applications | catalogue par catégories, cases à cocher, installation **et** suppression avec progression (`pkexec djerba-apps`) |
| Jeu | mode jeu, MangoHud, lanceurs détectés (Steam, Lutris, Heroic…) |
| Confidentialité | mode anonyme, nouvelle identité Tor, vérification d'IP, mode discret, ClamAV, pare-feu |
| Système | matériel, noyau, mises à jour, thèmes globaux |

En ligne de commande : `djerba-apps list|install|remove|status`.

### Mode jeu

* Noyau Debian en **préemption complète** (`preempt=full`), `vm.max_map_count`
  élevé (Proton), BBR, ordonnanceurs d'E/S adaptés, `irqbalance`.
* **GameMode** (par jeu : `gamemoderun %command%`), **MangoHud**,
  **vkBasalt**, GOverlay, pilotes Mesa/Vulkan, règles udev `steam-devices`.
* **`djerba-gamemode`** (mode jeu de session) : profil d'énergie
  « performance », effets KWin coûteux désactivés, état précédent restauré à
  l'identique ; superposition MangoHud activable globalement.
* Steam (avec multiarch i386 et accord de licence pré-rempli à la demande de
  l'utilisateur), Lutris, Heroic, Bottles… s'installent depuis l'installateur
  ou l'assistant.

### Mode anonyme (inspiré d'AnonSurf de Parrot OS)

`djerba-anonsurf start|stop|toggle|change|myip|status` (ou l'assistant, ou le
menu) fait passer **tout le trafic** de la machine par Tor :

* coupe-circuit **nftables** appliqué *avant* le démarrage de Tor : TCP
  redirigé vers le `TransPort`, DNS vers le `DNSPort` (domaines `.onion`
  compris), IPv6, UDP et ICMP bloqués, réseau local et DHCP autorisés ; si Tor
  s'arrête, **rien ne sort** ;
* instance Tor dédiée (`djerba-anonsurf-tor.service`), Tor système désactivé
  par défaut ;
* nouvelle identité (`SIGNAL NEWNYM`), vérification via
  `check.torproject.org`, notifications de bureau.

> Le mode anonyme protège l'adresse IP ; il ne remplace pas le Tor Browser
> pour la navigation (empreinte du navigateur, cookies). Tor Browser est
> proposé dans le catalogue.

### Mode discret (inspiré du mode undercover de Kali)

`djerba-undercover` (ou l'assistant) donne au bureau une apparence **façon
Windows 10** (barre alignée à gauche) ou **Windows 11** (barre centrée) :
couleurs claires, accent bleu, fond bleu. La configuration Plasma de
l'utilisateur est sauvegardée puis **restaurée à l'identique** au retour.
Ces thèmes (`org.djerba.undercover10/11`) sont installés dans le système et
disponibles **dès la session live**, sans Internet.

### Sécurité

* **UFW** actif (entrées refusées), `sysctl` durci, **AppArmor**, root
  verrouillé, core dumps désactivés.
* **ClamAV** préinstallé avec **ClamTk** (interface graphique) et mises à jour
  automatiques des signatures (`clamav-freshclam`).
* Mises à jour de sécurité Debian automatiques (`unattended-upgrades`).
* Firefox depuis le **dépôt APT officiel Mozilla** (clé
  `35BA A0B3 3E9E B396 F59C A838 C0BA 5CE6 DC63 15A3`), Flathub au niveau
  système pour Discover.

---

## Architecture du dépôt

```
.
├── .github/workflows/build-and-test.yml   # CI : matrice x86_64 / arm64 natifs
├── assets_generator/
│   ├── generate_assets.py                 # jetons de design -> visuels + paquet djerba-branding
│   └── templates/                         # grub-splash, plymouth, sddm (Qt 6), calamares
├── auto/{config,build,clean}              # lb config : trixie, miroirs, EFI, squashfs…
├── packages/
│   ├── build-packages.sh                  # dpkg-deb -> config/packages.chroot/
│   ├── djerba-branding/DEBIAN + root/     # identité (os-release dérouté, GRUB, À propos)
│   └── djerba-tools/DEBIAN + root/        # djerba-welcome, -apps, -anonsurf, -undercover, -gamemode
├── tools/catalog-to-netinstall.py         # apps.yaml -> page « Applications » de Calamares
├── config/
│   ├── package-lists/                     # 01 base · 02 bureau · 03 jeu
│   ├── hooks/normal/                      # 0100 → 0500 (hooks chroot)
│   ├── preseed/djerba.cfg.chroot          # réponses debconf (clavier)
│   └── includes.chroot/                   # Calamares (settings, modules, djerbaapps), polkit, SDDM, skel…
├── tests/
│   ├── run_local_lints.sh                 # shellcheck, py_compile, YAML/INI, XML, visudo
│   ├── test-yaml-ini.py                   # validateur strict mount.conf / flathub.conf / …
│   ├── test-features.sh                   # inspection ISO + squashfs + chroot
│   └── test-iso-qemu.sh                   # boot UEFI headless (OVMF / AAVMF)
├── build.sh                               # orchestrateur
└── Dockerfile                             # environnement de build Debian trixie
```

Les visuels générés, `build/`, `config/packages.chroot/`, `netinstall.conf`
et les fichiers régénérés par `lb config` sont ignorés par git.

> Les fichiers `djerba-linux-iso.zip` et `.github/workflows/bootstrap.yml`
> appartiennent à l'ancien prototype Arch (archiso) ; ils ne participent pas
> à la construction de Djerba OS.

---

## Chaîne de construction

```
build.sh
 ├─ contrôles (root, live-build 1:2025…, debian-archive-keyring)
 ├─ apt-get update en boucle (résilience runners ARM64)
 ├─ tests/run_local_lints.sh   (dont tests/test-yaml-ini.py)
 ├─ assets_generator/generate_assets.py + tools/catalog-to-netinstall.py
 ├─ packages/build-packages.sh  → config/packages.chroot/*.deb
 ├─ lb clean → lb config (auto/config) → lb build (auto/build, reprises)
 └─ artifacts/djerba-os-<version>-<arch>.iso + .sha256 + .packages
```

Tout est natif : live-build de Debian (paquets locaux de
`config/packages.chroot/`, listes conditionnelles `#if ARCHITECTURES`, hooks,
`includes.chroot`), dpkg (diversion, conffiles, scripts de maintenance),
Calamares (modules `netinstall`, `packages`, `shellprocess`, module Python),
systemd et polkit.

| Option `lb config` | Valeur |
|---|---|
| `--distribution` | `trixie` (+ `trixie-security`, `trixie-updates`) |
| `--archive-areas` | `main contrib non-free non-free-firmware` |
| `--bootloaders` | `grub-efi grub-pc` (amd64) · `grub-efi` (arm64) |
| `--uefi-secure-boot` | `enable` |
| `--linux-flavours` | `amd64` · `arm64` |
| `--apt-recommends` | `false` (dépendances listées explicitement) |
| `--firmware-chroot` | `false` (liste de micrologiciels explicite, sans installeurs qui téléchargent) |
| `--chroot-squashfs-compression-type` | `zstd` |

```
MKSQUASHFS_OPTIONS="-comp zstd -b 1M -Xcompression-level 19 -mem 4G -processors 2"
```

---

## Tests

| Script | Quand | Ce qu'il garantit |
|---|---|---|
| `tests/test-yaml-ini.py` | avant compilation | YAML strict et schémas Calamares 3.3 (`mount.conf` : `/@`, `/@home`, zstd/ssd/noatime/discard=async) ; INI strict pour `flathub.conf`, SDDM, `.desktop`, KDE ; unités systemd ; catalogue d'applications ; cohérence de `settings.conf` |
| `tests/run_local_lints.sh` | avant compilation | `shellcheck`, `set -euo pipefail` (ou `set -e` pour les scripts dpkg), bit exécutable, Python strict, XML polkit, `visudo -c` |
| `tests/test-features.sh` | après le build | ~90 contrôles : EFI/Secure Boot/BIOS, GRUB, squashfs, noyau Debian, **identité Djerba OS** (os-release, À propos, icônes), Plasma Wayland, Calamares + page Applications, mode anonyme, mode discret, ClamAV, jeu, puis en **chroot overlay** : paquets `djerba-*` installés, diversion dpkg, catalogue, polkit, services, chargement QML réel de l'assistant |
| `tests/test-iso-qemu.sh` | après le build | démarrage **UEFI** (OVMF / AAVMF), console série : succès sur `djerba login:`, **bannière « Djerba OS »** vérifiée, échec sur panique ou shell initramfs ; capture d'écran QMP |

---

## CI/CD GitHub Actions

`.github/workflows/build-and-test.yml` exécute une **matrice native** :
`x86_64` sur `ubuntu-latest` (KVM) et `arm64` sur `ubuntu-24.04-arm` (TCG).

Étapes : checkout → libération d'espace → lints → génération des assets et
de la page Applications → cache des paquets live-build → image Docker +
`./build.sh` → tests fonctionnels → test QEMU → artefacts (ISO, journaux,
capture). Sur un tag `v*`, une **GitHub Release** publie les ISO, sommes
SHA-256 et manifestes (fichiers > 2 Gio découpés en `*.iso.partNN`).

---

## Identité visuelle

Palette « lagon de Djerba » (`COLORS` dans `generate_assets.py`) : `night`
`#070B18`, `deep` `#0A1F3D`, `lagoon` `#0E5E7E`, `turquoise` `#00C2CB`,
`aqua` `#5EEAD4`, `sand` `#F4E1C1`, `coral` `#FF7A59`, `white` `#F8FAFC`,
`muted` `#94A3B8`. Typographie : **Inter**.

**Logo** : monogramme « D » couleur chaux dont l'œil est une **arche de
menzel djerbien** ouverte sur un soleil couchant et le lagon, dans un carré
de verre. Il est décliné en SVG et PNG (48 → 256 px) pour l'icône du menu,
« À propos », GRUB, Plymouth, SDDM, Calamares et l'assistant.

* **GRUB** : scène SVG, menu centré, surbrillance turquoise, thème installé
  aussi sur le système (`/usr/share/grub/themes/djerba`).
* **Plymouth** : scène QtQuick rendue en 60 images (ondes, arcs, écume),
  messages et saisie LUKS en verre.
* **SDDM (Qt 6)** : carte de connexion en verre (`MultiEffect`), avatar à
  anneau animé, particules, animations d'entrée et d'attente.
* **Calamares** : barre latérale nuit/turquoise, feuille de style sombre
  complète (résumé, partitions, listes, info-bulles lisibles), diaporama de six
  cartes.
* **Plasma** : thèmes globaux **Djerba** et **Djerba Horizon** (barre
  centrée), plus **Discret 10/11** pour le mode discret.
* **Djerba Welcome** : barre latérale, transitions de pages glissées, verre
  dépoli échantillonné une seule fois, particules d'écume, interrupteurs et
  cartes animés à 60 i/s.

---

## Choix techniques et limites connues

| Sujet | Choix |
|---|---|
| Base Ubuntu → Debian | Debian 13 apporte Plasma 6, Qt 6.8, Calamares 3.3.14 Qt 6 et un live-build natif ; Djerba OS devient une distribution dérivée à part entière |
| Identifiant EFI | le GRUB **signé** de Debian cherche sa configuration dans `/EFI/debian` : `efiBootloaderId: debian` est conservé pour que le Secure Boot fonctionne ; le menu affiche « Djerba OS » (`GRUB_DISTRIBUTOR`) |
| `gamescope` | absent de Debian 13 ; installable via Flathub (extension `org.freedesktop.Platform.VulkanLayer.gamescope`) |
| Noyau temps réel | `preempt=full` sur le noyau Debian (préemption dynamique) plutôt qu'un noyau tiers non signé, pour garder le Secure Boot |
| Steam / Wine 32 bits | x86_64 seulement ; Steam n'est pas préinstallé mais proposé (licence Valve, multiarch i386) |
| Flatpak à l'installation | installés au premier démarrage (réseau requis, réessais automatiques) |
| `flathub.conf` | source de vérité INI lue par le hook 0500, qui enregistre le remote avec `flatpak remote-add` |

---

## Personnalisation

* **Paquets système** : `config/package-lists/*.list.chroot`.
* **Applications proposées** : `packages/djerba-tools/root/usr/share/djerba/apps.yaml`
  (APT ou Flatpak, architectures, sélection par défaut) — la page de
  l'installateur et l'assistant en sont générés.
* **Langue / clavier du live** : `DJERBA_LOCALE=en_US.UTF-8 DJERBA_KEYMAP=us ./build.sh --docker`.
* **Couleurs, logo, diaporama** : `COLORS`, `logo_fragment()`, `SLIDES` dans
  `assets_generator/generate_assets.py`.

---

## Dépannage

| Symptôme | Piste |
|---|---|
| `live-build 1:… requis` | utilisez `./build.sh --docker` |
| `debootstrap` / `mount` refusés | le conteneur doit être `--privileged` |
| Coupure réseau pendant `lb build` | `build.sh` relance `lb build`, qui reprend à la dernière étape ; APT réessaie (`Acquire::Retries=10`) |
| Construction arm64 sur hôte x86_64 | non supportée : hôte/runner ARM64 natif |
| Curseur invisible sur une partie de l'écran (VM) | curseur logiciel KWin activé automatiquement en VM (`10-djerba-vm-cursor.sh`) |
| Applications choisies non installées | `djerba-apps status` ; `systemctl status djerba-apps-firstboot` (réessaie toutes les 2 min tant que le réseau manque) |
| Plus d'Internet après le mode anonyme | `pkexec djerba-anonsurf stop` (le coupe-circuit bloque tout tant que Tor n'est pas connecté) |
| Revenir de l'apparence Windows | `djerba-undercover off` |
| L'assistant ne s'ouvre plus | `djerba-welcome`, ou `Autostart=true` dans `~/.config/djerba-welcomerc` |

---

## Licences

* Code de ce dépôt (scripts, configurations, générateur, QML) : **GPL-3.0-or-later**.
* Visuels générés (logo, fonds, thèmes) : **CC-BY-SA-4.0**.
* Les paquets inclus restent sous leurs licences respectives (Debian, Mozilla,
  Flathub…). **Steam** est soumis à
  l'[accord de souscription Steam](https://store.steampowered.com/subscriber_agreement/) ;
  il n'est installé que si l'utilisateur le coche.
