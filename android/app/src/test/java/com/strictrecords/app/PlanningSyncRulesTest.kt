package com.strictrecords.app
import org.junit.Assert.*
import org.junit.Test
class PlanningSyncRulesTest {
    @Test fun staleYesCannotReplaceNewerNo() { assertFalse(PlanningSyncRules.mayReplaceState(2,1,100,200)) }
    @Test fun oldActiveSnapshotCannotReplaceLaterOff() { assertFalse(PlanningSyncRules.mayReplaceState(2,2,200,100)) }
    @Test fun newerRevisionIsAccepted() { assertTrue(PlanningSyncRules.mayReplaceState(1,2,100,200)) }
    @Test fun invalidTimestampCannotRenewLease() { assertFalse(PlanningSyncRules.mayReplaceState(0,1,0,0)) }
    @Test fun oldReceiptCannotClearNewOutboxChoice() {
        assertFalse(PlanningSyncRules.ownsReceipt("request-new","request-old"))
        assertTrue(PlanningSyncRules.ownsReceipt("request-new","request-new"))
    }
}
