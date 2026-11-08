"""Post WebView2 COM work to its owning WinForms thread without waiting for it."""
import logging
import os
from urllib.parse import urlsplit

logger = logging.getLogger("StrictRecords.WebViewLifecycle")
# Python.NET can return a fresh wrapper for the same managed CoreWebView2.
# Keep the desktop core identities so restoring the window cannot add dialogs.
_permission_cores = []


def _ensure_webview2_assembly():
    try:
        import clr
        try:
            clr.AddReference("Microsoft.Web.WebView2.Core")
        except Exception:
            try:
                import webview
                wv_dir = os.path.dirname(webview.__file__)
                dll = os.path.join(wv_dir, "lib", "Microsoft.Web.WebView2.Core.dll")
                if os.path.exists(dll):
                    clr.AddReference(dll)
            except Exception:
                pass
    except Exception:
        pass


def _local_origin(uri):
    try:
        value = urlsplit(str(uri))
        if value.scheme not in {'http', 'https'} or value.hostname not in {'127.0.0.1', 'localhost', '::1'} or value.username or value.password:
            return None
        return value.scheme, value.hostname, value.port or (443 if value.scheme == 'https' else 80)
    except ValueError:
        return None


def request_media_permission(core, args, states, prompt, schedule):
    """Defer native UI until the COM event returns, and grant only after Yes."""
    kind = str(args.PermissionKind)
    origin = _local_origin(args.Uri)
    if kind not in {'Camera', 'Microphone'} or not origin or origin != _local_origin(core.Source):
        args.State = states.Default
        args.Handled = False
        return
    args.State = states.Deny
    args.Handled = True
    # A declined test must be retryable, without a persistent profile denial.
    try:
        args.SavesInProfile = False
    except Exception:
        pass  # Older runtimes still support the explicit permission decision.
    deferral = args.GetDeferral()

    def decide():
        try:
            if origin == _local_origin(core.Source) and prompt(kind) is True:
                # Navigation may have changed while the native dialog was open.
                if origin == _local_origin(core.Source):
                    args.State = states.Allow
        except Exception:
            logger.warning('Media permission prompt failed; access remains denied', exc_info=True)
        finally:
            deferral.Complete()

    try:
        queued = schedule(decide)
    except Exception:
        queued = False
    if not queued:
        deferral.Complete()


def ensure_webview2_permissions(core, native=None, target_url=None):
    """Prompt for local camera/microphone access; never silently grant devices."""
    if core is None or getattr(core, "_sr_perm_hooked", False) is True or any(existing == core for existing in _permission_cores):
        return
    try:
        _ensure_webview2_assembly()
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState, CoreWebView2PermissionKind

        def prompt(kind):
            from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon, DialogResult
            device = 'camera' if kind == 'Camera' else 'microphone'
            message = (f'Allow StrictRecords to use this PC\'s {device} for the local prerequisite check?\n\n'
                       'The test checks whether the device is available. It does not record or upload audio or video. '
                       'Access stops when you leave preparation or turn off test consent.\n\n'
                       'Choose Yes to allow this test, or No to keep the check pending.')
            return MessageBox.Show(native, message, f'StrictRecords — {device.title()} permission',
                                   MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes

        def schedule(callback):
            from System import Action
            return post_to_ui(native, callback, Action)

        def permission_requested(sender, args):
            try:
                request_media_permission(core, args, CoreWebView2PermissionState, prompt, schedule)
            except Exception as ex:
                logger.debug("Permission grant error: %s", ex)

        core.PermissionRequested += permission_requested
        setattr(core, "_sr_perm_hooked", True)
        _permission_cores.append(core)
        # Migrate old blanket grants/denials. Only the dashboard's two media
        # permissions are reset; unrelated origins and permissions are untouched.
        uri = target_url or core.Source
        origin = _local_origin(uri)
        if origin:
            scheme, host, port = origin
            authority = f'[{host}]' if ':' in host else host
            try:
                for kind in (CoreWebView2PermissionKind.Camera, CoreWebView2PermissionKind.Microphone):
                    core.Profile.SetPermissionStateAsync(kind, f'{scheme}://{authority}:{port}', CoreWebView2PermissionState.Default)
            except Exception:
                logger.debug('Runtime could not reset cached media permissions', exc_info=True)
        logger.info("WebView2 camera/microphone consent prompt registered; other permissions use runtime prompts.")
    except Exception as ex:
        logger.warning("Could not register WebView2 permission handler: %s", ex)


def find_webview_on_ui_thread(native):
    browser = getattr(native, "browser", None)
    webview = getattr(browser, "webview", None) if browser is not None else None
    if webview is None and hasattr(native, "Controls"):
        for control in native.Controls:
            if "WebView2" in control.GetType().FullName:
                return control
    return webview


def post_to_ui(native, callback, action_factory=None):
    """Never fall back to calling COM from a worker when dispatch fails."""
    if native is None:
        return False
    try:
        native.BeginInvoke(action_factory(callback) if action_factory else callback)
        return True
    except Exception:
        logger.warning("WebView UI operation could not be queued", exc_info=True)
        return False


def post_memory_target(native, level, action_factory=None, level_resolver=None):
    if level not in ("Low", "Normal"):
        raise ValueError("Unsupported WebView memory target")

    def apply():
        try:
            webview = find_webview_on_ui_thread(native)
            core = getattr(webview, "CoreWebView2", None) if webview is not None else None
            if core is None:
                return
            if level_resolver:
                target = level_resolver(level)
            else:
                _ensure_webview2_assembly()
                import clr
                clr.AddReference("Microsoft.Web.WebView2.Core")
                from Microsoft.Web.WebView2.Core import CoreWebView2MemoryUsageTargetLevel
                target = getattr(CoreWebView2MemoryUsageTargetLevel, level)
            core.MemoryUsageTargetLevel = target
        except Exception:
            logger.debug("WebView memory target was not changed", exc_info=True)

    return post_to_ui(native, apply, action_factory)


def post_recovery_handlers(native, target_url, action_factory=None):
    def attach():
        try:
            webview = find_webview_on_ui_thread(native)
            if webview is None:
                return

            def failed(sender, args):
                logger.warning("WebView process failed (%s); reloading dashboard", getattr(args, "ProcessFailedKind", "Unknown"))
                try:
                    webview.Reload()
                except Exception:
                    from System import Uri
                    webview.Source = Uri(target_url)

            def hook(core):
                core.ProcessFailed += failed
                ensure_webview2_permissions(core, native, target_url)

            core = getattr(webview, "CoreWebView2", None)
            if core is not None:
                hook(core)
            else:
                def initialized(sender, args):
                    if getattr(args, "IsSuccess", False) and sender.CoreWebView2 is not None:
                        hook(sender.CoreWebView2)
                webview.CoreWebView2InitializationCompleted += initialized
        except Exception:
            logger.warning("WebView recovery handler could not be attached", exc_info=True)

    return post_to_ui(native, attach, action_factory)
