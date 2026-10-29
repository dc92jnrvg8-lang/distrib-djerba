# Invariants de sécurité de Djerba OS (33)

Chaque invariant a **un test** `test_invariant_NN_*` dans
`tests/test-invariants.py`. Un test vérifie le comportement réel (démon root,
politique, agent, fichiers livrés) ; il **échoue tant que l'invariant n'est
pas implémenté** : aucun `skip`, aucun `expectedFailure`.

La CI applique un **cliquet** (`tests/invariants-verts.txt`, protégé par
`CODEOWNERS`) :

* un invariant listé comme vert qui échoue fait échouer la CI (régression) ;
* un invariant qui passe sans être listé fait aussi échouer la CI : la liste
  doit être mise à jour, dans une PR relue ;
* les autres invariants sont rouges et annoncés comme tels dans le rapport.

| N° | Invariant |
|---|---|
| 01 | Politique intouchable : aucune clé de politique/sécurité modifiable par outil, moteur, plugin ou session ; humain authentifié (polkit) seulement. |
| 02 | Niveau calculé par l'effet réel (chemins résolus, arguments), pas par le nom de l'outil. |
| 03 | Budget cumulé : suite d'actions de niveau 1 requalifiée en niveau 2. |
| 04 | Installer = exécuter du code root : dépôt/clé APT, Flatpak, venv, ollama, .deb local, patch, unité, règle polkit = niveau 2 minimum. |
| 05 | Aucune écriture root ne suit un lien symbolique (openat2 RESOLVE_BENEATH\|NO_SYMLINKS, descripteurs, anti-TOCTOU). |
| 06 | Provenance, pas détection : contenu non fiable dans le contexte ⇒ session tainted (niveau max 1, pas de sortie réseau nouvelle), drapeau tenu et appliqué côté root. |
| 07 | TCB immuable pour l'agent (`tcb.toml`) : sysd, politique, trust, transactions, audit, snapshots, unités root, polkit, bootloader, clés. |
| 08 | Chemins absolus et PATH fixé pour tout ce qui s'exécute en root. |
| 09 | Appelants distants (Jarvis, MCP) plafonnés niveau 0-1 ; tainted dès qu'ils lisent du contenu externe. |
| 10 | Identité authentifiée (SO_PEERCRED, jeton) : moteur, session, appelant, auteur ne sont jamais auto-déclarés. |
| 11 | gui.sock, D-Bus, menus contextuels, raccourcis, presse-papiers : contenu non fiable, aucun droit élevé. |
| 12 | Clé de chiffrement hors de portée du même UID (trousseau/TPM) ; clés d'API root seulement. |
| 13 | Le journal ne s'annule pas (transactions, audit, snapshots, politique exclus de toute restauration). |
| 14 | Budget cloud et débit par appelant côté root ; pas de repli silencieux vers un autre fournisseur payant. |
| 15 | L'IA ne valide pas ses tests : suppression/skip/assouplissement détecté en CI, seuils de politique protégés. |
| 16 | Interrupteur et mode sûr : pause, `djerba.agent=off`, entrée GRUB de récupération ; l'OS fonctionne sans l'agent. |
| 17 | Canal d'authentification de confiance : pas de fausse fenêtre de mot de passe (Studio, plugins). |
| 18 | Contexte D-Bus non spoofable ; titres de fenêtres non fiables. |
| 19 | Bac à sable sans évasion (ni réseau, ni `sysd.sock`, ni `$HOME`, ni D-Bus session) ; tests d'évasion. |
| 20 | Ressources bornées (cgroup slice, quotas disque, anti-boucle). |
| 21 | Modèles et artefacts : `model_pull` niveau 2, jamais depuis une session tainted ; Ollama épinglé (version, SHA-256). |
| 22 | Aucun secret dans traces, audit, historique, télémétrie, rapports, requêtes web (tests canaris). |
| 23 | Canaris : jetons-pièges ; lecture/émission hors besoin ⇒ alerte et gel. |
| 24 | Réseau sortant de l'agent en liste blanche par moteur et fonction. |
| 25 | Web sans SSRF (proxy local : pas de loopback, RFC1918, link-local, métadonnées, rebinding). |
| 26 | Anti-rejeu : jetons avec expiration, nonce, révocation, rotation. |
| 27 | Mises à jour signées et atomiques avec rollback ; TCB mise à jour seulement par paquet signé. |
| 28 | Session live ≠ système installé (pas de sudo/polkit/autologin live après installation). |
| 29 | Notifications : actions vérifiées par identifiant de transaction. |
| 30 | Aucune télémétrie sortante par défaut. |
| 31 | Fail-closed : erreur de politique/identité/journal/snapshot ⇒ action modifiante refusée. |
| 32 | Pas d'injection de commande : argv, schémas stricts, pas de `shell=True`. |
| 33 | Chiffrement authentifié, rotation de clé, effacement cryptographique (« oublier vraiment », snapshots inclus). |

L'état courant (vert / rouge) est produit par
`python3 tests/test-invariants.py --rapport` et publié dans le journal de la
CI ; la liste des verts fait foi (`tests/invariants-verts.txt`).
