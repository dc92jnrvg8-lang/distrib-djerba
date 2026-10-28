# Plan « Autonomie » de Djerba OS

Objectif : faire de l'IA le cœur de Djerba OS — autonome, sûre par
réversibilité, indépendante du moteur d'IA, belle et simple. Chaque phase
est livrée avec ses tests et sa documentation ; la suivante ne commence que
si la précédente est verte (lints, tests de l'agent en root dans le
conteneur Debian 13, CI x86_64 et arm64).

| Phase | Sujet | État |
|---|---|---|
| 1 | Réversibilité : transactions, instantanés, undo, rollback auto et au boot | **terminée** |
| 2 | Moteur de politique dans `sysd` (niveaux 0–3, modes, disjoncteur) | **terminée** |
| 3 | Couche unifiée de fournisseurs (Ollama, Tailscale, Jarvis, API cloud), parité | en cours |
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
- [x] CI verte (x86_64 et arm64) : 113 tests de l'agent, 47/47 contrôles en session live.

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

---

## Phase 2 — Moteur de politique dans `sysd`

### Conception

* **`core/policy.py`** (démon root uniquement) : décision déterministe
  `decide(outil, paramètres, origine) → Décision(autorisé, niveau, exigences,
  raison)`. L'agent **demande**, le démon **décide** ; la décision est la même
  quel que soit le moteur d'IA (local, Tailscale, Jarvis, cloud).
* **Politique versionnée** livrée par le paquet :
  `/usr/share/djerba-ai/policy.toml` (lecture seule, hors de portée de
  l'agent : aucune méthode RPC ne l'écrit, chemin interdit en dur) :
  niveaux par outil, racines autorisées (listes blanches) pour
  `os_sys_file_write` / `os_sys_chown` / `os_sys_chmod`, chemins et actions
  interdits en dur, plafonds de débit.
* **Niveaux** (par réversibilité) : 0 lecture ; 1 réversible local (service,
  caches, paquet) → autonome + transaction ; 2 `/etc`, noyau, GRUB, pare-feu,
  pilotes → autonome + transaction + vérification + rollback + notification
  après coup ; 3 irréversible → refusé en autonomie (mode « total » : exige une
  sauvegarde préalable explicite).
* **Interdits en dur** (code, pas configuration) : bootloader et ESP en
  écriture directe, clés Secure Boot, `/etc/shadow`, `/etc/gshadow`, code de
  `sysd`, politique, instantanés, journal d'audit, journal de transactions,
  manifeste de confiance.
* **Modes** : « prudent » (confirmation hors bande par polkit pour tout
  niveau ≥ 1), « autonome » (défaut), « total » (avertissement explicite).
  Changement de mode : méthode dédiée `policy.set_mode`, polkit `auth_admin`
  (mot de passe à chaque fois) → l'IA ne peut pas l'activer seule.
* **Session « tainted »** (contenu non fiable lu pendant la tâche) : niveau
  plafonné à 1 jusqu'à la fin de la tâche (`origin.tainted`).
* **Plafonds** : N actions de niveau ≥ 1 par minute et par session ; même
  correction 3 fois → arrêt (détection de boucle) ; **disjoncteur global**
  `djerba-ai pause` / `resume` (`/var/lib/djerba-ai/paused`).
* **Profil de confiance par moteur** (préparé pour la phase 3) : plafond de
  niveau par moteur dans la politique, sans retirer de fonctionnalité visible.
* **Refus journalisés** (audit + compteur Prometheus), visibles au tableau
  de bord.
