package com.strictrecords.app

import org.json.JSONArray

/**
 * Intelligent parser for Mi Fitness (Xiaomi Wear) sleep screenshots.
 * Integrates with [MiFitnessRegionValidator] for normalized layout region mapping,
 * anchor-based dynamic detection, and structured cross-checks.
 */
object MiFitnessSleepReportParser {

    fun parse(
        ocrText: String,
        blocksJson: JSONArray? = null,
        defaultYear: Int = 2026
    ): MiFitnessSleepBonusRules.ParsedSleepData {
        val validationResult = MiFitnessRegionValidator.validate(
            ocrText = ocrText,
            blocksJson = blocksJson,
            defaultYear = defaultYear
        )
        return validationResult.toParsedSleepData()
    }
}
