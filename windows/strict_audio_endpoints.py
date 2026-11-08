"""Thread-local Core Audio access and read-only jack topology inspection."""
from contextlib import contextmanager
import ctypes as ct
from functools import lru_cache


def is_active_render(device):
    state=getattr(device,'state',None)
    try:active=int(getattr(state,'value',state))==1
    except (ValueError,TypeError):active=str(state).casefold() in {'active','audiodevicestate.active'}
    return str(getattr(device,'id','')).casefold().startswith('{0.0.0.') and active


def form_factor(device):
    for key,value in (getattr(device,'properties',{}) or {}).items():
        if str(key).upper().strip()=='{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E} 0':
            try:return int(getattr(value,'value',value))
            except (TypeError,ValueError):return None
    return None


@contextmanager
def audio_devices():
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    devices=[]
    try:
        enumerator = AudioUtilities.GetDeviceEnumerator()
        endpoints = enumerator.EnumAudioEndpoints(0, 1)  # 0: eRender, 1: DEVICE_STATE_ACTIVE
        for i in range(endpoints.GetCount()):
            try:
                raw_dev = endpoints.Item(i)
                devices.append(AudioUtilities.CreateDevice(raw_dev))
            except Exception:
                pass
        yield devices
    finally:
        devices.clear()
        comtypes.CoUninitialize()


@contextmanager
def active_render_endpoints():
    """Returns active audio rendering endpoints without triggering COM property errors on phantom devices."""
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    devices = []
    try:
        enumerator = AudioUtilities.GetDeviceEnumerator()
        endpoints = enumerator.EnumAudioEndpoints(0, 1)  # 0: eRender, 1: DEVICE_STATE_ACTIVE
        for i in range(endpoints.GetCount()):
            try:
                raw_dev = endpoints.Item(i)
                wrapped = AudioUtilities.CreateDevice(raw_dev)
                devices.append(wrapped)
            except Exception:
                pass
        yield devices
    finally:
        devices.clear()
        comtypes.CoUninitialize()


def get_default_render_device():
    """Returns (device, friendly_name, device_id) of default audio rendering endpoint or None."""
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    try:
        enumerator = AudioUtilities.GetDeviceEnumerator()
        dev = enumerator.GetDefaultAudioEndpoint(0, 0)  # 0: eRender, 0: eConsole
        wrapped = AudioUtilities.CreateDevice(dev)
        return dev, getattr(wrapped, "FriendlyName", "Default Speakers"), dev.GetId()
    except Exception:
        return None, "Unavailable", None
    finally:
        comtypes.CoUninitialize()


