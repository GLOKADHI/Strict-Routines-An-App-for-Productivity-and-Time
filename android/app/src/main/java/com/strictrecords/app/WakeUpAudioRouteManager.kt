package com.strictrecords.app

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.util.Log

/**
 * Robust Bluetooth audio route evaluation, verification, and monitoring for Wake-Up verification.
 *
 * Establishes that a supported external Bluetooth audio output is genuinely connected
 * and available as an active audio output route for music playback.
 */
object WakeUpAudioRouteManager {

    private const val TAG = "WakeUpAudioRoute"

    enum class RouteState(val userMessage: String) {
        NO_BLUETOOTH_PERMISSION("Bluetooth permission required"),
        NO_EARBUDS_CONNECTED("Connect your earbuds"),
        CHECKING_ROUTE("Checking Bluetooth audio output"),
        CONNECTED_CHECKING_PLAYBACK_ROUTE("Earbuds connected — checking playback route"),
        EARBUDS_VERIFIED("Earbud audio output verified"),
        EARBUDS_DISCONNECTED("Earbuds disconnected — reconnect to continue"),
        ROUTE_UNAVAILABLE("Unable to detect an active earbud audio route")
    }

    data class BluetoothDeviceInfo(
        val name: String,
        val address: String,
        val type: Int,
        val typeName: String,
        val isSink: Boolean
    )

    data class RouteEvaluation(
        val state: RouteState,
        val isVerified: Boolean,
        val deviceName: String,
        val deviceAddress: String,
        val routeTypeName: String,
        val actionableMessage: String,
        val isMuted: Boolean = false
    )

