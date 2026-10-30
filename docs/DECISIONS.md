# Décisions

Une entrée par PR : la décision, sa raison, ce qui a été mesuré.

## UX PR 1 — lisibilité et mise en page (`claude/ux-correctifs`)

* **Schémas de couleurs Djerba au lieu de Breeze tel quel.** Mesure de la palette Qt vue par les
  applications KDE (`plasma-integration`, Debian 13) avec le `kdeglobals` livré : 18/26 paires lisibles en
  sombre, 16/26 en clair. Défauts : texte sélectionné blanc sur bleu Breeze `#3daee9` (2,43:1), textes
  désactivés sous 3:1 (jusqu'à 1,70:1). `DjerbaSombre` / `DjerbaClair` (dérivés de Breeze 6.3.5,
  `assets_generator/derive_color_schemes.py`) : sélection turquoise foncé, effet « désactivé » moins
  agressif ; 26/26 paires. L'hypothèse de départ (palette retombée en clair faute de groupes `[Colors:*]`) était
  fausse : le thème de plateforme résout bien le schéma par son nom ; les groupes sont tout de même écrits dans
  `/etc/skel/.config/kdeglobals` (comme `plasma-apply-colorscheme`).
* **Accent = couleur de sélection** (`0,122,130`) : Plasma recolore la sélection avec l'accent ; le turquoise vif
  `#00C2CB` ne porte pas un texte blanc (2,19:1).
* **Panneaux opaques par défaut**, transparence et flou en option (`ui.translucent`) : sans compositeur (X11,
  VM), les fonds translucides prenaient la couleur du bureau.
* **Saisie jamais désactivée pendant le travail** : le style `org.kde.desktop` atténue un champ désactivé (texte
  « Djerba travaille… » quasi invisible en clair). Le message part en file d'attente ; une barre d'activité dit la
  phase (réfléchit, agit, vérifie, écrit), la durée, et porte **Arrêter**.
* **Arrêt entre deux étapes, jamais au milieu d'une action** : l'agent vérifie la demande d'arrêt avant chaque
  appel au modèle et chaque outil ; une action déjà lancée (éventuellement root) va à son terme.
* **Jetons de thème en fichiers JSON validés** (base de P10) : clés connues, `#RRGGBB`, opacités bornées, aucun
  code ; un nom de jeton `onXxx` est interdit (QML le prend pour un gestionnaire de signal : couleur restée noire,
  vu sur le bouton Arrêter).
* **Signaux Qt plutôt que `QTimer.singleShot` depuis le fil de l'agent** : un `QThread` sans boucle d'événements
  ne déclenche jamais ces minuteurs (défaut latent aussi pour les validations et l'avis de repli).
