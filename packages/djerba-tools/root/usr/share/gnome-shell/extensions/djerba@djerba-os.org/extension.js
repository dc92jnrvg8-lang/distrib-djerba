// SPDX-License-Identifier: GPL-3.0-or-later
// Djerba OS — extension GNOME Shell (46+) de l'agent Djerba.
//
// * À chaque changement de focus (Mutter), la fenêtre active est transmise au
//   bus de contexte de l'agent : appel D-Bus asynchrone
//   org.djerba.ai.Context.ActiveWindow(titre, classe, pid) sur le bus de session
//   (sans effet si le démon de l'agent ne tourne pas).
// * Un bouton de la barre supérieure ouvre l'anneau d'action (djerba-ai --hud).

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

const BUS_NAME = 'org.djerba.ai.Context';

export default class DjerbaExtension extends Extension {
    // Toute erreur est contenue : l'extension passe en mode dégradé (journalisé), jamais
    // GNOME Shell ni Mutter. Les erreurs « Djerba » du journal sont reprises dans
    // /var/log/djerba/gnome-crash.log par le rapport de session de l'agent.
    enable() {
        try {
            this._focusId = global.display.connect('notify::focus-window', () => this._report());
        } catch (error) {
            this._focusId = null;
            console.error(`Djerba : fenêtre active indisponible (mode dégradé) : ${error}`);
        }
        try {
            this._button = new PanelMenu.Button(0.0, 'Djerba', true);
            this._button.add_child(new St.Icon({icon_name: 'djerba-ai', style_class: 'system-status-icon',
                                                fallback_icon_name: 'system-search-symbolic'}));
            this._button.connect('button-press-event', () => {
                try {
                    Gio.Subprocess.new(['djerba-ai', '--hud'], Gio.SubprocessFlags.NONE);
                } catch (error) {
                    console.error(`Djerba : ${error.message}`);
                }
            });
            Main.panel.addToStatusArea(this.uuid, this._button);
        } catch (error) {
            this._button?.destroy();
            this._button = null;
            console.error(`Djerba : bouton de la barre supérieure indisponible (mode dégradé) : ${error}`);
        }
        this._report();
    }

    disable() {
        try {
            if (this._focusId)
                global.display.disconnect(this._focusId);
        } catch (error) {
            console.error(`Djerba : ${error}`);
        }
        this._focusId = null;
        this._button?.destroy();
        this._button = null;
    }

    _report() {
        try {
            const window = global.display.focus_window;
            const parameters = new GLib.Variant('(ssi)', [
                window ? window.get_title() ?? '' : '',
                window ? window.get_wm_class() ?? '' : '',
                window ? window.get_pid() : 0,
            ]);
            Gio.DBus.session.call(BUS_NAME, '/Context', BUS_NAME, 'ActiveWindow', parameters, null,
                Gio.DBusCallFlags.NO_AUTO_START, 1000, null, (connection, result) => {
                    try {
                        connection.call_finish(result);
                    } catch {
                        // démon de l'agent absent : rien à signaler
                    }
                });
        } catch (error) {
            console.error(`Djerba : fenêtre active non transmise : ${error}`);
        }
    }
}
