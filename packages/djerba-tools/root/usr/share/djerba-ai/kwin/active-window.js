// SPDX-License-Identifier: GPL-3.0-or-later
// Djerba OS — script KWin (Plasma 6) : signale la fenêtre active au bus de
// contexte de l'agent Djerba (service D-Bus de session org.djerba.ai.Context).
function report(window) {
    callDBus("org.djerba.ai.Context", "/Context", "org.djerba.ai.Context", "ActiveWindow",
             window ? String(window.caption) : "",
             window ? String(window.resourceClass) : "",
             window ? window.pid : 0);
}
workspace.windowActivated.connect(report);
report(workspace.activeWindow);
