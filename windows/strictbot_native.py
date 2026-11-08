"""Python/.NET bridge for the native 3D presentation; contains no access decisions."""
import json
from pathlib import Path
import sys
from windows.strictbot_display import select_display
from windows.strictbot_preferences import Preferences


class NativePresentation:
    def __init__(self,callback,preferences=None):
        import clr
        from System import Action,String
        root=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parents[1]))
        library=root/"assets"/"StrictBotNative.dll"
        if not library.is_file():raise RuntimeError("The native StrictBot 3D library is missing. Rebuild the Windows package.")
        clr.AddReference(str(library))
        from StrictBotNative import NativeHost
        self.store=preferences or Preferences()
        self._last_data={}
        self._callback=Action[String](lambda command:callback(json.loads(str(command))))
        self.host=NativeHost(self._callback)
        self.host.Start()
        self.configure()

    def context(self,data=None):
        data=self._last_data if data is None else data
        prefs=self.store.load()
        display=select_display(data.get("hwnd",0),prefs["monitor"],data.get("display_handle"))
        return dict(preferences=prefs,work=list(display.work),dpi=display.dpi)

    def configure(self):self.host.Configure(json.dumps(self.context()))

    def show(self,session,data):
        self._last_data=dict(data)
        payload=dict(data,session=session,**self.context(data))
        self.host.Present(json.dumps(payload))

    def settings(self):self.configure();self.host.ShowSettings()
    def preview(self,preferences):
        display=select_display(0,preferences['monitor'])
        payload=dict(session=-1,preview=True,robot_preview=True,target_app='StrictBot Preview',
                     preferences=preferences,work=list(display.work),dpi=display.dpi)
        self.host.Present(json.dumps(payload))
    def close(self):self.host.Hide()
    def coordinate(self, state):self.host.CoordinateOverlays(json.dumps(state))
    def stop(self):self.host.Shutdown()
    def metrics(self):return json.loads(str(self.host.Metrics()))
