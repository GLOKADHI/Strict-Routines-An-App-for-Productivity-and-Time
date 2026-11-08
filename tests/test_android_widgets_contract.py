"""Source/resource contracts. These do not substitute for launcher/device tests."""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'android/app/src/main'
JAVA = APP / 'java/com/strictrecords/app'
RES = APP / 'res'
A = '{http://schemas.android.com/apk/res/android}'
KINDS = ('s1','s2','s3','s4','m1','m2','l1','l2','l3')

class WidgetContracts(unittest.TestCase):
    def source(self, name): return (JAVA / (name+'.kt')).read_text(encoding='utf-8-sig')
    def test_nine_discoverable_widgets(self):
        manifest = ET.parse(APP/'AndroidManifest.xml').getroot()
        widgets = [r for r in manifest.findall('application/receiver') if any(m.get(A+'name') == 'android.appwidget.provider' for m in r.findall('meta-data'))]
        self.assertEqual(9,len(widgets))
        for w in widgets:
            self.assertEqual('false',w.get(A+'exported'))
            self.assertTrue(w.get(A+'label','').startswith('@string/widget_'))
    def test_every_metadata_config_preview_and_resize(self):
        for k in KINDS:
            meta=ET.parse(RES/'xml'/f'strict_widget_{k}_info.xml').getroot()
            self.assertEqual('1800000',meta.get(A+'updatePeriodMillis'))
            self.assertEqual('horizontal|vertical',meta.get(A+'resizeMode'))
            self.assertEqual('reconfigurable',meta.get(A+'widgetFeatures'))
            self.assertEqual('com.strictrecords.app.StrictWidgetConfigurationActivity',meta.get(A+'configure'))
            self.assertTrue((RES/'layout'/f'strict_widget_preview_{k}.xml').is_file())
            self.assertEqual('2' if k.startswith('s') else '4',meta.get(A+'targetCellWidth'))
            self.assertEqual('4' if k.startswith('l') else '2',meta.get(A+'targetCellHeight'))
    def test_remoteviews_only_supported_views(self):
        for path in (RES/'layout').glob('strict_widget_*.xml'):
            root=ET.parse(path).getroot()
            self.assertTrue(all(e.tag in {'LinearLayout','TextView','ProgressBar'} for e in root.iter()),path)
    def test_no_fake_picker_score_or_times(self):
        for path in (RES/'layout').glob('strict_widget_*.xml'):
            text=path.read_text(encoding='utf-8')
            self.assertNotIn('ONLINE',text)
            self.assertNotIn('14:50',text)
            self.assertNotIn('32 /100',text)
    def test_existing_widget_component_preserved(self):
        self.assertIn('open class StrictRecordsWidgetProvider',self.source('StrictRecordsWidgetProvider'))
        self.assertIn('override fun onRestored',self.source('StrictRecordsWidgetProvider'))
    def test_read_only_existing_sources(self):
        source=self.source('StrictWidgetRepository')
        self.assertIn('db.getScore(date, false)',source)
        self.assertIn('db.getPointsBreakdown(date,false)',source)
        self.assertIn('BathScheduleRecord.fromSaved',source)
        self.assertIn('DisciplineScoringRules.evaluateRestrictionDecision',source)
        self.assertNotRegex(source,r'db\.(getDailyActivities|getDayState|setConfigValue|enqueueSync|addScoreEvent)\(')
        self.assertNotIn('writableDatabase',source)
        self.assertNotRegex(source,r'"(?:INSERT|UPDATE|DELETE|CREATE)\s')
    def test_no_widget_network_requests(self):
        sources='\n'.join(p.read_text(encoding='utf-8-sig') for p in JAVA.glob('StrictWidget*.kt'))
        self.assertNotIn('HttpURLConnection',sources)
        self.assertNotIn('fetchRewardStatus(',sources)
        self.assertNotIn('GymPolicyClient.refresh(',sources)
    def test_no_seconds_ticker_or_foreground_service(self):
        source=self.source('StrictRecordsWidgetProvider')+self.source('StrictWidgetModel')+self.source('StrictWidgetRenderer')
        for forbidden in ['setExact','setRepeating','setAndAllowWhileIdle','startForeground','newWakeLock','Chronometer','hh:mm:ss','remaining_seconds']:
            self.assertNotIn(forbidden,source)
        self.assertIn('AlarmManager.RTC,',source)
        self.assertIn('setWindow(',source)
        self.assertEqual(1,source.count('setWindow('))
    def test_instance_settings_reset_scoped(self):
        source=self.source('StrictWidgetStore')
        self.assertIn('strict_home_widgets_v1',source)
        self.assertIn('instance_$id',source)
        self.assertNotIn('prefs.edit().clear()',source)
        self.assertNotIn('StrictRecordsLocalDb',source)
    def test_pinning_requires_user_request_and_confirmation(self):
        source=self.source('StrictWidgetConfigurationActivity')
        self.assertIn('isRequestPinAppWidgetSupported',source)
        self.assertIn('Request sent. Confirm placement',source)
        self.assertIn('EXTRA_APPWIDGET_ID',source)
        self.assertIn('defaultKind(context,id) ?: return',source)
        manifest=ET.parse(APP/'AndroidManifest.xml').getroot()
        callback=next(r for r in manifest.findall('application/receiver') if r.get(A+'name')=='.StrictWidgetPinReceiver')
        self.assertEqual('false',callback.get(A+'exported'))
    def test_settings_entry_and_valid_navigation(self):
        self.assertIn('Triple("▦ Widgets"',self.source('MainActivity'))
        self.assertIn('StrictWidgetConfigurationActivity::class.java',self.source('MainActivity'))
        self.assertIn('putExtra("OPEN_TAB",k.tab)',self.source('StrictWidgetRenderer'))
        self.assertNotIn('"FOCUS"',self.source('StrictRecordsWidgetProvider'))
    def test_configuration_updates_only_selected_preview_on_toggle(self):
        source=self.source('StrictWidgetConfigurationActivity')
        listener=source.split('setOnCheckedChangeListener',1)[1].split('\n    }',1)[0]
        self.assertIn('updatePreview(selectedPreview,selected)',listener)
        self.assertNotIn('render()',listener)
        self.assertIn('override fun onResume()',source)
        self.assertIn('refreshPlacedWidgets()',source)
        self.assertIn('out.putInt("scroll_y",scroll.scrollY)',source)
    def test_pinning_failure_isolated_and_callback_bounded(self):
        source=self.source('StrictWidgetConfigurationActivity')
        self.assertIn('runCatching { requestPin() }',source)
        self.assertIn('handler.postDelayed(release,8_000L)',source)
        self.assertIn('released.compareAndSet(false,true)',source)
        self.assertIn('finally { handler.removeCallbacks(release); release.run() }',source)
    def test_configuration_has_no_ineffective_small_category_option(self):
        source=self.source('StrictWidgetConfigurationActivity')
        self.assertIn('selected.kind in setOf(StrictWidgetKind.M2,StrictWidgetKind.L2)',source)
        self.assertIn('if(!selected.kind.name.startsWith("S")) optionPanel.addView(toggle("Compact presentation"',source)
        self.assertIn('.forProvider(fallback)',self.source('StrictWidgetStore'))
    def test_unknown_stale_never_healthy(self):
        source=self.source('StrictWidgetRenderer')+self.source('StrictWidgetRepository')
        self.assertIn('PC / browser: live status unknown',source)
        self.assertIn('STALE · Open app to refresh',source)
        self.assertIn('Live status unknown',source)
        self.assertIn('Current window state unavailable',source)
        self.assertIn('details.optBoolean("is_demo")',source)
    def test_event_domains_and_removal(self):
        source=self.source('StrictRecordsWidgetProvider')
        self.assertIn('store.load(id,fallback).kind.domain in domains',source)
        self.assertIn('StrictWidgetStore(context).remove(it)',source)
        self.assertIn('if (all.isEmpty()) { cancel(context); return }',source)
        self.assertIn('Intent(context,StrictRecordsWidgetProvider::class.java).setAction(ACTION_REFRESH)',source)

if __name__ == '__main__': unittest.main()
