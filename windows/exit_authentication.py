"""Authentication for explicit process exit; window hiding never invokes it."""

import hashlib
import hmac


def verify_exit_key(value) -> bool:
    # Keep the user's exit key out of logs, settings responses and prompt defaults.
    expected = bytes.fromhex("03ac674216f3e15c761ee1a5e255f067953623c8b388b4459e13f978d7c846f4")
    return isinstance(value, str) and hmac.compare_digest(hashlib.sha256(value.encode("utf-8")).digest(), expected)


def prompt_exit_key(owner=None):
    """Shown only after a local explicit Exit action, on the WinForms UI thread."""
    import System.Windows.Forms as forms
    from System.Drawing import Size, Point, ColorTranslator
    from System import Action

    result = [False]

    def show():
        def control(kind, **properties):
            item = kind()
            for name, value in properties.items(): setattr(item, name, value)
            return item
        dialog = forms.Form()
        dialog.Text = "Exit StrictRoutines"
        dialog.ClientSize = Size(360, 224)
        dialog.FormBorderStyle = forms.FormBorderStyle.FixedDialog
        dialog.StartPosition = forms.FormStartPosition.CenterScreen
        dialog.MinimizeBox = False
        dialog.MaximizeBox = False
        dialog.BackColor = ColorTranslator.FromHtml("#101419")
        dialog.ForeColor = ColorTranslator.FromHtml("#F8FAFC")
        label = control(forms.Label, Text="Enter your PIN to start the timed 25-question exit check.", Location=Point(16, 14), AutoSize=True)
        entry = control(forms.TextBox, Location=Point(16, 40), Width=328, UseSystemPasswordChar=True)
        error = control(forms.Label, Location=Point(16, 70), Width=328, Height=24)
        confirm = control(forms.Button, Text="Continue to quiz", Location=Point(16, 104), Width=160, Height=34)
        cancel = control(forms.Button, Text="Keep running", Location=Point(192, 104), Width=152, Height=34)
        notice = control(forms.Label, Text="25 minutes · All 25 answered · At least 24 correct", Location=Point(16, 158), Width=328, Height=40)
        entry.BackColor = ColorTranslator.FromHtml("#1F2633")
        entry.ForeColor = dialog.ForeColor
        confirm.BackColor = ColorTranslator.FromHtml("#00E599")
        confirm.ForeColor = ColorTranslator.FromHtml("#070A0C")
        cancel.BackColor = ColorTranslator.FromHtml("#1F2633")
        cancel.ForeColor = dialog.ForeColor

        def submit(sender, event):
            if verify_exit_key(str(entry.Text)):
                result[0] = str(entry.Text)
                entry.Clear()
                dialog.Close()
            else:
                error.Text = "Incorrect key. StrictRoutines is still running."
                entry.Clear()
                entry.Focus()

        confirm.Click += submit
        cancel.Click += lambda sender, event: dialog.Close()
        dialog.AcceptButton = confirm
        dialog.CancelButton = cancel
        for control in (label, entry, error, confirm, cancel, notice):
            dialog.Controls.Add(control)
        try:
            dialog.ShowDialog(owner) if owner is not None else dialog.ShowDialog()
        finally:
            dialog.Dispose()

    if owner is not None and owner.InvokeRequired:
        owner.Invoke(Action(show))
    else:
        show()
    return result[0]