    /**
     * Checks whether Bluetooth permissions required for the current Android version are granted.
     */
    fun hasBluetoothPermission(context: Context): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED
        } else {
            context.checkSelfPermission(Manifest.permission.BLUETOOTH) == PackageManager.PERMISSION_GRANTED
        }
    }

    /**
     * Checks if the device's Bluetooth adapter is enabled.
     */
    fun isBluetoothEnabled(context: Context): Boolean {
        return try {
            val bm = context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
            val adapter = bm?.adapter ?: BluetoothAdapter.getDefaultAdapter()
            adapter?.isEnabled == true
        } catch (_: Exception) {
            false
        }
    }

    /**
     * Returns whether a given AudioDeviceInfo represents a supported external Bluetooth audio sink.
     */
    fun isSupportedBluetoothAudioDevice(device: AudioDeviceInfo?): Boolean {
        if (device == null || !device.isSink) return false
        return when (device.type) {
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
            AudioDeviceInfo.TYPE_BLE_HEADSET,
            AudioDeviceInfo.TYPE_BLE_SPEAKER -> true
            else -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P &&
                    device.type == AudioDeviceInfo.TYPE_HEARING_AID) {
                    true
                } else if (Build.VERSION.SDK_INT >= 33 &&
                    device.type == 30 /* AudioDeviceInfo.TYPE_BLE_BROADCAST */) {
                    true
                } else {
                    false
                }
            }
        }
    }

    /**
     * Human-readable name for an audio device type.
     */
    fun getRouteTypeName(deviceType: Int): String {
        return when (deviceType) {
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> "BLUETOOTH_A2DP"
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "BLUETOOTH_SCO"
            AudioDeviceInfo.TYPE_BLE_HEADSET -> "BLE_HEADSET"
            AudioDeviceInfo.TYPE_BLE_SPEAKER -> "BLE_SPEAKER"
            AudioDeviceInfo.TYPE_HEARING_AID -> "HEARING_AID"
            30 -> "BLE_BROADCAST"
            AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> "PHONE_SPEAKER"
            AudioDeviceInfo.TYPE_WIRED_HEADSET -> "WIRED_HEADSET"
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES -> "WIRED_HEADPHONES"
            AudioDeviceInfo.TYPE_USB_HEADSET -> "USB_HEADSET"
            else -> "UNKNOWN_$deviceType"
        }
    }

    /**
     * Queries all currently available output audio devices that are supported Bluetooth audio routes.
     */
    @SuppressLint("MissingPermission")
    fun getConnectedBluetoothAudioOutputs(context: Context): List<BluetoothDeviceInfo> {
        val list = mutableListOf<BluetoothDeviceInfo>()
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager ?: return list
        try {
            val outputs = audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            for (dev in outputs) {
                if (isSupportedBluetoothAudioDevice(dev)) {
                    val rawName = dev.productName?.toString()?.trim()
                    val name = if (!rawName.isNullOrBlank()) rawName else "Bluetooth Audio Output"
                    var address = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                        runCatching { dev.address?.trim().orEmpty() }.getOrDefault("")
                    } else ""
                    if (address.isBlank() && hasBluetoothPermission(context)) {
                        runCatching {
                            val bm = context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
                            val adapter = bm?.adapter ?: BluetoothAdapter.getDefaultAdapter()
                            val bonded = adapter?.bondedDevices
                            val match = bonded?.filter { it.name.equals(name, ignoreCase = true) }
                            if (match?.size == 1) {
                                address = match.first().address.orEmpty()
                            }
                        }
                    }
                    list.add(
                        BluetoothDeviceInfo(
                            name = name,
                            address = address,
                            type = dev.type,
                            typeName = getRouteTypeName(dev.type),
                            isSink = dev.isSink
                        )
                    )
                }
            }
            list.sortWith(compareByDescending { dev ->
                when (dev.type) {
                    AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> 100
                    AudioDeviceInfo.TYPE_BLE_HEADSET -> 90
                    AudioDeviceInfo.TYPE_BLE_SPEAKER -> 80
                    AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> 10
                    else -> 0
                }
            })
        } catch (e: Exception) {
            Log.w(TAG, "getConnectedBluetoothAudioOutputs error: ${e.message}")
        }
        return list
    }

    /**
     * Evaluates the active Bluetooth earbud route against configured preferences.
     */
    fun evaluateRoute(
        context: Context,
        configuredName: String = "",
        configuredAddress: String = ""
    ): RouteEvaluation {
        // 1. Permission check
        if (!hasBluetoothPermission(context)) {
            return RouteEvaluation(
                state = RouteState.NO_BLUETOOTH_PERMISSION,
                isVerified = false,
                deviceName = "None",
                deviceAddress = "",
                routeTypeName = "NONE",
                actionableMessage = "Bluetooth permission required. Grant permission to verify earbuds."
            )
        }

        // 2. Adapter check
        if (!isBluetoothEnabled(context)) {
            return RouteEvaluation(
                state = RouteState.NO_EARBUDS_CONNECTED,
                isVerified = false,
                deviceName = "None",
                deviceAddress = "",
                routeTypeName = "NONE",
                actionableMessage = "Bluetooth is turned off. Turn on Bluetooth and connect your earbuds."
            )
        }

        // 3. Audio output check
        val btOutputs = getConnectedBluetoothAudioOutputs(context)
        if (btOutputs.isEmpty()) {
            return RouteEvaluation(
                state = RouteState.NO_EARBUDS_CONNECTED,
                isVerified = false,
                deviceName = "None",
                deviceAddress = "",
                routeTypeName = "NONE",
                actionableMessage = "No Bluetooth audio output detected. Connect your earbuds and try again."
            )
        }

        // 4. Volume check
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
        val currentVol = audioManager?.getStreamVolume(AudioManager.STREAM_MUSIC) ?: 0
        val isMuted = currentVol == 0

        // 5. Match against configured earbuds
        val cfgName = configuredName.trim()
        val cfgAddr = configuredAddress.trim().replace("-", ":")
        val allowAny = cfgName.isBlank() ||
                cfgName.equals("None configured", ignoreCase = true) ||
                cfgName.equals("Any Bluetooth Audio Output", ignoreCase = true)

        val matchedDevice: BluetoothDeviceInfo? = if (allowAny) {
            btOutputs.firstOrNull()
        } else {
            btOutputs.firstOrNull { dev ->
                val devAddr = dev.address.trim().replace("-", ":")
                (cfgAddr.isNotBlank() && devAddr.isNotBlank() && devAddr.equals(cfgAddr, ignoreCase = true)) ||
                        dev.name.equals(cfgName, ignoreCase = true) ||
                        dev.name.contains(cfgName, ignoreCase = true) ||
                        cfgName.contains(dev.name, ignoreCase = true) ||
                        dev.name.equals("Bluetooth Audio Output", ignoreCase = true)
            }
        }

        if (matchedDevice != null) {
            val resolvedName = if (matchedDevice.name.equals("Bluetooth Audio Output", ignoreCase = true) && !allowAny) {
                cfgName
            } else {
                matchedDevice.name
            }

            val resolvedAddress = when {
                matchedDevice.address.isNotBlank() -> matchedDevice.address
                cfgAddr.isNotBlank() && (matchedDevice.name.equals(cfgName, ignoreCase = true) || matchedDevice.name.equals("Bluetooth Audio Output", ignoreCase = true)) -> cfgAddr
                else -> ""
            }

            val msg = if (isMuted) {
                "Earbuds verified: $resolvedName (${matchedDevice.typeName}). Note: Media volume is muted; increase volume for playback."
            } else {
                "Earbud audio output verified: $resolvedName (${matchedDevice.typeName})."
            }

            return RouteEvaluation(
                state = RouteState.EARBUDS_VERIFIED,
                isVerified = true,
                deviceName = resolvedName,
                deviceAddress = resolvedAddress,
                routeTypeName = matchedDevice.typeName,
                actionableMessage = msg,
                isMuted = isMuted
            )
        }

        // Earbuds are connected, but do not match the specifically configured model
        val availableNames = btOutputs.joinToString(", ") { "${it.name} (${it.typeName})" }
        return RouteEvaluation(
            state = RouteState.ROUTE_UNAVAILABLE,
            isVerified = false,
            deviceName = btOutputs.first().name,
            deviceAddress = btOutputs.first().address,
            routeTypeName = btOutputs.first().typeName,
            actionableMessage = "Connected Bluetooth device ($availableNames) does not match configured earbuds ($cfgName). Select your earbuds or connect the configured pair.",
            isMuted = isMuted
        )
    }

    /**
     * Verifies if a given routed AudioDeviceInfo from MediaPlayer matches the verified Bluetooth route.
     */
    fun isMediaPlayerRouteVerified(
        routedDevice: AudioDeviceInfo?,
        expectedName: String,
        expectedAddress: String
    ): Boolean {
        if (routedDevice == null || !isSupportedBluetoothAudioDevice(routedDevice)) return false
        val allowAny = expectedName.isBlank() ||
                expectedName.equals("None configured", ignoreCase = true) ||
                expectedName.equals("Any Bluetooth Audio Output", ignoreCase = true)
        if (allowAny) return true

        val routedName = routedDevice.productName?.toString()?.trim().orEmpty()
        val routedAddr = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            runCatching { routedDevice.address?.trim().orEmpty() }.getOrDefault("")
        } else ""

        val cleanExpName = expectedName.trim()
        val cleanExpAddr = expectedAddress.trim().replace("-", ":")
        val cleanRouteAddr = routedAddr.trim().replace("-", ":")

        val isRedactedExp = cleanExpAddr.equals("02:00:00:00:00:00", ignoreCase = true) ||
            cleanExpAddr.equals("00:00:00:00:00:00", ignoreCase = true)
        val isRedactedRoute = cleanRouteAddr.equals("02:00:00:00:00:00", ignoreCase = true) ||
            cleanRouteAddr.equals("00:00:00:00:00:00", ignoreCase = true)

        val hasRealExpAddr = cleanExpAddr.isNotBlank() && !isRedactedExp
        val hasRealRouteAddr = cleanRouteAddr.isNotBlank() && !isRedactedRoute

        val isGenericName = routedName.isBlank() ||
            routedName.equals("Bluetooth Audio Output", ignoreCase = true) ||
            routedName.equals("Bluetooth Audio Device", ignoreCase = true) ||
            routedName.equals("Bluetooth Audio", ignoreCase = true) ||
            routedName.equals("Bluetooth", ignoreCase = true)

        val nameMatches = !isGenericName && (
            routedName.equals(cleanExpName, ignoreCase = true) ||
            routedName.contains(cleanExpName, ignoreCase = true) ||
            cleanExpName.contains(routedName, ignoreCase = true)
        )

        // 1. If the product name explicitly matches the configured earbud model (e.g. "WF-1000XM5"), route is verified.
        // In True Wireless Stereo (TWS), left and right buds have distinct MAC addresses or LE Audio randomized addresses,
        // so explicit model match is authoritative.
        if (nameMatches) {
            return true
        }

        // 2. If the address matches (exact MAC or matching OUI / TWS channel prefix), route is verified.
        if (hasRealExpAddr && hasRealRouteAddr) {
            if (cleanExpAddr.equals(cleanRouteAddr, ignoreCase = true)) return true
            if (cleanExpAddr.contains(":") && cleanRouteAddr.contains(":") &&
                cleanExpAddr.substringBeforeLast(":").equals(cleanRouteAddr.substringBeforeLast(":"), ignoreCase = true)
            ) {
                return true
            }
        }

        // 3. If driver exposes a generic name ("Bluetooth Audio Output"), accept if address matches or address is redacted by OS
        if (isGenericName && (!hasRealExpAddr || !hasRealRouteAddr)) {
            return true
        }

        return false
    }
}
