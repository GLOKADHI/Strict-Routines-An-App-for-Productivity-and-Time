package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.time.OffsetDateTime

class PlanningContractTest {
    private fun fixture() = JSONObject(javaClass.classLoader!!.getResourceAsStream("planning_contract_vectors.json")!!.bufferedReader().use { it.readText() })
    @Test fun canonicalActivationCutoffVectors() {
        val cases=fixture().getJSONArray("activation")
        for(i in 0 until cases.length()) {
            val v=cases.getJSONObject(i);val at=OffsetDateTime.parse(v.getString("at")).toInstant().toEpochMilli()
            assertEquals(v.toString(),v.getBoolean("allowed"),GymModeRules.isWithinActivationWindow(at))
            if(v.has("expiry"))assertEquals(OffsetDateTime.parse(v.getString("expiry")).toInstant().toEpochMilli(),GymModeRules.calculateAccessExpiry(at))
        }
    }
    @Test fun canonicalBathReservationVectors() {
        val cases=fixture().getJSONArray("bath_reservations")
        for(i in 0 until cases.length()) {
            val v=cases.getJSONObject(i)
            val slot=BathWindowRules.resolveBathReservedHourlySlot("2026-10-20",v.getString("planned"))
            val targetDay=java.time.LocalDate.parse("2026-10-20").plusDays(v.optInt("day_offset",0).toLong())
                .format(java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd"))
            assertEquals("ACT-$targetDay-"+v.getString("target").replace(":","")+"-HOURLY",slot.occurrenceId)
        }
    }
}
