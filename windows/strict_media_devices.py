"""DirectShow input identities for the existing OpenCV camera backend.

Enumeration reads device metadata only: it never opens a capture device. Browser
device IDs are origin-specific and must not be mistaken for OpenCV indices.
"""
import ctypes as ct
import hashlib
import re
import uuid


class GUID(ct.Structure):
    _fields_ = [('data', ct.c_ubyte * 16)]

    def __init__(self, value):
        super().__init__()
        self.data[:] = uuid.UUID(value).bytes_le


def _method(pointer, slot, result, *arguments):
    table = ct.cast(pointer, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
    return ct.WINFUNCTYPE(result, ct.c_void_p, *arguments)(table[slot])


def enumerate_cameras():
    """Return native identities in exactly DirectShow/OpenCV enumeration order."""
    ole = ct.OleDLL('ole32')
    automation = ct.OleDLL('oleaut32')
    ole.CoInitializeEx.argtypes = [ct.c_void_p, ct.c_uint32]
    ole.CoInitializeEx.restype = ct.c_int32
    initialized = ole.CoInitializeEx(None, 2)
    if initialized not in (0, 1, -2147417850):  # RPC_E_CHANGED_MODE: already initialized
        raise OSError('Windows camera enumeration could not initialize COM')
    ole.CoCreateInstance.argtypes = [ct.POINTER(GUID), ct.c_void_p, ct.c_uint32, ct.POINTER(GUID), ct.POINTER(ct.c_void_p)]
    ole.CoCreateInstance.restype = ct.c_int32
    ole.CoTaskMemFree.argtypes = [ct.c_void_p]
    automation.VariantClear.argtypes = [ct.c_void_p]
    system, enumerator = ct.c_void_p(), ct.c_void_p()

    def release(pointer):
        if pointer:
            _method(pointer, 2, ct.c_uint32)(pointer)

    def read_property(bag, name):
        variant = ct.create_string_buffer(24 if ct.sizeof(ct.c_void_p) == 8 else 16)
        result = _method(bag, 3, ct.c_int32, ct.c_wchar_p, ct.c_void_p, ct.c_void_p)(bag, name, variant, None)
        try:
            if result == 0 and ct.c_ushort.from_buffer(variant).value == 8:  # VT_BSTR
                address = ct.c_void_p.from_buffer(variant, 8).value
                return ct.wstring_at(address) if address else ''
            return ''
        finally:
            automation.VariantClear(variant)

    try:
        clsid = GUID('62BE5D10-60EB-11D0-BD3B-00A0C911CE86')
        interface = GUID('29840822-5B84-11D0-BD3B-00A0C911CE86')
        if ole.CoCreateInstance(ct.byref(clsid), None, 1, ct.byref(interface), ct.byref(system)) != 0:
            raise OSError('Windows camera device enumerator is unavailable')
        category = GUID('860BB310-5D01-11D0-BD3B-00A0C911CE86')
        result = _method(system, 3, ct.c_int32, ct.POINTER(GUID), ct.POINTER(ct.c_void_p), ct.c_uint32)(system, ct.byref(category), ct.byref(enumerator), 0)
        if result == 1:
            return []
        if result != 0 or not enumerator:
            raise OSError('Windows camera list is unavailable')
        rows, index = [], 0
        while True:
            moniker, fetched = ct.c_void_p(), ct.c_uint32()
            result = _method(enumerator, 3, ct.c_int32, ct.c_uint32, ct.POINTER(ct.c_void_p), ct.POINTER(ct.c_uint32))(enumerator, 1, ct.byref(moniker), ct.byref(fetched))
            if result == 1:
                break
            if result != 0 or not moniker:
                raise OSError('Windows camera list changed while reading it')
            bag, display = ct.c_void_p(), ct.c_void_p()
            try:
                name, path = '', ''
                iid = GUID('55272A00-42CB-11CE-8135-00AA004BB851')
                if _method(moniker, 9, ct.c_int32, ct.c_void_p, ct.c_void_p, ct.POINTER(GUID), ct.POINTER(ct.c_void_p))(moniker, None, None, ct.byref(iid), ct.byref(bag)) == 0:
                    name, path = read_property(bag, 'FriendlyName'), read_property(bag, 'DevicePath')
                if _method(moniker, 20, ct.c_int32, ct.c_void_p, ct.c_void_p, ct.POINTER(ct.c_void_p))(moniker, None, None, ct.byref(display)) == 0 and display:
                    path = ct.wstring_at(display) or path
                if path and name:
                    rows.append(dict(native_id=hashlib.sha256(path.encode('utf-8')).hexdigest(), name=name, index=index))
            finally:
                if display:
                    ole.CoTaskMemFree(display)
                release(bag)
                release(moniker)
            index += 1
        return rows
    finally:
        release(enumerator)
        release(system)
        if initialized in (0, 1):
            ole.CoUninitialize()


def _label(value):
    # Chromium appends the USB VID:PID to some Windows friendly names.
    return re.sub(r'\s*\([0-9a-f]{4}:[0-9a-f]{4}\)$', '', str(value).strip(), flags=re.I).casefold()


def match_camera_label(label, cameras=None):
    matches = [c for c in (enumerate_cameras() if cameras is None else cameras) if _label(c['name']) == _label(label)]
    if len(matches) != 1:
        raise ValueError('The selected camera could not be uniquely matched to the Windows study monitor. Choose another input and test again.')
    return matches[0]


def resolve_camera_id(camera_id, cameras=None):
    matches = [c for c in (enumerate_cameras() if cameras is None else cameras) if c['native_id'] == camera_id]
    if len(matches) != 1:
        raise ValueError('The selected camera is unavailable. Refresh the inputs, choose a camera and test again.')
    return matches[0]
