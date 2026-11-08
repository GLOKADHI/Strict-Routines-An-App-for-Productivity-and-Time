package com.strictrecords.app

/** A polling gap or uncertain system window is never evidence of a new app visit. */
class PopupEntrySession {
    private var packageName: String? = null
    private var transition = 0L
    private var presented = false
    private var dismissed = false
    @Synchronized fun observeTarget(pkg: String): Long {
        if (packageName != pkg) { packageName = pkg; transition++; presented = false; dismissed = false }
        return transition
    }
    @Synchronized fun observeExit() { packageName = null; presented = false; dismissed = false }
    @Synchronized fun canPresent(pkg: String): Boolean = packageName == pkg && !presented && !dismissed
    @Synchronized fun claim(pkg: String, entry: Long): Boolean {
        if (packageName != pkg || transition != entry || presented || dismissed) return false
        presented = true
        return true
    }
    @Synchronized fun dismiss(pkg: String) { observeTarget(pkg); presented = true; dismissed = true }
    /** Temporary loss of window confirmation is not a user dismissal or a new visit. */
    @Synchronized fun release(pkg: String) { if (packageName == pkg && !dismissed) presented = false }
    /** A genuine eligibility transition can rearm this same visit without inventing another visit/history event. */
    @Synchronized fun onEligibilityChanged(pkg: String) {
        if (packageName == pkg) { presented = false; dismissed = false }
    }
    @Synchronized fun currentId(): Long = transition
    @Synchronized fun isPresentationCurrent(pkg: String, entry: Long): Boolean =
        packageName == pkg && transition == entry && presented && !dismissed
    @Synchronized fun failedAttach(pkg: String, entry: Long) {
        if (packageName == pkg && transition == entry) presented = false
    }
}
