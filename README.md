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
| Agent IA | **Djerba** : 251 capacités système, cerveau local, Tailscale ou en ligne avec repli, Spotlight (Meta+Espace), auto-réparation, auto-extension, Studio | idem |
| Sécurité | UFW, ClamAV + ClamTk, AppArmor, mises à jour automatiques | idem |
| Installation | Calamares, choix des applications, BTRFS `@`/`@home`, zstd, sans swap disque | idem |

---

## Sommaire

> Documentation technique complète (chaque fichier, chemin, service, capacité, contrat interne) : [`docs/DOCUMENTATION-TECHNIQUE.md`](docs/DOCUMENTATION-TECHNIQUE.md).

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
  (`/etc/xdg/djerba/kcm-about-distrorc`, placé en tête de `XDG_CONFIG_DIRS`
  par `/etc/xdg/plasma-workspace/env/djerba-xdg.sh` et
  `/usr/lib/environment.d/60-djerba-xdg.conf` ; ce dossier ne contient que des
  fichiers de configuration KDE, sans effet sur GNOME). `/etc/xdg/kcm-about-distrorc` reste à `desktop-base` : aucun
  conflit de fichiers quand Discover ou GNOME tirent ce paquet Debian
  (`Replaces: desktop-base`, `Breaks`/`Conflicts: desktop-base (<< 13.0.4)` ;
  l'ancien conffile est retiré par `dpkg-maintscript-helper rm_conffile`).
* **`djerba-tools`** : assistant d'accueil, gestionnaire d'applications, mode
  anonyme, mode discret, mode jeu, actions polkit et services systemd.

**Tout est annulable.** Chaque action qui modifie le système (outil root de
l'agent, correctif, réglage, installation) est précédée d'une sauvegarde
(instantané Btrfs sur le système installé, copie de `/etc` et `/usr/local`
ailleurs), vérifiée par des sondes objectives (services, dpkg, réseau) et
**annulée automatiquement** en cas de régression. Chaque modification est
tracée avec le moteur d'IA et la conversation à l'origine :
`djerba-ai transactions`, `djerba-ai undo ID`, `djerba-ai undo --since
2026-10-28`. Après deux démarrages ratés, GRUB revient seul au dernier
démarrage réussi (`@djerba-last-good`, Btrfs + UEFI) ;
`djerba-ai rollback --make-permanent` conserve l'état restauré. Détails :
`docs/DOCUMENTATION-TECHNIQUE.md` §15 bis, plan : `docs/PLAN-AUTONOMIE.md`.

**L'agent demande, le système décide.** Une politique déterministe, dans le
démon root et identique pour tous les moteurs d'IA, classe chaque action
(0 lecture, 1 réversible, 2 système sensible, 3 irréversible). En mode
« autonome » (par défaut), Djerba répare et règle seul jusqu'au niveau 2 —
tout est sauvegardé et annulable — mais ne touche jamais aux chemins
protégés (amorçage, comptes, sudo, polkit, son propre code, ses journaux) et
n'agit jamais de façon irréversible. Un contenu non fiable (page web,
fichier, réponse d'un moteur distant) plafonne la tâche au niveau 1.
`djerba-ai policy`, `djerba-ai pause` / `resume` ; changer de mode
(« prudent », « autonome », « total ») demande le mot de passe administrateur.

**dpkg toujours réparable.** Une transaction interrompue (coupure, conflit
de fichiers, script en échec) bloquait Discover sur un écran vide.
`djerba-dpkg-repair` détecte l'incohérence sans privilège (`dpkg --audit`,
`/var/lib/dpkg/updates`, en ignorant les transactions en cours relevées dans
`/proc/locks`) et répare en root, hors transaction : `dpkg --configure -a`,
`apt-get -f install` ; un conflit avec un fichier d'un paquet Djerba est
résolu par `--force-overwrite` sur la seule archive concernée (jamais entre
paquets tiers). Déclencheurs : crochet APT `DPkg::Post-Invoke` (exécuté même
après un échec, réparation programmée par `systemd-run` sans bloquer APT),
postinst de `djerba-branding`, minuterie `djerba-dpkg-repair.timer` (2 min
après le démarrage puis toutes les 6 h), et `djerba-apps` avant/après chaque
installation. Dans la session (KDE comme GNOME), une notification
« Réparer » apparaît si l'état reste incohérent (pkexec, action polkit
`org.djerba.dpkg-repair`). Journal : `/var/log/djerba/dpkg-repair.log`.

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