* Côté agent : `needs_confirmation` remplacé par la décision du démon
  (plus de confirmation en mode autonome jusqu'au niveau 2).

### Critères de réussite

- [x] Table de décision testée (niveaux × modes × tainted × moteur).
- [x] Chemins interdits refusés pour tous les outils d'écriture, y compris
      par traversée (`..`, liens symboliques).
- [x] Contenu piégé lu par l'agent → demande d'écriture système refusée par
      le démon root (test de bout en bout, aucune transaction exécutée).
- [x] Boucle (même action 3 fois en 1 h) arrêtée ; débits ; disjoncteur.
- [x] Changement de mode impossible sans authentification administrateur ;
      `config.set` refuse `policy.*`.
- [x] Même décision pour tous les moteurs à plafond égal (test de parité).
- [x] Lints et 120 tests verts en local ; contrôles ISO et session live
      (pause → action refusée → reprise).
- [x] CI verte (x86_64 et arm64) : 120 tests, 48/48 contrôles en session live.

### Décisions

* La politique (niveaux, racines, plafonds) est un **fichier du paquet**,
  versionné et testé contre le registre ; les **interdits** sont dans le
  code : aucune configuration ne peut les lever.
* Confirmation du mode « prudent » **hors bande** (fenêtre polkit, mot de
  passe de l'utilisateur) : ni le modèle ni une page piégée ne peuvent la
  simuler. Les confirmations intégrées au chat sont supprimées.
* Outil root inconnu → niveau 2 (prudence par défaut) ; démon injoignable →
  agent plafonné au niveau 1.
* Le niveau 3 n'est jamais autonome ; en mode « total », il exige
  l'authentification administrateur **à chaque fois** (`auth_admin` sans
  mémorisation).

### Limites restantes

* Le niveau 3 en mode « total » n'a pas encore de sauvegarde automatique
  des données de l'utilisateur (déplacement vers la corbeille prévu avec
  l'historique, phase 4).
* Les profils de capacité par moteur (plafonds calculés automatiquement)
  viendront avec la phase 3 ; aujourd'hui, `policy.set_caps` les fixe.
* L'interface de la politique (refus, disjoncteur) est minimale : la refonte
  est prévue en phase 8.

---

## Phase 3 — Couche unifiée de fournisseurs d'IA

### Conception

* **Registre des moteurs** (`core/engines.py`) : plusieurs moteurs configurés
  en parallèle — identifiant, type (`ollama` local ou distant, `jarvis`,
  `openai`, `gemini`, `anthropic`, `openai_compat`), nom, icône, couleur,
  rôle, adresse, modèle, fenêtre de contexte, profil de capacité mesuré,
  règles de confidentialité. Stockage root : `/etc/djerba/engines.json`
  (0600, secrets compris) et vue publique `/etc/djerba/engines.public.json`
  (0644, sans secrets). Migration automatique depuis `llm.*`.
* **Interface unique** : chaque moteur devient un `BaseLLMProvider` (chat en
  flux, appels d'outils, santé, embeddings si disponible). Les moteurs à
  secret (Jarvis, API en ligne) sont **relayés par le démon root** : jeton et
  clés ne quittent jamais root. Ollama local/distant : connexion directe.
* **Les outils appartiennent à Djerba** : le moteur propose, Djerba valide
  (schéma) puis la politique de la phase 2 décide. Origine des actions :
  identifiant du moteur ; plafonds de la politique par moteur ou par type.
* **Parité** : moteur sans appels d'outils natifs → repli « JSON contraint »
  (`format` JSON d'Ollama, schéma strict, 2 réessais), même résultat côté
  agent. Petites fenêtres : résumé de l'historique et outils filtrés.
* **Profil de capacité** mesuré à l'ajout (poignée de main + mini-banc :
  appel d'outil, JSON, vitesse, code) → libellé simple (« Très adapté »,
  « Adapté », « Limité : réparations simples seulement ») et plafond de
  niveau proposé à la politique.
* **Jarvis** (aucune information sur son API à ce jour : contrat minimal
  défini et documenté dans `docs/JARVIS-CONTRAT.md`, adaptateur de référence
  et serveur factice de test) :
  * Jarvis comme moteur : HTTP compatible OpenAI (`/v1/chat/completions` en
    flux SSE, `/v1/models`, `/v1/health`), jeton Bearer obtenu par
    appairage (`POST /v1/pair` avec un code à 6 chiffres affiché par
    Jarvis), joignable par Tailscale ;
  * Djerba comme serveur d'outils pour Jarvis : serveur MCP minimal
    (`initialize`, `tools/list`, `tools/call`) en HTTP, lié à l'interface
    Tailscale, jeton par appelant (appairage par code ou QR affiché par
    Djerba), révocable, débit limité ; chaque appel passe par la politique
    et l'audit avec l'identité de l'appelant.
* **Sécurité** : DLP avant tout envoi vers un moteur distant ; par moteur,
  choix de ce qui peut quitter la machine (contexte système, mémoire) ;
  sortie anormale d'un moteur distant (outil non proposé, arguments
  invalides, indices d'injection) → session « tainted ».

### Critères de réussite

- [x] Plusieurs moteurs configurés, bascule à chaud sans perte de conversation,
      repli distant → local conservé (`test_hot_switch_keeps_conversation_and_falls_back_to_local`).
- [x] Anthropic et Jarvis (contrat) fonctionnels contre des serveurs factices.
- [x] Repli JSON contraint : un modèle sans appels d'outils exécute les mêmes
      actions.
- [x] Suite de parité : mêmes scénarios rejoués sur chaque moteur (local,
      Ollama sans outils, Jarvis, Claude), mêmes actions et résultats.
- [x] Profil de capacité mesuré et affiché ; plafond appliqué par la politique.
- [x] Appairage Jarvis par code ; serveur MCP authentifié, révocable, sous
      politique et audit.
- [x] DLP et « tainted » pour les moteurs distants.
- [x] Lints et 129 tests verts en local ; contrôles ISO (modules, unité MCP)
      et session live (registre, MCP inactif par défaut) ajoutés.
- [x] CI verte (x86_64 et arm64) : run 40682259564 (63c5041), 138 tests, 50/50 contrôles en session live.

### Décisions

* **Registre à côté de `llm.*`** plutôt que migration destructive : `local`,
  `tailscale`, `cloud` restent des moteurs intégrés dérivés de la
  configuration existante ; `llm.engine` choisit le moteur actif (vide →
  `llm.provider`). Aucune configuration existante n'est réécrite.
* **Relais root pour tout moteur à secret** (Jarvis, Anthropic, API en
  ligne) : la session n'a que la vue publique ; un compromis de la session ne
  révèle aucun jeton.
* **Jarvis privé uniquement** : adresse Tailscale, réseau local ou boucle
  locale, refus sinon. Contrat minimal compatible OpenAI (le plus répandu),
  documenté dans `docs/JARVIS-CONTRAT.md` ; seul l'adaptateur changera.
* **MCP minimal sans flux** (HTTP + JSON-RPC, `initialize`, `tools/list`,
  `tools/call`) : pas de dépendance hors APT ; serveur inactif par défaut,
  lié à l'adresse Tailscale seulement ; jetons hachés, 5 essais
  d'appairage/min, 60 appels/min, plafond 2 par appelant.
* **Anomalie = tainted, pas refus** : une réponse distante anormale ne coupe
  pas la conversation mais plafonne la tâche au niveau 1, audité comme
  « moteur distant ».
* **Profil mesuré → plafond** : un moteur « Limité » ne peut pas dépasser le
  niveau 1 quel que soit le mode ; le profil est remesurable
  (`djerba-ai engines measure ID`).

### Limites restantes

* L'API réelle de Jarvis est inconnue : adaptateur et tests reposent sur le
  contrat v1 (questions ouvertes listées en fin de `docs/JARVIS-CONTRAT.md`).
* Parité limitée à l'agent (outils, politique, transactions) : parité des
  extensions, applications et installations rapportée à la phase 5 (Studio).
* Pas d'embeddings pour les moteurs relayés (la recherche sémantique reste
  sur Ollama local).
* Petites fenêtres de contexte : filtrage des outils existant, résumé de
  l'historique à généraliser en phase 4 (historique par IA).
* Interface graphique de gestion des moteurs : CLI et réglages seulement ;
  page dédiée en phase 8.

## Phase 4 — Historique par IA, mémoire, continuité

### Conception

* **Schéma versionné** de la mémoire (`core/memory.py`, `PRAGMA
  user_version`) : liste ordonnée de migrations, chacune dans une
  transaction, **sauvegarde automatique** de la base avant toute migration
  (`~/.local/share/djerba/backups/`). Version 3 :
  * `conversations` : `key` (identifiant unique, sert de session au journal
    de transactions), `engine` (moteur d'origine), `pinned`, `summary`
    (chiffré) et `summary_upto`, `source` (import) ;
  * `messages.engine` (moteur qui a produit chaque réponse) ;
  * `facts.engine`, `lessons.engine` (`""` = mémoire partagée) ;
  * `tool_runs.conversation`, `tool_runs.engine`, `tool_runs.transaction` ;
  * table `engine_memory` : par moteur, mode `isole` / `partage` et
    rétention (jours, 0 = illimitée).
* **Historique par moteur** : liste filtrable par moteur, recherche plein
  texte (déchiffrement en mémoire, insensible à la casse et aux accents) et
  sémantique (embeddings Ollama locaux), épinglage, renommage, export
  Markdown/JSON (messages et actions), suppression. Chiffrement AES-256-GCM
  conservé (titres, messages, résumés).
* **Continuité** : la conversation suit le changement de moteur ;
  l'historique transmis est adapté à la fenêtre du nouveau moteur — derniers
  échanges intégraux, plus anciens condensés en un résumé extractif
  déterministe (mis en cache, chiffré).
* **Mémoire isolée / partagée par moteur** : en mode `partage`, le moteur lit
  et écrit la mémoire commune ; en mode `isole`, seulement la sienne. Par
  défaut : Ollama local partagé, tout autre moteur isolé. L'envoi vers un
  moteur distant reste soumis aux règles `share` et au DLP (phase 3).
* **Traçabilité et annulation depuis l'historique** : chaque action est
  liée à la conversation (`key`) et au moteur ; méthode root
  `tx.undo_session {session}` (polkit `org.djerba.ai.undo`, transactions du
  seul appelant), outil `history_undo`, bouton dans l'interface.
* **Rétention** par moteur (purge des conversations non épinglées),
  **sauvegarde / restauration** de l'historique (copie SQLite cohérente,
  contrôle d'intégrité et de clé avant restauration, sauvegarde de l'état
  courant d'abord).
* **Import Jarvis** : format d'export défini dans le contrat
  (`docs/JARVIS-CONTRAT.md` §4, conversations JSON) ; import depuis un
  fichier ou par le relais root (`GET /v1/history`, jeton jamais exposé) ;
  dédoublonnage par identifiant d'origine.
* **Accès** : CLI `djerba-ai history …`, outils de l'agent, page Historique
  de l'interface (liste par moteur, recherche, épingler, renommer, exporter,
  supprimer, reprendre avec un autre moteur, annuler les actions).

### Critères de réussite

- [x] Migrations v1 (clair) → v2 → v3 testées : aucune perte, sauvegarde
      préalable créée (déjà chiffrée).
- [ ] Base plus récente refusée — **case cochée à tort en phase 4** : le code
      existait, aucun test ne le vérifiait (relevé par l'audit ; test ajouté
      en phase 4 bis, voir ci-dessous).
- [x] Historique filtré par moteur ; recherche plein texte et sémantique ;
      épinglage, renommage, export Markdown/JSON, suppression.
- [x] Reprise d'une conversation avec un autre moteur : résumé adapté à sa
      fenêtre (demande d'origine et actions conservées), derniers échanges
      intégraux.
- [x] Mémoire isolée / partagée par moteur respectée (faits, leçons, outils
      de mémoire attribués au moteur actif).
- [x] Actions liées à la conversation et au moteur ; annulation depuis
      l'historique (transactions root du seul appelant).
- [x] Rétention par moteur, sauvegarde et restauration vérifiées (clé
      étrangère et base corrompue refusées, état courant sauvegardé).
- [x] Import Jarvis (fichier et relais root) contre le serveur factice,
      dédoublonné.
- [x] Lints et 138 tests verts en local ; contrôle de session live ajouté.
- [x] CI verte (x86_64 et arm64) : run 40682259564 (63c5041), 138 tests, 50/50 contrôles en session live.

### Décisions

* **Pas de FTS5** : un index plein texte SQLite stockerait le texte en clair ;
  la recherche déchiffre en mémoire (volume personnel : quelques milliers de
  messages), la recherche sémantique réutilise l'index chiffré existant.
* **Résumé extractif déterministe** plutôt que résumé par le modèle : pas
  d'appel réseau supplémentaire, testable, rien n'est envoyé à un moteur
  distant pour être résumé ; la demande d'origine et les actions sont
  toujours conservées.
* **Clé de conversation** (UUID) comme session du journal de transactions :
  un identifiant numérique supprimé peut être réutilisé par SQLite ; l'état
  de session de l'agent est aussi lié à cette clé (correction d'une fuite
  d'état possible entre conversations).
* **Annulation par conversation limitée à l'appelant** : `tx.undo_session`
  filtre sur l'uid ; aucune conversation n'engage un autre utilisateur.
* **Origine propagée aux méthodes root appelées par un outil** (variable de
  contexte `ipc.ORIGIN`) : un réglage changé par l'agent appartient à sa
  conversation et s'annule depuis l'historique comme une action root.
* **Mémoire isolée par défaut pour tout moteur autre qu'Ollama local** : ce
  qu'un moteur distant apprend ne se mélange pas à la mémoire commune sans
  choix explicite.

### Limites restantes

* Recherche plein texte linéaire (suffisante jusqu'à quelques dizaines de
  milliers de messages).
* Import Jarvis selon le contrat v1 ; format réel inconnu.
* Interface de l'historique fonctionnelle mais sobre (onglet du Studio) ;
  refonte en phase 8.

## Phase 4 bis — Durcissement avant la phase 5 (PR relue)

Issue de l'audit d'avant phase 5. Référence : `docs/INVARIANTS.md` (33
invariants, un test réel chacun dans `tests/test-invariants.py`, sans saut ;
cliquet `tests/invariants-verts.txt` en CI).

### Conception

1. **Phase 0** : `.github/CODEOWNERS` (socle de confiance et gardes) ;
   `tests/check-guards.py` (garde des gardes : test supprimé, saut ajouté,
   politique assouplie, interdit ou clé protégée retirés, invariant vert
   retiré → échec sauf dérogation relue) ; réglages protégés (`policy.*`,
   `llm.*` sauf le modèle local, `mcp.*`, `security.*`, `sandbox.*`,
   `telemetry.*`, activation des greffons…) réservés à l'humain authentifié
   (polkit administrateur, refus de toute origine d'agent) ; `engine_use`,
   `brain_switch`, `memory_mode` retirés de l'agent ; une capacité générée
   ne s'active plus seule.
2. **Provenance et identité établies par root** (`core/provenance.py`) :
   clé de conversation délivrée par le démon et liée à l'uid ; moteur pris
   dans la configuration système ; présomption « non fiable » (session
   inconnue, absence d'origine, toute lecture hors `[provenance] fiables`) ;
   drapeau persistant par conversation, effacé seulement par un humain
   authentifié ; import (relais ou fichier) toujours non fiable ; plus de
   nouvelle destination réseau en session non fiable.
3. **Plafonds** : 3 pour Ollama local, 1 par défaut pour tout moteur distant
   (relevé seulement par `policy.set_caps`, administrateur), 1 au plus pour
   un appelant MCP.
4. **MCP** : requêtes signées (HMAC), horodatage ±60 s, nonce à usage
   unique, jeton de 7 jours, rotation, révocation, débit testé, écoute
   limitée à 100.64.0.0/10 vérifiée.
5. **Purge réelle** d'une conversation (WAL, VACUUM, sauvegardes, actions,
   session, coffre DLP, index sémantique).
6. **Tests négatifs** manquants de la phase 4.
7. **Revue des entrées ajoutées à `policy.toml`** en phases 3 et 4.
8. **Test QEMU réel** du rollback au démarrage.

### Entrées de `policy.toml` ajoutées en phases 3 et 4

L'audit annonçait 10 entrées : il y en a **9** (la dixième ligne du diff était
un commentaire).

| Entrée | Niveau avant | Niveau après | Attendu | Raison |
|---|---|---|---|---|
| `engine_save` | 1 | 2 | 2, humain seulement | Choisit où partent les conversations ; méthode réservée à l'humain authentifié (refus de toute origine d'agent). |
| `engine_remove` | 1 | 2 | 2, humain seulement | Même raison. |
| `engine_profile` | 1 | 1 | 1, humain seulement | Le profil mesuré ne peut qu'abaisser le plafond. |
| `jarvis_pair` | 1 | 2 | 2, humain seulement | Crée un accès vers un service distant. |
| `engine_use` | 1 | retiré | hors agent | Changer de moteur est réservé à l'humain (invariant 01) ; outil retiré de l'agent. |
| `history_export` | 1 | 1 | 1 | Écrit un fichier dans `~/Documents/Djerba` : local, réversible. |
| `history_undo` | 1 | 2 | 2 | Défaire des actions peut annuler un correctif de sécurité ; jamais depuis une session non fiable. |
| `memory_mode` | 1 | retiré | hors agent | L'isolation de la mémoire entre moteurs est un choix humain. |
| `history_import` | 1 | 1 | 1 | Ajoute des conversations, toujours marquées non fiables. |

Même raisonnement appliqué à `transaction_undo` (1 → 2). Retraits
consignés dans `tests/garde/derogations.txt`.

### Critères de réussite

- [x] Invariants : document, 33 tests réels, cliquet en CI (12 verts / 33 à
      ce stade : 01, 06, 09, 10, 11, 15, 19, 26, 28, 30, 31, 32).
- [x] Phase 0 : CODEOWNERS, garde des gardes, réglages protégés,
      `llm.cloud.endpoint` refusé à `settings_set`, `engine_use` hors agent.
- [x] Provenance tenue par root, drapeau persistant ; test : il survit au
      changement de moteur, à la reprise et à l'import de Jarvis.
- [x] Plafond 1 par défaut des moteurs distants et de MCP.
- [x] MCP : expiration, nonce, rotation, débit, plage Tailscale (tests).
- [x] Purge réelle (test sur les octets de la base, du WAL, des sauvegardes).
- [x] Tests négatifs : base et sauvegarde plus récentes, imports malformés
      ou trop gros, identifiant réutilisé.
- [x] Tableau des entrées de `policy.toml`.
- [ ] Test QEMU réel du rollback au démarrage.
- [ ] CI verte (x86_64 et arm64) et PR relue.

