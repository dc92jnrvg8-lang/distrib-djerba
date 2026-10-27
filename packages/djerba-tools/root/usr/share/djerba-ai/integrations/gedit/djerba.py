# SPDX-License-Identifier: GPL-3.0-or-later
"""Djerba OS — greffon Gedit : actions de l'agent sur la sélection (ou le document).

Raccourcis : Ctrl+Alt+E expliquer, Ctrl+Alt+K corriger, Ctrl+Alt+T écrire des
tests. Le texte est transmis à « djerba-ai --ask-selection » sur l'entrée
standard (jamais en argument de commande).
"""

import subprocess

from gi.repository import Gedit, Gio, GObject

ACTIONS = (
    ("djerba-explain", "<Primary><Alt>e", "Explique ce code ou ce texte"),
    ("djerba-fix", "<Primary><Alt>k", "Corrige l'orthographe et la grammaire de ce texte"),
    ("djerba-tests", "<Primary><Alt>t", "Écris des tests unitaires pour ce code"),
)


class DjerbaAppActivatable(GObject.Object, Gedit.AppActivatable):
    app = GObject.Property(type=Gedit.App)

    def do_activate(self):
        for name, accelerator, _instruction in ACTIONS:
            self.app.set_accels_for_action(f"win.{name}", [accelerator])

    def do_deactivate(self):
        for name, _accelerator, _instruction in ACTIONS:
            self.app.set_accels_for_action(f"win.{name}", [])


class DjerbaWindowActivatable(GObject.Object, Gedit.WindowActivatable):
    window = GObject.Property(type=Gedit.Window)

    def do_activate(self):
        for name, _accelerator, instruction in ACTIONS:
            action = Gio.SimpleAction(name=name)
            action.connect("activate", self._ask, instruction)
            self.window.add_action(action)

    def do_deactivate(self):
        for name, _accelerator, _instruction in ACTIONS:
            self.window.remove_action(name)

    def _ask(self, _action, _parameter, instruction):
        document = self.window.get_active_document()
        if document is None:
            return
        if document.get_has_selection():
            start, end = document.get_selection_bounds()
        else:
            start, end = document.get_bounds()
        text = document.get_text(start, end, False)
        process = subprocess.Popen(["djerba-ai", "--ask-selection", instruction, document.get_short_name_for_display()],
                                   stdin=subprocess.PIPE, start_new_session=True)
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()