**Djerba** (menu « Djerba », `djerba-ai`) est le cœur de Djerba OS : l'IA pilote
le système, en permanence au fait de son état, tout en restant découplée du
moteur d'inférence (Ollama local, machine distante via Tailscale, API en ligne),
depuis une barre flottante ou une application complète.

```
 demande ─▶ UserPrompt (Pydantic) ─▶ garde anti-injection ─▶ CognitiveEngine ─▶ fournisseur (local / Tailscale / cloud)
                                                              │  poignée de main, profil d'invite, session chiffrée
 bus de contexte (CPU, RAM, E/S, journal, fenêtre KWin) ──────┤
 mémoire chiffrée + DLP ◀─────────────── agent ───────────────┘── outils ──▶ niveau de risque ──▶ confirmation / validation
                                                                     │ root
                        djerba-ai-sysd ◀── JSON-RPC strict, SO_PEERCRED, appelant signé, polkit, audit chaîné (avant/après)
                              └──▶ bac à sable hybride (namespaces, seccomp-bpf, cgroups v2, gVisor) ─▶ Pytest, Git
```

| Élément | Rôle |
|---|---|
| **Anneau d'action** (`Super+Espace`) | HUD flottant (KDE comme GNOME) : actions contextuelles disposées en anneau autour d'un champ de saisie, selon l'application au premier plan (navigateur, fichiers, terminal, éditeur, jeu…), complétées par les suggestions de l'agent (validées par schéma, titre de fenêtre traité comme donnée non fiable) ; réponse en continu et confirmations sur place ; modèle préchargé dès l'ouverture |
| **Multi-bureau** (`ui/adapter.py`) | une seule interface, deux implémentations natives : **KDE Plasma** (script KWin pour la fenêtre active, KGlobalAccel, kdeglobals) et **GNOME Shell 46+** (extension `djerba@djerba-os.org` pour Mutter, raccourcis GSettings — Super+Espace libéré du changement de disposition —, thème `color-scheme`) ; détection à chaque session, réglages et raccourcis rétablis après un changement de bureau. GNOME est proposé à l'installation (catalogue, « Bureaux supplémentaires ») : session complète (`gnome-session`, `gnome-session-xsession`, Xwayland, `dbus-user-session`, `gnome-settings-daemon`…) vérifiée après installation (fichiers de session, schémas GSettings), sans `gdm3` (SDDM reste le gestionnaire de connexion). **Mode dégradé silencieux** : chaque étape d'intégration (détection, extension, raccourcis, greffons) est isolée ; un échec est consigné (`~/.local/state/djerba-ai/gnome-crash.log` et, par le démon root, `/var/log/djerba/gnome-crash.log`) et n'interrompt jamais la session ; l'extension GNOME contient ses erreurs, et un plantage de GNOME Shell ou de gnome-session (`OnFailure=`) produit un rapport avec l'extrait du journal |
| **Applications** | menu « Djerba » de **Dolphin** (menu de service) et de **Nautilus** (python3-nautilus) sur les fichiers ; outils externes de **Kate** et greffon **Gedit** (Ctrl+Alt+E/K/T) sur la sélection, transmise par l'entrée standard |
| **Presse-papiers** (`Meta+Maj+C`) | expliquer, résumer, traduire, corriger le texte copié, ou vérifier une commande |
| **Tableau de bord** | flux de pensées de l'agent (poignée de main, garde anti-injection, capacités choisies, actions et niveau de risque, résultats, bascules de moteur, réflexion des modèles qui l'exposent), télémétrie vivante (bus de contexte, latences, mémoire de l'agent), **validations explicites** des actions à haut risque (saisie de « CONFIRMER »), contrôle de l'audit et du bac à sable |
| **Mode Studio** (`djerba-ai`) | discussion, santé, auto-réparation, Studio (applications, historique, bac à sable, **audit en direct**), capacités, cerveau et réglages ; orbe Qt Quick 3D, **2D vectorielle** si le GPU n'a pas d'accélération 3D fiable (détection `/sys/class/drm`) |
| Icône résidente | pastille d'état (vert prêt, bleu au travail, orange repli local, rouge indisponible), menu (Spotlight, cerveau, Studio, réglages), notifications ; une seule instance (`$XDG_RUNTIME_DIR/djerba-ai/gui.sock`) |
| `/etc/djerba/config.toml` | configuration centrale (root, **0600**) : `[llm]` (fournisseur, adresses, modèles, clé d'API), `[shortcuts]`, `[system]` (bac à sable 15 s / 3 essais, chien de garde, alerte disque < 10 %), `[agent]` ; vue publique sans secret `config.public.json` surveillée par l'interface (synchronisation en temps réel), historique des réglages dans `memory.db` |
| `core/engine.py` | **couche d'abstraction cognitive** : entrées/sorties normalisées par schémas **Pydantic v2** (`core/schemas.py`), ingestion durcie (`core/guard.py`), poignée de main dynamique (latence, taille, contexte, appels d'outils, réflexion via `/api/show` → profil compact / standard / étendu de l'invite système), état de session persisté de façon **atomique, chiffrée et asynchrone** : bascule à chaud entre moteurs sans perte de contexte |
| `core/contextbus.py` | **bus de contexte** permanent : CPU, mémoire, E/S disque et réseau, erreurs du journal, fenêtre active (script KWin → D-Bus), chaque valeur horodatée (UTC, ms, séquence, instantané périmé signalé), chiffré en AES-256-GCM dans `$XDG_RUNTIME_DIR` |
| `core/telemetry.py` | **télémétrie** native : `http://127.0.0.1:9464/metrics` (Prometheus ; tableau de bord Grafana fourni) et `/health` (Uptime Kuma) — mémoire, CPU et latences de l'agent, du moteur, des capacités et du démon root |
| `core/ghost.py` | **Ghost Runner** : tâches de fond dans des services systemd utilisateur dédiés (`djerba-ghost-*`, quotas cgroups v2, sans privilège, sans accès au démon root) |
| `core/memory.py`, `core/dlp.py` | **mémoire permanente chiffrée** (SQLite, AES-256-GCM par colonne, transactions, leçons et préférences, index vectoriel) et **DLP à deux niveaux** (règles déterministes puis analyse contextuelle par tokenizer et entropie : les secrets réels vont chiffrés au coffre, les gabarits et exemples de code restent intacts) |
| `djerba_ai.tools` | **263 capacités** découvertes par introspection (`BaseTool`) : maintenance 31, performances 27 (dont `game_optimizer`), sécurité 30, bureau 26, applications 27, matériel 20, agent 44, système 21, Studio 19, **os 18** (apt, Flatpak, fichiers, `chmod`/`chown`, recherche par nom et **sémantique**) |
| `djerba-ai-sysd` | démon root activé par la socket `/run/djerba-ai/sysd.sock` : JSON-RPC analysé par **schémas Pydantic stricts** (champ inconnu, type approximatif ou méthode absente : rejet immédiat), appelant identifié par `SO_PEERCRED` puis **vérifié** (`core/trust.py` : interpréteur système en mode isolé `-I`, point d'entrée `/usr/bin/djerba-ai` dont l'empreinte figure au manifeste signé du paquet, aucune injection `LD_*`), autorisation **polkit** (`exec`, `patch`, `deploy`, `setup`, `configure`, `cloud`, `audit`, `extend`), **seul** à lire la clé d'API et à écrire `config.toml` ; refuse les applications du Studio et les tâches de fond ; **registre d'audit immuable** (`/var/log/djerba/audit.log` : demande consignée *avant* exécution, chaîne de hachages SHA-256 et tête de chaîne, attribut noyau « ajout seul », rotation interne) ; ordonné après `network-online.target` et `tailscaled.service` |
| `core/sandbox.py` | **runtime hybride** du code non fiable : service transitoire systemd (DynamicUser, espaces de noms réseau/IPC/PID/utilisateurs/montages, `SystemCallFilter` seccomp-bpf, `RestrictAddressFamilies=AF_UNIX`, MemoryMax/CPUQuota/TasksMax cgroups v2, durée bornée) ; repli `unshare` + cgroup v2 + filtre libseccomp ; **gVisor** (`runsc`) pour les instructions arbitraires si installé ; dossiers jetables purgés après chaque exécution |
| `djerba-ai.service` (utilisateur) | surveillance du journal (incidents), chien de garde (disque sous le seuil, services en échec), maintenance quotidienne, mise à jour hebdomadaire du modèle local |

**Cerveaux.** Trois fournisseurs, choisis dans les réglages (sélecteur avec
pastille de connexion en temps réel) ou par l'agent (`brain_switch`) :

* **Ollama local** (`127.0.0.1:11434`) — rien ne sort de la machine ;
* **Mac Mini via Tailscale** — Ollama d'une machine puissante du tailnet
  (nom MagicDNS ou adresse 100.x), état du pair lu par `tailscale status --json` ;
* **API en ligne** — OpenAI, Google Gemini (point d'accès compatible OpenAI) ou
  serveur compatible (HTTPS, ou `http://127.0.0.1` pour llama.cpp / LM Studio).
  La requête est **relayée par le démon root** : la clé ne quitte jamais root,
  les jetons reviennent en continu.

Si le cerveau distant ne répond pas, ou pas dans les **5 secondes**, Djerba
bascule **de façon transparente** sur le modèle local et vous prévient
(notification Plasma, pastille orange).

**Transport Ollama** (`core/llm.py`). Connexions HTTP **persistantes**
(keep-alive, réserve partagée entre requêtes ; **HTTP/2** négocié par ALPN
avec `httpx`/`h2` sur un point d'accès TLS — `tailscale serve`, proxy
inverse —, Ollama lui-même parlant HTTP/1.1 en clair), délais séparés :
**connexion 5 s**, **60 s au plus entre deux fragments** du flux (le premier
fragment, qui attend le chargement du modèle en mémoire, garde son propre
délai). Une connexion fermée sans réponse (« Remote end closed connection
without response » : connexion persistante périmée, moteur redémarré, relais
Tailscale coupé) est **rejouée 3 fois avec attente exponentielle** sur une
connexion neuve, sans jamais dupliquer de jetons ; si le moteur ferme encore,
une requête minimale (sans outils ni réflexion) est tentée. Le corps
`/api/chat` est **validé par Pydantic** avant l'envoi (`OllamaChatRequest` :
`messages` non vide aux rôles connus, `stream` toujours explicite, outils au
format JSON Schema, champs internes retirés). Si Ollama n'écoute pas sur la
boucle locale (`OLLAMA_HOST` lié à l'adresse Tailscale ou du réseau local),
l'adresse joignable est trouvée (`::1`, `tailscale ip -4`, adresses globales)
et mémorisée ; les embeddings passent par le même transport.

**Auto-évolution.** Quand aucune capacité ne convient (même après
`tools_search`), Djerba écrit un module Python (`BaseTool`) **et ses tests
Pytest**, les fait vérifier par le démon root dans le bac à sable Git
`/var/lib/djerba-ai/sandbox` : syntaxe, pyflakes, dépendances éventuelles dans
un venv temporaire (roues binaires uniquement), chargement puis **tests Pytest
avec 15 s au plus par test** (pytest-timeout), le tout dans le runtime hybride
(sans réseau). Jusqu'à **3 tentatives** avec la sortie de Pytest en retour ;
sinon état propre absolu : `git reset --hard`, `git clean`, venv supprimé,
dossier purgé, mémoire libérée. Validé, le module et ses tests sont enregistrés
dans `~/.local/share/djerba/plugins/` et **intégrés à chaud**. Une capacité
générée n'est jamais root et chaque exécution reste confirmée. Les dépendances
Python éventuelles vont dans le venv interne `/usr/share/djerba-ai/venv`
(PEP 668 : jamais de `pip` global).

**Défense active** (`djerba_ai/security/`).

* **AppArmor dynamique** : chaque application du Studio et chaque capacité
  générée reçoit un profil strict généré (`djerba-app-*`, `djerba-plugin-*` :
  système en lecture seule, son dossier seul en écriture, secrets — `~/.ssh`,
  trousseaux, mémoire et configuration de Djerba — refusés, ni capacité, ni
  montage, ni ptrace), vérifié par `apparmor_parser` puis chargé par le démon
  root ; appliqué par `aa-exec` et par le bac à sable (`AppArmorProfile=`).
* **Confiance zéro réseau** : aucun code généré n'atteint Internet par
  défaut — seccomp (sockets UNIX seules) toujours, AppArmor sans règle réseau
  en plus ; l'accès se donne au tableau de bord (« CONFIRMER » + mot de passe
  administrateur, liste `security.network_grants`) et se retire à tout moment.
  Une capacité générée ne s'exécute **jamais** dans le processus de l'agent :
  un mandataire la lance dans un processus isolé, environnement minimal.
* **IDS** (`djerba-ai-ids.service`, root) : force brute SSH, balayage de
  ports, rafales d'échecs d'authentification, violations de confinement du
  code généré ; blocage ciblé et temporaire de l'adresse dans nftables
  (`inet djerba_ids`), décision inscrite au registre d'audit, déblocage depuis
  le tableau de bord.

**Garde anti-injection.** Toute demande est validée (schéma `UserPrompt` :
Unicode normalisé, caractères invisibles et de contrôle retirés, longueur
bornée), ses jetons de rôle (`<|im_start|>`, `[INST]`…) neutralisés et ses
indices d'injection notés (consignes à ignorer, changement d'identité,
exfiltration, faux appels d'outils, charges base64…). Toute sortie d'outil,
page web ou fichier lu est encadrée comme **donnée non fiable** ; si elle porte
des indices d'injection, la session est **contaminée** : toute action sensible
passe au risque « élevé » et exige une validation explicite.

**Niveaux de risque.** Lecture : faible (sans confirmation) ; modification ou
droits root : modéré (confirmation, puis polkit) ; actions critiques (fichiers
système, démarrage, pare-feu, mises à niveau globales, code déployé) ou toute
action sensible d'une session contaminée : **élevé** (saisie de « CONFIRMER »).

**Isolation.** Les applications du Studio tournent dans un scope systemd
(`systemd-run --user --scope`, unité `djerba-app-*`), environnement nettoyé et
ressources bornées ; le démon root rejette tout processus de ces cgroups.

**Fonctionnement.** À chaque demande, l'agent reçoit l'état de la machine
(version, matériel, session live ou installée, modes anonyme / discret / jeu,
applications, services en échec, espace disque, réseau) et ce qu'il a retenu de
vous. Il choisit les capacités pertinentes (recherche + historique de réussite ;
`tools_search` lui donne accès aux 251), les appelle, lit leurs résultats et
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
modèles Snake, calculatrice et chronomètre restent disponibles.

**Premier lancement.** Un assistant présente Djerba, fait choisir le cerveau,
rappelle les raccourcis et règle recherche web, auto-réparation et
auto-extension. Les raccourcis se modifient dans les réglages : la combinaison
pressée est écrite dans `~/.config/kglobalaccelrc` et appliquée aussitôt à
KGlobalAccel par D-Bus.

En terminal : `djerba-ai --spotlight`, `djerba-ai --clipboard`,
`djerba-ai ask "…"`, `djerba-ai tools [recherche]`,
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
| `tests/test-djerba-ai.py` | avant compilation (image Debian 13, en root : bac à sable réel) | 64 tests : schémas Pydantic et JSON-RPC strict, garde anti-injection directe et indirecte (contamination de session, risque élevé), poignée de main et profils, session chiffrée et bascule à chaud sans perte de contexte, appelants signés, audit chaîné (falsification et troncature détectées), runtime hybride (sonde d'isolation, Pytest réel : tests en échec, absents ou sans fin annulés), bus de contexte, exportateur Prometheus et `/health`, Ghost Runner, mémoire chiffrée et migrée, DLP à deux niveaux (faux positifs écartés) ; 251 capacités et schémas, paramètres hostiles refusés (aucun shell), mémoire et masquage des secrets, `config.toml` (0600, vue publique, validation), repli local (distant injoignable ou trop lent), API en ligne relayée par le démon (clé jamais exposée, jetons en continu), audit JSON, refus des applications isolées, capacités « os » (chemins vitaux refusés, recherche sémantique), auto-extension en 3 tentatives et intégration à chaud, bac à sable réel des greffons (valide, cassé, boucle infinie coupée), raccourcis `kglobalaccelrc`, détection GPU, instance unique, chargement réel de l'interface et de Spotlight |
| `tests/test-live-session.sh` | après le build | démarre l'ISO et se connecte sur la console série : DNS (resolv.conf, NSS), Tor sous AppArmor et retour arrière du mode anonyme, mode jeu, mode discret (bascule puis restauration à l'identique), store, agent IA (capacité, démon root activé par socket, `config.toml` écrit par le démon et synchronisé, audit, bac à sable des greffons, application isolée refusée, recherche sémantique, icône résidente et Spotlight, Studio) ; avec `--network` (CI) : résolution, HTTPS, Firefox, mode anonyme complet via Tor sans fuite IPv6 |
| `tests/test-iso-qemu.sh` | après le build | démarrage **UEFI** (OVMF / AAVMF), console série : succès sur `djerba login:`, **bannière « Djerba OS »** vérifiée, échec sur panique ou shell initramfs ; capture d'écran QMP |

---

## CI/CD GitHub Actions

`.github/workflows/build-and-test.yml` exécute une **matrice native** :
`x86_64` sur `ubuntu-24.04` (KVM) et `arm64` sur `ubuntu-24.04-arm` (TCG) —
libellés épinglés plutôt que `ubuntu-latest`, actions sur Node 24.

Étapes : checkout → libération d'espace (nettoyage APT tolérant) → lints →
génération des assets et de la page Applications → cache des paquets
live-build → image Docker + `./build.sh` → tests de l'agent dans l'image
Debian 13 (en root : bac à sable réel) → tests fonctionnels → test QEMU →
session live avec réseau → publication.

**Télécharger l'ISO live.** Une ISO pèse ~2,5 Gio par architecture : elle
n'est jamais envoyée comme artefact (quota de stockage d'Actions) mais comme
**asset de release**, non décompté de ce quota :

* chaque poussée réussie met à jour la pré-release
  **« Djerba OS — version de développement »** (tag `djerba-os-dev`, onglet
  *Releases* du dépôt) ;
* un tag `v*` publie la release « Djerba OS vX ».

Une ISO de plus de 2 Gio est découpée : `cat djerba-os-*.iso.part* >
djerba-os.iso` puis `sha256sum -c djerba-os-*.iso.sha256`. Seuls les journaux
de test restent des artefacts (7 jours).

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
| GNOME et ses intégrations | GNOME n'est pas dans l'image live (taille) : il s'installe depuis l'installateur ou `djerba-apps` ; sous Wayland, l'extension n'est chargée qu'à la session suivante. GNOME Text Editor n'a pas d'API de greffons : Gedit et Nautilus portent l'intégration |
| AppArmor en session live | Debian ne charge pas AppArmor sur un média live : les profils générés y sont écrits mais le confinement repose sur seccomp, espaces de noms et cgroups ; il s'ajoute sur le système installé |
| Appelants « signés » | le manifeste d'empreintes est généré à la construction du paquet et protégé par les droits root ; la vérification s'ajoute à polkit (défense en profondeur), elle ne le remplace pas. `kernel.yama.ptrace_scope=1` empêche un programme de l'utilisateur de s'attacher à l'agent en cours d'exécution |
| Chiffrement de la mémoire | clé aléatoire par utilisateur (`memory.key`, 0600) : protège la base copiée, sauvegardée ou lue par un autre compte ; pas un logiciel malveillant exécuté sous le même compte. SQLCipher n'étant pas dans Debian, le chiffrement est fait par colonne (AES-256-GCM, python3-cryptography) |
| gVisor | `runsc` n'est pas empaqueté par Debian : utilisé automatiquement s'il est installé (dépôt officiel gVisor), sinon le runtime systemd (ou unshare) est le plus isolé |
| Flux de pensées | le tableau de bord montre les décisions de l'agent et, pour les modèles qui l'exposent (Ollama `think`), leur réflexion ; il ne peut pas révéler un raisonnement qu'un modèle ne fournit pas |
| Fenêtre active | script KWin (Plasma 6) poussant chaque changement de focus par D-Bus ; titres désactivables (`context.window_titles`) et toujours passés au DLP |
| `After=tailscaled.service` | porté par le service **système** `djerba-ai-sysd.service` (qui relaie les cerveaux distants et en ligne) : un service *utilisateur* comme `djerba-ai.service` ne peut pas s'ordonner par rapport à des services système |
| Tailscale | absent de Debian : installable depuis le dépôt officiel de Tailscale ; sans lui, une connexion directe à l'hôte est tentée |
| Flou d'arrière-plan | sous X11/XWayland via `_KDE_NET_WM_BLUR_BEHIND_REGION` ; sous Wayland natif, le protocole de flou de KWin n'est exposé que par KWindowSystem (C++), absent de PyQt6 : fond translucide teinté |
| Plasmoïde | l'icône résidente (StatusNotifierItem de la zone de notification) remplit ce rôle ; aucun composant C++ à compiler, l'interface est entièrement en QML chargé à l'exécution |
| Embeddings | modèle d'Ollama (`nomic-embed-text` par défaut) si disponible, sinon vecteurs locaux par hachage de n-grammes (512 dimensions) : la recherche reste hors ligne |
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
| Discover bloqué, « dpkg was interrupted », « trying to overwrite » | `/usr/libexec/djerba-dpkg-repair --check`, puis `pkexec /usr/libexec/djerba-dpkg-repair --repair` (journal : `/var/log/djerba/dpkg-repair.log`) |
| « moteur injoignable … Remote end closed connection » | `djerba-ai doctor` ; le client rejoue 3 fois (attente exponentielle) puis tente une requête minimale ; si Ollama écoute sur l'adresse Tailscale ou du réseau local (`OLLAMA_HOST`), l'adresse est trouvée automatiquement |
| Session GNOME qui revient à l'écran de connexion | `sudo /usr/libexec/djerba-dpkg-repair --repair` (installation GNOME interrompue), puis `/var/log/djerba/gnome-crash.log` et `~/.local/state/djerba-ai/gnome-crash.log` |

---

## Licences

* Code de ce dépôt (scripts, configurations, générateur, QML) : **GPL-3.0-or-later**.
* Visuels générés (logo, fonds, thèmes) : **CC-BY-SA-4.0**.
* Les paquets inclus restent sous leurs licences respectives (Debian, Mozilla,
  Flathub…). **Steam** est soumis à
  l'[accord de souscription Steam](https://store.steampowered.com/subscriber_agreement/) ;
  il n'est installé que si l'utilisateur le coche.
