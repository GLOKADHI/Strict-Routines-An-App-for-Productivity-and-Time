"""Windows RFCOMM adapter. SDP registration is required for Android UUID lookup.

Uses installed Win32/Winsock APIs; no BLE/GATT substitution or pairing changes.
"""
import ctypes as C
import os
import socket
import struct
import uuid

SPP_UUID = '00001101-0000-1000-8000-00805F9B34FB'
U32 = C.c_uint32


class GUID(C.Structure):
    _fields_ = [('data1', U32), ('data2', C.c_uint16), ('data3', C.c_uint16), ('data4', C.c_ubyte * 8)]


class SockaddrBth(C.Structure):
    _pack_ = 1  # ws2bth.h includes pshpack1.h
    _fields_ = [('family', C.c_uint16), ('address', C.c_uint64), ('service', GUID), ('port', U32)]


class SocketAddress(C.Structure):
    _fields_ = [('address', C.c_void_p), ('length', C.c_int)]


class CsaddrInfo(C.Structure):
    _fields_ = [('local', SocketAddress), ('remote', SocketAddress), ('socket_type', C.c_int), ('protocol', C.c_int)]


class QuerySet(C.Structure):
    _fields_ = [('size', U32), ('name', C.c_wchar_p), ('service', C.POINTER(GUID)),
                ('version', C.c_void_p), ('comment', C.c_wchar_p), ('namespace', U32),
                ('provider', C.c_void_p), ('context', C.c_wchar_p), ('protocol_count', U32),
                ('protocols', C.c_void_p), ('query', C.c_wchar_p), ('address_count', U32),
                ('addresses', C.POINTER(CsaddrInfo)), ('flags', U32), ('blob', C.c_void_p)]


class SystemTime(C.Structure):
    _fields_ = [(name, C.c_uint16) for name in ('year', 'month', 'dow', 'day', 'hour', 'minute', 'second', 'ms')]


class DeviceInfo(C.Structure):
    _fields_ = [('size', U32), ('address', C.c_uint64), ('device_class', U32),
                ('connected', C.c_int), ('remembered', C.c_int), ('authenticated', C.c_int),
                ('last_seen', SystemTime), ('last_used', SystemTime), ('name', C.c_wchar * 248)]


class SearchParams(C.Structure):
    _fields_ = [('size', U32), ('authenticated', C.c_int), ('remembered', C.c_int),
                ('unknown', C.c_int), ('connected', C.c_int), ('inquiry', C.c_int),
                ('timeout', C.c_ubyte), ('radio', C.c_void_p)]


def address_string(value):
    return ':'.join(f'{(value >> shift) & 255:02X}' for shift in range(40, -1, -8))


def normalize_address(value):
    text = str(value).strip().upper().replace('-', ':')
    if len(text) != 17 or any(len(part) != 2 or any(ch not in '0123456789ABCDEF' for ch in part) for part in text.split(':')):
        raise ValueError('Select a valid paired Bluetooth device address')
    return text


class WindowsRfcomm:
    SERVICE_UUID = SPP_UUID
    SERVICE_NAME = 'StrictRoutines Temporary Transfer'
    def __init__(self):
        if os.name != 'nt':
            raise OSError('The Windows RFCOMM receiver requires Windows')
        self.ws = C.WinDLL('Ws2_32.dll', use_last_error=True)
        self.ws.bind.argtypes = [C.c_size_t, C.c_void_p, C.c_int]
        self.ws.getsockname.argtypes = [C.c_size_t, C.c_void_p, C.POINTER(C.c_int)]
        self.ws.WSASetServiceW.argtypes = [C.POINTER(QuerySet), C.c_int, U32]
        self.ws.WSAGetLastError.restype = C.c_int
        self._registration = None
        self.socket = None

    @staticmethod
    def capability():
        if os.name != 'nt' or not hasattr(socket, 'AF_BLUETOOTH'):
            return False, 'Windows Bluetooth RFCOMM is unavailable'
        try:
            with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM):
                pass
            return True, 'RFCOMM available; a selected paired sender and advertised receiver are required'
        except OSError as error:
            return False, f'Enable Bluetooth on this PC and check its adapter ({error.winerror or error.errno})'

    @staticmethod
    def paired_devices():
        if os.name != 'nt':
            return []
        api = C.WinDLL('BluetoothAPIs.dll', use_last_error=True)
        api.BluetoothFindFirstDevice.argtypes = [C.POINTER(SearchParams), C.POINTER(DeviceInfo)]
        api.BluetoothFindFirstDevice.restype = C.c_void_p
        api.BluetoothFindNextDevice.argtypes = [C.c_void_p, C.POINTER(DeviceInfo)]
        api.BluetoothFindDeviceClose.argtypes = [C.c_void_p]
        search = SearchParams(); search.size = C.sizeof(search); search.authenticated = 1; search.remembered = 1
        info = DeviceInfo(); info.size = C.sizeof(info)
        handle = api.BluetoothFindFirstDevice(C.byref(search), C.byref(info))
        if not handle:
            return []
        found = {}
        try:
            while True:
                if info.authenticated:
                    address = address_string(info.address)
                    found[address] = {'address': address, 'name': info.name or address, 'paired': True,
                                      'connected': bool(info.connected)}
                if not api.BluetoothFindNextDevice(handle, C.byref(info)):
                    break
        finally:
            api.BluetoothFindDeviceClose(handle)
        return sorted(found.values(), key=lambda row: row['name'].lower())

    def listen(self):
        try:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            self.socket = sock
            # Enforce OS-supported pairing authentication and encrypted RFCOMM.
            sock.setsockopt(3, C.c_int(0x80000001).value, struct.pack('<I', 1))
            sock.setsockopt(3, 2, struct.pack('<I', 1))
            address = SockaddrBth(); address.family = 32; address.port = 0xFFFFFFFF
            if self.ws.bind(sock.fileno(), C.byref(address), C.sizeof(address)):
                raise OSError(self.ws.WSAGetLastError(), 'Cannot bind Bluetooth receiver')
            size = C.c_int(C.sizeof(address))
            if self.ws.getsockname(sock.fileno(), C.byref(address), C.byref(size)):
                raise OSError(self.ws.WSAGetLastError(), 'Cannot resolve Bluetooth receiver channel')
            sock.listen(1); sock.settimeout(1.0)
            service = GUID.from_buffer_copy(uuid.UUID(self.SERVICE_UUID).bytes_le)
            local = CsaddrInfo(SocketAddress(C.addressof(address), C.sizeof(address)), SocketAddress(), 1, 3)
            query = QuerySet(); query.size = C.sizeof(query); query.name = self.SERVICE_NAME
            query.service = C.pointer(service); query.namespace = 16
            query.address_count = 1; query.addresses = C.pointer(local)
            # Retain all pointed-to objects until the matching unregister operation.
            self._registration = (query, local, address, service)
            if self.ws.WSASetServiceW(C.byref(query), 0, 0):
                self._registration = None
                raise OSError(self.ws.WSAGetLastError(), 'Cannot advertise Bluetooth SPP service')
            return sock
        except Exception:
            self.close()
            raise

    def close(self):
        registration, self._registration = self._registration, None
        if registration:
            self.ws.WSASetServiceW(C.byref(registration[0]), 2, 0)
        sock, self.socket = self.socket, None
        if sock:
            sock.close()


class AutomaticSyncRfcomm(WindowsRfcomm):
    # Separate service: existing temporary-code clients never enter automatic sync.
    SERVICE_UUID = '63b3f238-5317-4b5a-bc18-2bb6d482ca60'
    SERVICE_NAME = 'StrictRoutines Automatic Sync'