@lru_cache(maxsize=1)
def _jack_interfaces():
    """Use actual IKsJackDescription; topology activation alone proves nothing.

    Windows SDK GUIDs/vtable order: microsoft/win32metadata devicetopology.h.
    Caller owns a COM apartment. Unsupported drivers raise, never return clear.
    """
    from comtypes import IUnknown, GUID, COMMETHOD, HRESULT, CLSCTX_ALL
    P=ct.POINTER
    class Jack(ct.Structure):
        _fields_=[('channels',ct.c_uint32),('color',ct.c_uint32),('connection',ct.c_int),
                  ('geo',ct.c_int),('general',ct.c_int),('port',ct.c_int),('connected',ct.c_int32)]
    class Connector(IUnknown):
        _iid_=GUID('{9C2C4058-23F5-41DE-877A-DF3AF236A09E}')
    Connector._methods_=[
        COMMETHOD([],HRESULT,'GetType',(['out'],P(ct.c_int),'value')),
        COMMETHOD([],HRESULT,'GetDataFlow',(['out'],P(ct.c_int),'value')),
        COMMETHOD([],HRESULT,'ConnectTo',(['in'],P(Connector),'other')),
        COMMETHOD([],HRESULT,'Disconnect'),
        COMMETHOD([],HRESULT,'IsConnected',(['out'],P(ct.c_int32),'value')),
        COMMETHOD([],HRESULT,'GetConnectedTo',(['out'],P(P(Connector)),'other'))]
    class Topology(IUnknown):
        _iid_=GUID('{2A07407E-6497-4A18-9787-32F79BD0D98F}')
        _methods_=[COMMETHOD([],HRESULT,'GetConnectorCount',(['out'],P(ct.c_uint),'count')),
                   COMMETHOD([],HRESULT,'GetConnector',(['in'],ct.c_uint,'index'),(['out'],P(P(Connector)),'connector'))]
    class Part(IUnknown):
        _iid_=GUID('{AE2DE0E4-5BCA-4F2D-AA46-5D13F8FDB3A9}')
        _methods_=[
            COMMETHOD([],HRESULT,'GetName',(['out'],P(ct.c_wchar_p),'name')),
            COMMETHOD([],HRESULT,'GetLocalId',(['out'],P(ct.c_uint),'value')),
            COMMETHOD([],HRESULT,'GetGlobalId',(['out'],P(ct.c_wchar_p),'value')),
            COMMETHOD([],HRESULT,'GetPartType',(['out'],P(ct.c_int),'value')),
            COMMETHOD([],HRESULT,'GetSubType',(['out'],P(GUID),'value')),
            COMMETHOD([],HRESULT,'GetControlInterfaceCount',(['out'],P(ct.c_uint),'value')),
            COMMETHOD([],HRESULT,'GetControlInterface',(['in'],ct.c_uint,'index'),(['out'],P(P(IUnknown)),'value')),
            COMMETHOD([],HRESULT,'EnumPartsIncoming',(['out'],P(P(IUnknown)),'value')),
            COMMETHOD([],HRESULT,'EnumPartsOutgoing',(['out'],P(P(IUnknown)),'value')),
            COMMETHOD([],HRESULT,'GetTopologyObject',(['out'],P(P(Topology)),'value')),
            COMMETHOD([],HRESULT,'Activate',(['in'],ct.c_uint32,'context'),(['in'],P(GUID),'iid'),(['out'],P(P(IUnknown)),'value'))]
    class Jacks(IUnknown):
        _iid_=GUID('{4509F757-2D46-4637-8E62-CE7DB944F57B}')
        _methods_=[COMMETHOD([],HRESULT,'GetJackCount',(['out'],P(ct.c_uint),'count')),
                   COMMETHOD([],HRESULT,'GetJackDescription',(['in'],ct.c_uint,'index'),(['out'],P(Jack),'description'))]
    class Jack2(ct.Structure):
        _fields_=[('device_state',ct.c_uint32),('capabilities',ct.c_uint32)]
    class Jacks2(IUnknown):
        _iid_=GUID('{478F3A9B-E0C9-4827-9228-6F5505FFE76A}')
        _methods_=[COMMETHOD([],HRESULT,'GetJackCount',(['out'],P(ct.c_uint),'count')),
                   COMMETHOD([],HRESULT,'GetJackDescription2',(['in'],ct.c_uint,'index'),(['out'],P(Jack2),'description'))]
    return Topology,Part,Jacks,Jacks2


def jack_descriptions(device):
    from comtypes import CLSCTX_ALL
    Topology,Part,Jacks,Jacks2=_jack_interfaces()
    topology=device._dev.Activate(Topology._iid_,CLSCTX_ALL,None).QueryInterface(Topology)
    result=[];supported=False
    for index in range(min(32,topology.GetConnectorCount())):
        try:
            connector=topology.GetConnector(index).GetConnectedTo()
            part=connector.QueryInterface(Part)
            jacks=part.Activate(CLSCTX_ALL,ct.byref(Jacks._iid_)).QueryInterface(Jacks)
            count=jacks.GetJackCount();supported=True
            try:jacks2=part.Activate(CLSCTX_ALL,ct.byref(Jacks2._iid_)).QueryInterface(Jacks2)
            except Exception:jacks2=None
            for slot in range(min(32,count)):
                jack=jacks.GetJackDescription(slot)
                capabilities=jacks2.GetJackDescription2(slot).capabilities if jacks2 else 0
                result.append(dict(connected=bool(jack.connected),connection_type=jack.connection,port=jack.port,presence_supported=bool(capabilities&1)))
        except Exception:
            continue
    if not supported:raise RuntimeError('Driver does not expose jack descriptions')
    return result
