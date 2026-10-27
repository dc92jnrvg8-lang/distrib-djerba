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
| Agent IA | **Djerba** : 228 capacités système, Ollama ou API compatible OpenAI, auto-réparation, Studio | idem |
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
7. [Agent IA Djerba](#agent-ia-djerba)
8. [Identité visuelle](#identité-visuelle)
9. [Choix techniques et limites connues](#choix-techniques-et-limites-connues)
10. [Personnalisation](#personnalisation)
11. [Dépannage](#dépannage)
12. [Licences](#licences)

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

En ligne de commande : `djerba-apps list|install|remove|status`. Les opérations sont
sérialisées par un verrou global (le service de premier démarrage, l'assistant et
le terminal ne se gênent jamais), attendent le verrou de dpkg au lieu d'échouer,
et publient une progression réelle (`--json-progress` : canal d'état d'APT pour
les paquets, pourcentage de Flatpak) ; l'architecture i386 est activée
automatiquement pour Steam et Wine.

### Mode jeu

* Noyau Debian en **préemption complète** (`preempt=full`), `vm.max_map_count`
  élevé (Proton), BBR, ordonnanceurs d'E/S adaptés, `irqbalance`.
* **GameMode** (par jeu : `gamemoderun %command%`), **MangoHud**,
  **vkBasalt**, GOverlay, pilotes Mesa/Vulkan, règles udev `steam-devices`.
* **Djerba Game Center** (`djerba-game-center`) : mode jeu en un clic, profil
  d'énergie du processeur, effets KWin, latence minimale (tearing autorisé en
  plein écran sous Wayland), MangoHud (préréglages, limite d'images/s),
  **GE-Proton** installé dans Steam (archive vérifiée par SHA-512) et lanceurs
  détectés. Moteur en ligne de commande : `djerba-gamemode` (`status --json`,
  `profile`, `effects`, `tearing`, `mangohud`, `proton list|install-ge|remove`) ;
  l'état précédent est toujours restauré à l'identique.
* Steam (avec multiarch i386 et accord de licence pré-rempli à la demande de
  l'utilisateur), Lutris, Heroic, Bottles… s'installent depuis l'installateur
  ou l'assistant.

### Mode anonyme (inspiré d'AnonSurf de Parrot OS)

`djerba-anonsurf start|stop|restart|toggle|change|myip|status [--json] [--verbose]`
(ou l'assistant, le menu, l'agent) fait passer **tout le trafic** de la machine
par Tor :

* coupe-circuit **nftables** appliqué *avant* le démarrage de Tor : TCP
  redirigé vers le `TransPort`, DNS vers le `DNSPort` (domaines `.onion`
  compris), IPv6, UDP et ICMP bloqués, réseau local et DHCP autorisés ; si Tor
  s'arrête, **rien ne sort** ;
* instance Tor dédiée (`djerba-anonsurf-tor.service`) configurée par
  `/etc/tor/djerba-anonsurf.torrc` (seul emplacement que le profil AppArmor de
  Tor autorise), Tor système désactivé par défaut ;
* si Tor ne parvient pas à se connecter, tout est annulé (rien n'est sorti
  entre-temps) et la connexion directe est rétablie, avec le journal de Tor ;
  un verrou empêche deux bascules simultanées ; `--verbose` détaille chaque étape ;
* nouvelle identité (`SIGNAL NEWNYM`), vérification via
  `check.torproject.org`, notifications de bureau.

> Le mode anonyme protège l'adresse IP ; il ne remplace pas le Tor Browser
> pour la navigation (empreinte du navigateur, cookies). Tor Browser est
> proposé dans le catalogue.

### Mode discret (inspiré du mode undercover de Kali)

`djerba-undercover on|off|toggle|status [--style 10|11] [--json] [--verbose]` (ou
l'assistant) donne au bureau une apparence **façon Windows 10** (barre alignée à
gauche) ou **Windows 11** (barre centrée) : couleurs claires, accent bleu, fond
bleu. Comme kali-undercover, l'état du bureau est sauvegardé puis **restauré à
l'identique** ; le bureau (plasmashell) est arrêté proprement avant chaque
sauvegarde et restauration — il réécrit sa configuration en quittant, ce qui
corrompait la session auparavant — et un thème qui échoue est annulé.
Ces thèmes (`org.djerba.undercover10/11`) sont installés dans le système et
disponibles **dès la session live**, sans Internet.

### Réseau

`/etc/resolv.conf` pointe vers le stub de **systemd-resolved**, alimenté par
NetworkManager, et le module NSS `resolve` est installé : la résolution de noms
fonctionne avec ou sans mode anonyme (en mode anonyme, les requêtes DNS IPv4
passent par le `DNSPort` de Tor). Auparavant, live-build restaurait en fin de
construction un `resolv.conf` vide : aucun nom ne se résolvait (Firefox, curl,
Discover), seuls les accès par adresse IP fonctionnaient.

### Sécurité

* **UFW** actif (entrées refusées), `sysctl` durci, **AppArmor** (profils
  chargés sur le système installé ; comme toute image Debian live, la session
  live ne les charge pas : `apparmor.service` s'y désactive), root verrouillé,
  core dumps désactivés.
* **ClamAV** préinstallé avec **ClamTk** (interface graphique) et mises à jour
  automatiques des signatures (`clamav-freshclam`).
* Mises à jour de sécurité Debian automatiques (`unattended-upgrades`).
* Firefox depuis le **dépôt APT officiel Mozilla** (clé
  `35BA A0B3 3E9E B396 F59C A838 C0BA 5CE6 DC63 15A3`), Flathub au niveau
  système pour Discover.

---

## Agent IA Djerba

**Djerba** (menu « Djerba », `djerba-ai`) est l'agent IA natif de Djerba OS : il
connaît la configuration de la machine et l'administre avec vous.

| Élément | Rôle |
|---|---|
| Application `djerba-ai` | discussion en continu, cartes d'actions et de confirmation, santé du système, auto-réparation, Studio, catalogue des capacités, modèles et réglages ; orbe animée en Qt Quick 3D (2D en rendu logiciel) |
| `djerba_ai.core` | configuration, mémoire SQLite (`~/.local/share/djerba/memory.db`), clients Ollama / API compatible OpenAI, boucle agentique, contexte système, IPC JSON-RPC, démon root, auto-réparation, démon utilisateur |
| `djerba_ai.tools` | **228 capacités** découvertes par introspection (classe `BaseTool`) : maintenance 31, performances 27 (dont `game_optimizer`), sécurité 30, bureau 26, applications 27, matériel 20, agent 27, système 21, Studio 19 |
| `djerba-ai-sysd` | démon root activé par la socket `/run/djerba-ai/sysd.sock` : appelant identifié par `SO_PEERCRED`, autorisation **polkit** du processus (`org.djerba.ai.exec` / `patch` / `deploy` / `setup`) ; n'exécute que les capacités intégrées marquées root |
| `djerba-ai.service` (utilisateur) | surveillance du journal (incidents d'auto-réparation), maintenance quotidienne, mise à jour hebdomadaire du modèle |

**Fonctionnement.** À chaque demande, l'agent reçoit l'état de la machine
(version, matériel, session live ou installée, modes anonyme / discret / jeu,
applications, services en échec, espace disque, réseau) et ce qu'il a retenu de
vous. Il choisit les capacités pertinentes (recherche + historique de réussite ;
`tools_search` lui donne accès aux 228), les appelle, lit leurs résultats et
répond. Toute action sensible est **confirmée** dans l'interface, puis par
votre mot de passe administrateur (polkit) si elle exige root. Il **apprend**
de l'usage : actions réussies, vos retours « Utile / Pas utile » et exemples de
demandes similaires orientent ses choix suivants.

**Modèles.** Ollama n'est pas empaqueté par Debian : le bouton « Installer
Ollama » télécharge l'archive officielle (GitHub, somme SHA-256 vérifiée) et
crée son service. Le modèle se change à tout moment (Qwen 2.5 3B/7B, Llama
3.2/3.1, Qwen 2.5 Coder… selon votre mémoire), ou toute API compatible OpenAI
(llama.cpp, LM Studio, service en ligne). **Sans moteur**, Djerba reste utile :
il propose directement les capacités correspondant à la demande.

**Recherche web.** `web_search` (DuckDuckGo) et `web_fetch` pour les questions
récentes ; désactivable dans les réglages ; passe par Tor en mode anonyme.

**Auto-réparation.** Une erreur d'un outil `djerba-*` dans le journal devient
un incident (notification). Djerba explique la cause, puis peut proposer un
correctif : le modèle réécrit le script, le démon root le vérifie dans le bac à
sable Git `/var/lib/djerba-ai/sandbox` (syntaxe, shellcheck, `--help` exécuté
sous l'utilisateur `nobody`, comportement identique à l'original) et l'annule
par `git reset --hard` s'il échoue. Le déploiement n'a lieu **qu'après votre
accord** (aperçu du diff + mot de passe), de façon atomique dans
`/usr/local/bin` (prioritaire dans le `PATH`, sans modifier les fichiers du
paquet) ; « Annuler le correctif » rétablit la version du paquet.

**Studio.** « Crée un jeu Snake en PyQt6 », « une calculatrice HTML5 »… :
l'application est écrite, testée (lancement hors écran, chargement QML, HTML
autonome), corrigée une fois si besoin, enregistrée dans
`~/.local/share/djerba/generated_apps/<nom>/` avec Git et `requirements.txt`,
et ajoutée au menu de Plasma (`~/.local/share/applications/djerba-<nom>.desktop`,
`djerba-ai run-app <nom> [--live]` avec rechargement à chaud). Hors ligne, les
modèles Snake, calculatrice et chronomètre restent disponibles. Djerba peut
aussi s'écrire de **nouvelles capacités** (greffons dans
`~/.local/share/djerba/plugins/`), qui ne sont actives qu'après votre accord et
ne peuvent jamais obtenir les droits root.

En terminal : `djerba-ai ask "…"`, `djerba-ai tools [recherche]`,
`djerba-ai run OUTIL '{"param": …}'`, `djerba-ai health`, `djerba-ai doctor`.

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
│   └── djerba-tools/DEBIAN + root/        # djerba-welcome, -apps, -anonsurf, -undercover, -gamemode,
│                                          # -game-center, djerba-ai (+ djerba_ai/ : core, tools, gui)
├── tools/catalog-to-netinstall.py         # apps.yaml -> page « Applications » de Calamares
├── config/
│   ├── package-lists/                     # 01 base · 02 bureau · 03 jeu
│   ├── hooks/normal/                      # 0100 → 0500 (hooks chroot)
│   ├── preseed/djerba.cfg.chroot          # réponses debconf (clavier)
│   └── includes.chroot/                   # Calamares (settings, modules, djerbaapps), polkit, SDDM, skel…
├── tests/
│   ├── run_local_lints.sh                 # shellcheck, py_compile, YAML/INI, XML, visudo
│   ├── test-yaml-ini.py                   # validateur strict mount.conf / flathub.conf / …
│   ├── test-djerba-ai.py                  # agent IA : registre, sécurité, boucle, démon root, bac à sable, Studio
│   ├── test-features.sh                   # inspection ISO + squashfs + chroot
│   ├── test-iso-qemu.sh                   # boot UEFI headless (OVMF / AAVMF)
│   └── test-live-session.sh               # session live réelle via la console série
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
| `tests/test-djerba-ai.py` | avant compilation | 228 capacités et schémas, paramètres hostiles refusés (aucun shell), mémoire, boucle agentique face à un faux serveur Ollama, confirmation refusée, mode dégradé, démon root en JSON-RPC réel (SO_PEERCRED, autorisation), bac à sable (correctif cassé annulé, correctif valide déployé puis retiré), Studio hors ligne, chargement réel de l'interface |
| `tests/test-live-session.sh` | après le build | démarre l'ISO et se connecte sur la console série : DNS (resolv.conf, NSS), Tor sous AppArmor et retour arrière du mode anonyme, mode jeu, mode discret (bascule puis restauration à l'identique), store, agent IA (capacité, démon root activé par socket, Studio) ; avec `--network` (CI) : résolution, HTTPS, Firefox, mode anonyme complet via Tor sans fuite IPv6 |
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
| Interface de l'agent | demandée en PyWebView + Three.js : réalisée en **QML + Qt Quick 3D**, déjà utilisés par tout le système (même rendu, repli logiciel en VM) — PyWebView imposerait un moteur web WebKitGTK (> 100 Mo) dont WebGL est souvent absent en machine virtuelle |
| Reconnaissance vocale | Faster-Whisper n'existe pas dans Debian (et `python3-whisper` y est une base de données, pas de la reconnaissance vocale) : pas de saisie vocale pour l'instant |
| `cpupower` pour le mode jeu | power-profiles-daemon pilote déjà gouverneur et EPP ; les deux se contrediraient : `game_optimizer` passe par lui, `cpupower_governor` (paquet `linux-cpupower`) sert sans lui |
| Suspension du compositeur | possible en session X11 (`org.kde.KWin /Compositor suspend`) ; sous Wayland, KWin *est* le serveur d'affichage : effets coupés et tearing autorisé à la place |
| Déploiement des correctifs | jamais sans accord (aperçu + mot de passe) et dans `/usr/local/bin`, pas dans `/usr/bin` : les fichiers des paquets restent intacts et un retour arrière est toujours possible |
| Unités systemd | `/usr/lib/systemd/{system,user}` (usr fusionné de Debian 13) ; le démon utilisateur est activé pour tous les comptes (`systemctl --global`) plutôt que copié dans `~/.config/systemd/user` |

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
| Revenir de l'apparence Windows | `djerba-undercover off` (`--verbose` pour le détail) |
| Pages web qui ne chargent pas | `djerba-anonsurf status` ; `resolvectl status` ; `ls -l /etc/resolv.conf` doit pointer vers `../run/systemd/resolve/stub-resolv.conf` |
| Djerba (IA) en « mode simplifié » | Modèles et réglages → Installer Ollama, puis télécharger un modèle ; `djerba-ai doctor` |
| Un correctif de Djerba pose problème | Auto-réparation → « Annuler le correctif », ou `djerba-ai run patch_rollback '{"name": "djerba-…"}'` |
| L'assistant ne s'ouvre plus | `djerba-welcome`, ou `Autostart=true` dans `~/.config/djerba-welcomerc` |

---

## Licences

* Code de ce dépôt (scripts, configurations, générateur, QML) : **GPL-3.0-or-later**.
* Visuels générés (logo, fonds, thèmes) : **CC-BY-SA-4.0**.
* Les paquets inclus restent sous leurs licences respectives (Debian, Mozilla,
  Flathub…). **Steam** est soumis à
  l'[accord de souscription Steam](https://store.steampowered.com/subscriber_agreement/) ;
  il n'est installé que si l'utilisateur le coche.
