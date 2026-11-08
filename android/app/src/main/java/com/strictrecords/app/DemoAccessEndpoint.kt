package com.strictrecords.app

import java.net.URI

/** The demo helper uses the next port on the already paired PC, never a new host. */
object DemoAccessEndpoint {
    fun fromPairedServer(value: String): String? = try {
        val uri = URI(value.trim())
        val host = uri.host?.lowercase().orEmpty()
        val segments = host.split('.')
        val parts = segments.mapNotNull { it.toIntOrNull() }
        val privateIpv4 = segments.size == 4 && parts.size == 4 && parts.all { it in 0..255 } && (
            parts[0] == 10 || parts[0] == 127 ||
            (parts[0] == 192 && parts[1] == 168) ||
            (parts[0] == 172 && parts[1] in 16..31) ||
            (parts[0] == 100 && parts[1] in 64..127))
        val privateHost = privateIpv4 || host == "localhost" || host == "[::1]" ||
            host.endsWith(".ts.net")
        if (uri.scheme !in listOf("http", "https") || host.isBlank() ||
            uri.userInfo != null || uri.query != null || uri.fragment != null ||
            (!uri.path.isNullOrBlank() && uri.path != "/") || uri.port !in 1..65534 ||
            (uri.scheme == "http" && !privateHost)) null
        else URI(uri.scheme, null, uri.host, uri.port + 1, null, null, null).toString()
    } catch (_: Exception) { null }
}
