package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class DemoAccessEndpointTest {
    @Test fun pairedPrivateHostIsRetained() {
        assertEquals("http://100.93.80.125:8766", DemoAccessEndpoint.fromPairedServer("http://100.93.80.125:8765"))
        assertEquals("http://192.168.1.5:9001", DemoAccessEndpoint.fromPairedServer("http://192.168.1.5:9000/"))
        assertEquals("http://localhost:8766", DemoAccessEndpoint.fromPairedServer("http://localhost:8765"))
    }
    @Test fun publicPlaintextAndInvalidAddressesAreRejected() {
        for (value in listOf("http://example.com:8765", "http://100.128.0.1:8765", "http://256.2.3.4:8765", "http://100.fake.93.80.125:8765", "http://192.168.1.5:65535"))
            assertNull(value, DemoAccessEndpoint.fromPairedServer(value))
    }
    @Test fun credentialsPathsAndQueryOverridesAreRejected() {
        for (value in listOf("http://user:secret@100.93.80.125:8765", "http://100.93.80.125:8765/api", "http://100.93.80.125:8765?host=other", "file:///tmp/pc"))
            assertNull(value, DemoAccessEndpoint.fromPairedServer(value))
    }
    @Test fun tlsOriginRetainsTls() {
        assertEquals("https://pc.example:8766", DemoAccessEndpoint.fromPairedServer("https://pc.example:8765"))
    }
}
