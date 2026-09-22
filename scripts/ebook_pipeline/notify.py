"""Windows notifications for the eBook pipeline - no extra pip dependency,
just shells out to PowerShell (already on every Windows box).

Uses a WPF MessageBox rather than a toast/balloon: tried both
System.Windows.Forms.NotifyIcon balloon tips and the WinRT
ToastNotificationManager first, but neither actually rendered on this
machine (balloon tips need a running message loop this headless script
doesn't have; toast needs a registered AppUserModelID). MessageBox.Show
was confirmed visible on the first try and needs no registration - the
tradeoff is it's a modal dialog (blocks until clicked) rather than a
passive toast, which is fine here since PowerShell runs detached from the
pipeline script via subprocess.Popen.

Best-effort only: if this machine has no desktop session (e.g. running under
a service account with no interactive login) the notification silently no-ops
rather than failing the pipeline run over a cosmetic feature.
"""

from __future__ import annotations

import base64
import subprocess

PS_TEMPLATE = """
Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show('{message}', '{title}', 'OK', 'Information') | Out-Null
"""


def notify(title: str, message: str) -> None:
    def esc(text: str) -> str:
        return text.replace("'", "''")

    script = PS_TEMPLATE.format(title=esc(title), message=esc(message))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        # Popen (not run): the dialog blocks until clicked, so this must be
        # detached/non-blocking or the pipeline run would hang waiting on it.
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass
