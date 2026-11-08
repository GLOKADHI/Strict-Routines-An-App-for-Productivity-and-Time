"""One lightweight native countdown host inside the existing desktop process."""
import logging
import sys
import threading

logger = logging.getLogger('ExtensionCountdown')


def notification_text(model):
    remaining = max(0, int(model.get('remaining_grace_sec', 0)))
    overdue = max(0, int(model.get('overdue_sec', 0)))
    total_penalty = int(model.get('total_penalty', 0))
    if remaining > 0:
        time_label = f'Grace remaining: {remaining // 60:02d}:{remaining % 60:02d}'
        rule_label = 'Potential penalty: −1 point per complete verified minute after grace'
    else:
        overdue_str = f"-{overdue // 60:02d}:{overdue % 60:02d}"
        time_label = f'Overdue: {overdue_str}'
        if total_penalty > 0:
            rule_label = f'Cumulative penalty: {total_penalty} point(s) deducted'
        else:
            rule_label = 'Grace period ended; −1 point per complete verified minute'

    lines = [model['profile'],
             time_label,
             'Penalty begins at: ' + model['deadline_label'],
             rule_label]
    if model.get('sync_status') == 'PENALTY SYNC PENDING':
        lines.append(f'PENALTY SYNC PENDING · {model.get("pending_penalty", 0)} point(s) awaiting authority')
    elif not model.get('penalty_policy_enabled', True):
        lines.append('Penalty rule is not enabled; no new deduction requests.')
    return '\n'.join(lines)


class ExtensionCountdownNotifications:
    """All profiles share one UI thread; no new Windows process or polling worker."""
    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}
        self._thread = None
        self._stop = threading.Event()

    def update(self, key, snapshot, action):
        if sys.platform != 'win32':
            return False
        with self._lock:
            self._entries[key] = (snapshot, action)
            self._stop.clear()
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='ExtensionCountdownUI', daemon=True)
                self._thread.start()
        return True

    def remove(self, key):
        with self._lock:
            self._entries.pop(key, None)

    def stop(self):
        self._stop.set()
        with self._lock:
            self._entries.clear()

    def _run(self):
        try:
            import clr
            clr.AddReference('System.Windows.Forms')
            clr.AddReference('System.Drawing')
            import System.Windows.Forms as WF
            import System.Drawing as SD
            display_countdown(WF, SD, self)
        except Exception:
            logger.exception('Native extension countdown could not be displayed')
        finally:
            with self._lock:
                self._thread = None
                # A new episode may have arrived while the previous form closed.
                if self._entries and not self._stop.is_set():
                    self._entries.clear()  # The next monitor check can retry; never spin on UI failure.


def display_countdown(WF, SD, host, probe=None):
    """WinForms controls are created, updated and disposed on their owning thread."""
    form = WF.Form()
    form.Text = 'StrictRoutines — Extension countdown'
    form.FormBorderStyle = WF.FormBorderStyle.FixedToolWindow
    form.ControlBox = False
    form.ShowInTaskbar = False
    form.TopMost = True
    form.StartPosition = WF.FormStartPosition.Manual
    form.BackColor = SD.ColorTranslator.FromHtml('#0E151E')
    fonts = [SD.Font('Segoe UI', 14, SD.FontStyle.Bold), SD.Font('Segoe UI', 10)]
    form.ClientSize = SD.Size(530, 270)
    screen = WF.Screen.PrimaryScreen.WorkingArea
    form.Location = SD.Point(max(screen.Left, screen.Right-form.Width-18),
                             max(screen.Top, screen.Bottom-form.Height-18))
    title = WF.Label()
    title.Text = 'StrictRoutines extension disabled'
    title.ForeColor = SD.ColorTranslator.FromHtml('#ECF3FA')
    title.Font = fonts[0]
    title.SetBounds(18, 16, 460, 28)
    title.UseMnemonic = False
    body = WF.Label()
    body.ForeColor = SD.ColorTranslator.FromHtml('#B7C7D8')
    body.Font = fonts[1]
    body.SetBounds(18, 54, 494, 155)
    body.UseMnemonic = False
    action = WF.Button()
    action.SetBounds(18, 220, 494, 34)
    action.Text = 'Open browser extensions'
    action.FlatStyle = WF.FlatStyle.Flat
    action.BackColor = SD.ColorTranslator.FromHtml('#00D99C')
    action.ForeColor = SD.ColorTranslator.FromHtml('#062D22')
    hide = WF.Button()
    hide.Text = '×'
    hide.AccessibleName = 'Hide countdown; monitoring continues'
    hide.SetBounds(487, 12, 26, 28)
    for control in (title, body, action, hide):
        form.Controls.Add(control)
    current = [None, None]
    hidden = [None]
    actions = [False]

    def hide_form(sender, event):
        hidden[0] = current[0]
        form.Hide()

    def open_manager(sender, event):
        callback = current[1]
        if not callback or actions[0]:
            return
        actions[0] = True
        def invoke():
            try:
                callback()
            except Exception:
                logger.exception('Could not open the enrolled browser extension manager')
            finally:
                actions[0] = False
        threading.Thread(target=invoke, name='ExtensionRestoreAction', daemon=True).start()

    hide.Click += hide_form
    action.Click += open_manager
    timer = WF.Timer()
    timer.Interval = 1000

    def tick(sender=None, event=None):
        with host._lock:
            entries = list(host._entries.items())
        if host._stop.is_set() or not entries:
            timer.Stop()
            form.Close()
            return
        key, (snapshot, callback) = entries[-1]
        model = snapshot()
        if model is None:
            host.remove(key)
            return
        current[:] = [key, callback]
        text = notification_text(model)
        if len(entries) > 1:
            text += f'\n{len(entries)} affected profiles · all timers in Strict Session'
        if body.Text != text:
            body.Text = text
        action.Text = 'Open ' + model['browser_name'] + ' extensions'
        if hidden[0] != key and not form.Visible:
            try:
                import ctypes
                handle = form.Handle.ToInt64()
                style = ctypes.windll.user32.GetWindowLongW(handle, -20)
                ctypes.windll.user32.SetWindowLongW(handle, -20, style | 0x08000000 | 0x00000080)
            except (AttributeError, OSError):
                pass
            form.Show()
        if probe:
            probe(form, model)

    timer.Tick += tick
    try:
        tick()
        if host._entries:
            timer.Start()
            WF.Application.Run(form)
    finally:
        timer.Stop()
        timer.Dispose()
        form.Dispose()
        for font in fonts:
            font.Dispose()
