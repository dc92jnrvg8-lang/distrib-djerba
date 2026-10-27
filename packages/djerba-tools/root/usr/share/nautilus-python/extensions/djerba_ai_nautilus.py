# SPDX-License-Identifier: GPL-3.0-or-later
"""Djerba OS — menu contextuel « Djerba » de Nautilus (python3-nautilus, API 4).

Chaque action transmet les fichiers choisis à l'agent Djerba
(« djerba-ai --ask-file ACTION CHEMIN… ») : même agent, mêmes contrôles et
niveaux de risque que dans l'anneau d'action.
"""

import subprocess

from gi.repository import GObject, Nautilus

ACTIONS = (
    ("expliquer", "Expliquer avec Djerba", "Explique ce que contiennent ou font ces éléments"),
    ("ranger", "Proposer un rangement", "Propose un rangement de ces éléments (ne déplace rien sans mon accord)"),
    ("securite", "Vérifier la sécurité (antivirus)", "Vérifie ces fichiers avec l'antivirus et dis-moi s'ils sont sûrs"),
    ("resumer", "Résumer le document", "Résume ces documents (lis-les avec os_file_read)"),
)


class DjerbaMenu(GObject.GObject, Nautilus.MenuProvider):
    def _launch(self, _item, instruction, files):
        paths = [item.get_location().get_path() for item in files if item.get_location().get_path()]
        if paths:
            subprocess.Popen(["djerba-ai", "--ask-file", instruction, *paths], start_new_session=True)

    def get_file_items(self, files):
        if not files:
            return []
        root = Nautilus.MenuItem(name="DjerbaMenu::root", label="Djerba", icon="djerba-ai")
        submenu = Nautilus.Menu()
        root.set_submenu(submenu)
        for identifier, label, instruction in ACTIONS:
            item = Nautilus.MenuItem(name=f"DjerbaMenu::{identifier}", label=label)
            item.connect("activate", self._launch, instruction, files)
            submenu.append_item(item)
        return [root]

    def get_background_items(self, folder):
        item = Nautilus.MenuItem(name="DjerbaMenu::dossier", label="Djerba : ranger ce dossier", icon="djerba-ai")
        item.connect("activate", self._launch, "Propose un rangement de ce dossier (ne déplace rien sans mon accord)",
                     [folder])
        return [item]
