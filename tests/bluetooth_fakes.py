"""Software-only RFCOMM adapters. Never opens a physical radio or changes pairing."""
import queue
import socket

PEER = 'AA:BB:CC:DD:EE:01'


class FakeListener:
    def __init__(self): self.connections = queue.Queue(); self.closed = False
    def accept(self):
        if self.closed: raise OSError('Closed test receiver')
        try: return self.connections.get(timeout=0.05)
        except queue.Empty: raise socket.timeout()
    def close(self): self.closed = True


class FakeBackend:
    @staticmethod
    def capability(): return True, 'SIMULATED RFCOMM; no physical Bluetooth test'
    @staticmethod
    def paired_devices(): return [{'address': PEER, 'name': 'Simulated Android sender', 'paired': True}]
    def __init__(self): self.listener = FakeListener(); self.closed = False
    def listen(self): return self.listener
    def close(self): self.closed = True; self.listener.close()
