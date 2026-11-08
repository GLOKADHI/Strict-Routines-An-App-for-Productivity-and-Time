# Android proof approval receipt

Timeline and Today's Point Activity open the same native, read-only receipt for
proof events. Other score events retain their existing audit dialog.

## Data binding

The repository rereads the exact event ID from the Android score_events ledger,
resolves proof_reviews by evidence/submission ID, and resolves scheduled_activities
by activity ID. It never approves a submission or calls a points writer.

The amount comes from the stored row. “Points Credited” requires a positive ledger
row, a matching audit amount, and a currently approved proof passing the existing
eligibility check. Missing or inconsistent records display pending/reconciliation
states. This confirms the local ledger, not a separate server-ledger posting.

Validation comes only from audit.taufa_ai. Missing checks remain unavailable, false
checks remain negative, and a denominator appears only when max_score is stored.
Descriptions appear only when present in the authoritative activity record.

Action, reviewer, approval source and administrator remain independent. Explicit
audit-policy flags are honored. Incomplete human authorization, an AI identity
reused as its human authorizer, and approval/rejection contradictions are flagged
without rewriting the original record.

## Evidence and audit

Evidence loads off the UI thread from app-private storage or the paired server's
GET /api/device/proof_evidence endpoint using the registered device's Bearer token.
The endpoint checks ownership, retained-storage containment, deletion, actual image
content, size and hash. It accepts an evidence ID, never a client file path.
Responses are private and not publicly cached.

The client disables authenticated redirects, bounds downloads and decoding,
preserves aspect ratio, and caches bytes in memory per credential and identity.
Missing files use a neutral placeholder. Tapping a thumbnail opens a larger viewer.

The complete stored event, raw audit_json and parsed audit remain available in the
collapsed technical section. Formatting/highlighting is deferred until expansion.
Text wraps; JSON and identifier copy actions preserve the full value. Raw timestamps
remain in technical data.

The modal uses an accessible close footer, internal scrolling, phone stacking and
two evidence/details columns at 720 dp and above. Opening a receipt dismisses the
previous instance.

## Verification scope

Eight isolated Python tests cover original bytes/read-only behavior, authorization,
ownership, deleted/missing/corrupt evidence, hash mismatch and storage escape.
Four JVM model tests cover approval versus credit, rejected/pending/missing events,
negative validation, long IDs, absent score denominators and provenance preservation.

The new Kotlin components were compiled against API 34 with a signature-only engine
adapter in scratch/proof_receipt_check. This is not a full app compile. No APK was
built or installed. Phone/tablet visual testing, production ledger integration and
physical evidence viewing remain runtime checks. The Windows dashboard is unchanged.
