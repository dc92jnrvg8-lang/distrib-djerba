# Plan « Autonomie » de Djerba OS

Objectif : faire de l'IA le cœur de Djerba OS — autonome, sûre par
réversibilité, indépendante du moteur d'IA, belle et simple. Chaque phase
est livrée avec ses tests et sa documentation ; la suivante ne commence que
si la précédente est verte (lints, tests de l'agent en root dans le
conteneur Debian 13, CI x86_64 et arm64).

| Phase | Sujet | État |
|---|---|---|
| 1 | Réversibilité : transactions, instantanés, undo, rollback auto et au boot | terminée (CI à confirmer) |
| 2 | Moteur de politique dans `sysd` (niveaux 0–3, modes, disjoncteur) | à faire |
| 3 | Couche unifiée de fournisseurs (Ollama, Tailscale, Jarvis, API cloud), parité | à faire |
| 4 | Historique par IA, mémoire, continuité | à faire |
| 5 | Studio unifié : plugins, applications, extensions | à faire |
| 6 | L'IA au cœur de l'expérience système | à faire |
| 7 | Diagnostic autonome, playbooks, recherche web sûre | à faire |
| 8 | Refonte de l'interface | à faire |
| 9 | Durcissement de sécurité | à faire |
| 10 | Stabilité, cohérence, qualité | à faire |
| 11 | Banc d'évaluation et matrice de tests | à faire |

---

## État des lieux (avant la phase 1)

* **Moteurs** (`core/llm.py`, `core/engine.py`) : Ollama local, Ollama
  distant (Tailscale), API compatible OpenAI relayée par le démon root ;
  repli distant → local ; poignée de main (`/api/show`) → profil d'invite ;
  sessions chiffrées et bascule à chaud. Manquent : interface `Provider`
  unique (embeddings, capacités déclarées), Anthropic, Jarvis, repli JSON
  contraint pour les modèles sans appels d'outils.
* **Mémoire** (`core/memory.py`) : SQLite chiffré partagé par tous les
  moteurs (conversations, leçons, incidents, index sémantique). Pas
  d'historique par moteur.
* **Studio** (`tools/devtools/app_generator.py`) et **auto-extension**
  (`core/extend.py` + `sys.test_plugin`) : génération, Git, Pytest en bac à
  sable (3 essais). Manquent : extensions de bureau, registre unifié des
  créations, AppArmor dans tout le pipeline.
* **Auto-réparation** (`core/selfheal.py`) : erreurs des outils `djerba-*`
  seulement, correctifs testés puis déployés dans `/usr/local/bin`.
* **Réversibilité** : outil manuel `snapshot_create` ; aucun journal, aucun
  undo. Sécurité fondée sur des confirmations, pas de politique côté root.

---

## Phase 1 — Réversibilité

### Conception

* **Journal de transactions** (`core/transactions.py`, démon root) : SQLite
  root 0600 `/var/lib/djerba-ai/transactions.db`. Une transaction : id,
  dates, uid, **moteur d'IA et session à l'origine**, méthode/outil,
  paramètres (masqués), sauvegarde associée, chemins créés/modifiés/supprimés,
  delta des paquets, sondes avant/après, état
  (`en cours`, `appliquée`, `annulée`, `annulée automatiquement`, `échec`).
* **Sauvegarde avant action** (`core/snapshots.py`), choisie automatiquement :
  * **Btrfs** (système installé) : instantané en lecture seule du
    sous-volume racine avant l'action et après ; `btrfs send --no-data -p
    avant après | btrfs receive --dump` donne la liste **exacte** des chemins
    touchés, sans que l'outil ait à les déclarer. L'undo restaure ces chemins
    depuis l'instantané « avant » (copie reflink puis renommage atomique, ou
    suppression). Journaux, caches et données de l'agent sont exclus.
  * **Fichiers** (session live, ext4, xfs) : archive de `/etc` et
    `/usr/local` (+ `/boot` et l'ESP pour les actions qui touchent
    l'amorçage), manifeste d'empreintes avant/après, delta dpkg. L'undo
    restaure les fichiers et inverse le delta de paquets avec APT.
* **Vérification** (`core/probes.py`) : sondes déterministes avant/après
  (unités en échec, `dpkg --audit`, NetworkManager, résolveur, route par
  défaut, unité ciblée). Régression → **undo automatique**, lui-même vérifié.
* **Undo** : `djerba-ai undo ID`, `djerba-ai undo --since DATE` (ordre
  antichronologique ; refus si une transaction plus récente a touché les
  mêmes chemins, sauf annulation de celle-ci d'abord).
* **Rollback au boot** (Btrfs + UEFI) : après chaque démarrage réussi, le
  clone inscriptible `@djerba-last-good` est rafraîchi ; un compteur GRUB
  stocké sur l'ESP (GRUB ne peut pas écrire sur Btrfs) bascule sur l'entrée
  « dernier démarrage réussi » après deux échecs consécutifs ; entrée de
  restauration dans le menu GRUB ; `djerba-ai rollback --make-permanent`.

### Critères de réussite

- [x] Action root → undo → état du système de fichiers identique (Btrfs réel
      sur image loop dans le conteneur, et sauvegarde par fichiers).
- [x] Vérification post-action en échec → rollback automatique, état
      identique, transaction marquée « annulée automatiquement » (régression
      des sondes **et** échec de l'action avec modifications partielles).
- [x] Chaque transaction porte le moteur et la session d'origine.
- [x] `undo --since` annule dans l'ordre inverse ; conflit détecté.
- [x] Scripts GRUB générés valides (`grub-script-check`), compteur sur ESP
      (`grub-editenv`) ; `@djerba-last-good` et « rendre définitif » testés
      sur Btrfs réel.
- [x] Lints et 113 tests de l'agent verts en local ; contrôle en session live
      (fuseau horaire modifié par root, puis `djerba-ai undo` → identique).
- [ ] CI verte (x86_64 et arm64).

### Décisions

* **Relevé exact des chemins par `btrfs send --no-data`** plutôt que des
  déclarations par outil : aucun outil (ni plugin futur) ne peut « oublier »
  un fichier. Hors Btrfs, périmètre limité et documenté (`/etc`,
  `/usr/local`, `/boot`, paquets).
- **Tout ou rien** : une action en échec qui a laissé des modifications est
  annulée, même sans régression détectée.
- **Pas d'action sans sauvegarde** : si l'instantané ou la copie échoue,
  l'action est refusée.
- Rollback au démarrage par **clone inscriptible** (`@djerba-last-good`)
  plutôt que démarrage sur un instantané en lecture seule (qui exigerait un
  overlay dans l'initramfs) ; compteur sur l'ESP car GRUB n'écrit pas sur
  Btrfs.
- Outils root de lecture marqués `mutates=False` (15) : aucune sauvegarde
  inutile.

### Limites restantes

* Rollback au démarrage indisponible avec racine chiffrée (LUKS), en BIOS,
  ou hors Btrfs ; test sur machine réelle (deux démarrages ratés) prévu en
  phase 11 (matrice QEMU).
* Sans Btrfs, les fichiers hors `/etc`, `/usr/local`, `/boot` ne sont
  annulés qu'à travers le delta de paquets (réinstallation d'une version
  retirée seulement si elle est encore disponible dans les dépôts).
* Les actions faites **en espace utilisateur** (thème, fichiers du dossier
  personnel) ne sont pas encore dans le journal : prévu avec la politique
  (phase 2) et l'historique (phase 4).
* Les sondes ne couvrent pas encore les symptômes spécifiques (playbooks de
  la phase 7).
