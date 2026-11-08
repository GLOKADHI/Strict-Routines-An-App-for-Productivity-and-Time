# Additive shared-client metadata

These changes are implemented by the existing runtime services. They do not
require resetting, replacing, or exporting the live database. No release test
applied them to the owner's live records; all integration fixtures used temporary
databases. The current Windows process is not restarted by packaging.

| Store | Additive change | Applied when |
|---|---|---|
| `system_config` | Create if absent; account-to-Google-subject binding and daily confirmation keys | Shared-client service opens |
| `cloud_sync_files` | Nullable `google_sub` and `local_account_id` columns, added only if absent | Drive backup coordinator opens |
| `client_routine_requests` | Account plus stable request ID primary key and immutable payload SHA-256 | First routine confirmation transaction |
| `drive_pending_records` | Retained source file ID, digest and raw untrusted claim; no direct ledger import | Legacy Drive import adapter used |
| `demo_access_state` | Existing account-scoped 30-second allowance and seven-day cooldown | Existing local demo service/activation |
| `scheduled_activities` | Replace inline time uniqueness with an equivalent partial index excluding CANCELLED rows | Application database initialization |

Routine confirmation binds the existing College/Gym/schedule engines to one
SQLite transaction. It rolls back decision revisions, activities, sync queue and
receipt together if any validation fails. A replay with a changed payload fails.
No separate scoring table or user identity database is introduced.

Cloud backup files and queue rows remain associated with their original account
and Google subject across sign-out/account switches. Existing file contents,
device registrations, immutable events and approval records are retained. The
new backup coordinator never uploads the credential-bearing SQLite file.

Back up the existing database using its established SQLite backup procedure and
retain original evidence before deployment. Do not copy a live WAL database as
an ordinary file and assume it is a consistent backup. Application rollback means
restarting the retained prior binaries, not restoring an old ledger over newer
records. Additional nullable metadata/receipt tables can remain after rollback.

The schedule migration addresses the reproduced legacy College-plan collision.
SQLite requires an atomic table reconstruction to change an inline constraint.
The migration retains all columns, IDs, times, values, indexes, triggers and child
references; a content digest and foreign-key checks guard the copy. Cancelled
plans may share a time with their replacement, while live/noncancelled records
remain unique. A failed migration rolls back the original table. Tests cover
preservation, references, triggers, uniqueness, second startup and rollback.
No schedule row is deleted, assigned a fictitious time, or silently approved.
