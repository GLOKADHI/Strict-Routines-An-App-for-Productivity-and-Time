# StrictRoutines home-screen widgets

Open **Settings & Profile → Widgets**. Select a layout, review the live saved-data preview, and choose **Add to Home Screen**. Confirm placement in the launcher. If pinning is unsupported, long-press an empty home-screen area, open **Widgets → StrictRoutines**, and drag a widget into place.

## Available layouts

| Size | Types |
| --- | --- |
| Small, approximately 2 × 2 | S1 Next task; S2 Window status; S3 Today's points; S4 Monitoring |
| Medium, approximately 4 × 2 | M1 Upcoming tasks; M2 Points and next reward |
| Large, approximately 4 × 4 | L1 Daily schedule; L2 Points and categories; L3 Monitoring and devices |

The launcher determines actual dimensions. Resize using its handles. Layouts reduce their content density at smaller sizes and larger font scales.

## Configuration

Long-press a placed widget and select **Configure**, where supported, or open the Widgets screen and select its instance number. Choose a type within that size family, details, category summaries, score progress, compactness, dark/light theme, and optional battery information for L3.

Preferences belong to individual widget IDs. **Reset Widget Settings** restores widget appearance/content defaults; it does not reset points, proofs, schedules, attendance or other app settings.

The controls adapt to the selected layout: category summaries appear for M2/L2, score progress for points widgets, compact presentation for medium/large widgets, and optional battery percentage for L3. Changing an option updates the selected preview without rebuilding the gallery. Preview cards do not open the main app. Unsaved choices and the scroll position survive activity recreation; Cancel preserves the saved instance settings. Returning from an instance editor refreshes the placed-widget list and saved-data previews.

An existing instance can change type within its size family. To change from small to medium or large, add the corresponding widget through the launcher. If a launcher rejects or cannot open a pin request, use its standard Widgets picker.

## Data and refresh behavior

- Score: the existing production `getScore(date, false)` method. Category totals: the existing Points Breakdown method. No Taufa score or threshold-test simulation is used as the account balance.
- Tasks: today's saved `scheduled_activities` rows. Widgets never create tasks or run schedule reconciliation. Cancelled occurrences are removed and IDs are deduplicated. Existing `WindowStateRules` supplies time-dependent states.
- Bath: `BathScheduleRecord` keeps activity, assigned attendance and photo submission windows distinct. Gym uses persisted activity endpoints and proof windows.
- Score privileges: the existing restriction decision function and live GYM music lease check. A score threshold is not a claim that a single-video reward voucher is unconsumed. Live single-video allowance is not cached by the existing reward client; check Rewards for that state.
- Monitoring: actual Accessibility connection, Usage Access, consent/selection, service and financial pause state. PC/browser live status remains explicitly unknown. A running service is not proof of an individual application's restriction being applied.
- Connectivity: Android's validated-network status. Optional battery percentage uses the system's sticky battery broadcast.
- Errors: retain the last display snapshot with a stale label, or show an unavailable placeholder. Widgets make no remote requests.

Updates coalesce app events, target the relevant domain or instance, and share one local snapshot. One non-wakeup, inexact alarm covers all instances. Countdown widgets request approximately five-minute refreshes and saved-window boundary refreshes; idle widgets use thirty-minute refreshes. Android widget periodic updates provide a thirty-minute fallback. Displayed countdowns are recalculated from timestamps at refresh; no second-based ticker, exact alarm, standalone foreground service, or wake lock is added.

Android may delay inexact/background work, particularly in Doze. Check the displayed update time. An old snapshot is not a continuously refreshed live monitor.

## Integration

The existing `StrictRecordsWidgetProvider` component remains M1 so previously placed instances retain their IDs. Eight additional provider components expose the other types. All providers share the same repository, renderer, preference store and update coordinator.

Android 7.0/API 24 remains supported. Pin requests require Android 8.0/API 26 and a compatible launcher. Android 12+ supports the declared reconfiguration and layout preview features; older launchers show the drawable preview and can reconfigure from the app's Widgets screen.

## Verification

- JVM tests: `StrictWidgetTest` covers countdown/window boundaries, terminal states, cancellation/deduplication, configuration serialization, multiple instances, reset isolation and ID restoration.
- Resource/source contracts: `python -m unittest tests.test_android_widgets_contract -v`.
- Debug emulator integration: the test APK's existing instrumentation runner accepts `-e mode widgets`. `StrictWidgetDeviceRegression` binds/renders all types in a debug-only `AppWidgetHost`, changes dimensions, exercises the actual configuration activity, verifies the real score and removes its test IDs. This host is excluded from release builds. Its report distinguishes a framework host from the launcher's home-screen grid.
- Configuration instrumentation additionally checks progress/category/detail previews, battery visibility, Save/reopen, cancelled drafts, instance isolation, gallery view reuse, and updated placed-instance labels on return. These checks require a rebuilt test APK and emulator execution; source compilation alone does not verify their runtime behavior.
- Launcher placement, pin confirmation, navigation, reboot behavior and actual power use require separate runtime checks. See the dated delivery report for checks actually completed.

Android references: [widget updates](https://developer.android.com/develop/ui/views/appwidgets/advanced), [configuration](https://developer.android.com/develop/ui/views/appwidgets/configuration), [launcher pinning](https://developer.android.com/develop/ui/views/appwidgets/discoverability).
