"""Read-only fullscreen warning policy. Never changes timer, proof or scores."""
from dataclasses import asdict, dataclass
from core.strict_participants import COMPANION_ROLES


@dataclass(frozen=True)
class DisplayWarning:
    role: str
    code: str
    message: str


def derive_warnings(preparation, native=None, *, windows_expected=False):
    """Use current server receipt freshness, not a phone's claimed clock."""
    warnings = []
    devices = (preparation or {}).get('devices', {})
    for role in COMPANION_ROLES:
        name = 'iOS'
        device = devices.get(role, {})
        if not device.get('joined'):
            continue
        report = device.get('report') or {}
        # Fullscreen, locking and sleep are explicitly allowed for mobiles.
        # Only the authenticated native interruption authority can raise an alert.
        if not device.get('session_interrupted'):
            if device.get('integrity_state')=='BACKGROUND_REASON_UNKNOWN':
                reason=device.get('integrity_reason')
                if reason=='PAGE_EXIT_OBSERVED':
                    warnings.append(DisplayWarning(role,'PAGE_EXIT_UNCONFIRMED',f'{name} timer page started leaving (navigation, reload or closure). Session preserved; cause unconfirmed.'))
                elif reason=='PAGE_HIDDEN':
                    warnings.append(DisplayWarning(role,'PAGE_HIDDEN_UNCONFIRMED',f'{name} timer is hidden (tab change, lock or suspension). Session preserved; cause unconfirmed.'))
            continue
        warnings.append(DisplayWarning(role,'APP_SWITCH_CONFIRMED',f'{name} left the study environment. Return to its Strict Session to resume.'))
        continue

    if windows_expected:
        native = native or {}
        if native.get('known') is not True:
            code, message = 'UNKNOWN', 'Windows fullscreen status cannot be verified.'
        elif native.get('visible') is not True:
            code, message = 'HIDDEN', 'The Windows focus window is hidden or minimized.'
        elif native.get('fullscreen') is not True:
            code, message = 'FULLSCREEN_EXITED', 'The Windows focus window left fullscreen.'
        elif native.get('foreground') is not True:
            code, message = 'NOT_FOREGROUND', 'The Windows focus window is no longer active.'
        elif native.get('focus_view_visible') is False:
            code, message = 'FOCUS_VIEW_HIDDEN', 'The Windows focus view is hidden behind session controls.'
        else:
            code = message = None
        if code:
            warnings.append(DisplayWarning('WINDOWS', code, message))
    return warnings


class DisplayWarningTracker:
    """Emit one notification per transition, retaining current warnings for UI."""
    def __init__(self):
        self.previous = {}

    def update(self, preparation, native=None, *, windows_expected=False):
        current = derive_warnings(preparation, native, windows_expected=windows_expected)
        by_role = {warning.role: warning for warning in current}
        entered = [warning for warning in current if self.previous.get(warning.role) != warning.code]
        restored = []
        for role in self.previous.keys() - by_role.keys():
            if role == 'WINDOWS':
                if windows_expected and native and all(native.get(key) is True for key in ('known', 'visible', 'fullscreen', 'foreground')):
                    restored.append(role)
            else:
                device = (preparation or {}).get('devices', {}).get(role, {})
                report = device.get('report') or {}
                if device.get('joined') and device.get('fresh') and report.get('visible') is True and report.get('fullscreen') is True:
                    restored.append(role)
        self.previous = {warning.role: warning.code for warning in current}
        return dict(warnings=[asdict(warning) for warning in current],
                    new_warnings=[asdict(warning) for warning in entered], restored=restored)
