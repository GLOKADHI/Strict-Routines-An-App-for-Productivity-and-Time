"""Static contracts for the Settings-only gym planner; no app or DB is started."""
from html.parser import HTMLParser
from pathlib import Path
import ast
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'core' / 'ui_dashboard.py').read_text(encoding='utf-8'))
HTML = next(ast.literal_eval(node.value) for node in TREE.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'DASHBOARD_HTML' for target in node.targets))
FEATURE = HTML.split('<!-- GYM_ROUTINE_START -->', 1)[1].split('<!-- GYM_ROUTINE_END -->', 1)[0]
SCRIPT = HTML.split('// GYM_ROUTINE_START', 1)[1].split('// GYM_ROUTINE_END', 1)[0]
CSS = HTML.split('/* GYM_ROUTINE_START */', 1)[1].split('/* GYM_ROUTINE_END */', 1)[0]


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if 'id' in attributes:
            self.ids.append(attributes['id'])


class GymRoutineUiContractTest(unittest.TestCase):
    def test_feature_is_inside_existing_settings(self):
        settings = HTML.index('<section class="app-page" id="page-settings">')
        feature = HTML.index('<!-- GYM_ROUTINE_START -->')
        self.assertLess(settings, feature)
        self.assertNotIn('</section>', HTML[settings:feature])
        self.assertNotIn('app-page', FEATURE)
        self.assertNotIn('nav-item', FEATURE)
        self.assertNotIn('Quick Actions', FEATURE)
        self.assertNotIn('Focus', FEATURE)

    def test_feature_ids_are_unique_and_javascript_references_exist(self):
        parser = IdCollector()
        parser.feed(HTML)
        feature_parser = IdCollector()
        feature_parser.feed(FEATURE)
        for identifier in feature_parser.ids:
            self.assertEqual(parser.ids.count(identifier), 1, identifier)
        for identifier in re.findall(r"getElementById\('([^']+)'\)", SCRIPT):
            self.assertIn(identifier, parser.ids)

    def test_month_calendar_has_seven_columns_and_text_markers(self):
        self.assertIn('repeat(7,minmax(0,1fr))', CSS)
        for marker in ('✓ Planned gym', '✕ Rest', '⏸ Paused', 'Verified = recorded workout'):
            self.assertIn(marker, FEATURE)
        self.assertIn("['Sun','Mon','Tue','Wed','Thu','Fri','Sat']", SCRIPT)
        self.assertIn("setAttribute('aria-current','date')", SCRIPT)
        self.assertIn("setAttribute('aria-pressed'", SCRIPT)

    def test_authoritative_api_revision_and_account_scoped_retry(self):
        self.assertIn("'/api/gym/routine?'", SCRIPT)
        self.assertIn("apiPost('/api/gym/routine',pending.command)", SCRIPT)
        self.assertIn('expected_revision:workflow.revision', SCRIPT)
        self.assertIn("gymRoutineStorageKey('pending',workflow.account_id)", SCRIPT)
        self.assertIn('pending.account_id!==gymRoutineSnapshot?.routine?.account_id', SCRIPT)
        self.assertIn('payload.routine.revision<knownRevision', SCRIPT)
        self.assertIn("stored?.command?.request_id===pending.command.request_id", SCRIPT)
        self.assertNotIn('setInterval(', SCRIPT)
        self.assertNotIn('setTimeout(', SCRIPT)

    def test_review_cancel_pause_and_resume_contracts(self):
        self.assertIn('<dialog id="gymRoutineDialog"', FEATURE)
        self.assertIn("GYM_ROUTINE_LAUNCH = '2026-10-25'", SCRIPT)
        self.assertIn("command.reason_category==='OTHER' && !command.note", SCRIPT)
        self.assertIn('command.reactivation_date<minimum', SCRIPT)
        self.assertIn('gymRoutineWorkflow=null', SCRIPT)
        self.assertIn("document.getElementById('gymRoutineDialog').close()", SCRIPT)
        self.assertNotIn('/api/gym/start', SCRIPT)
        self.assertNotIn('/api/gym_mode/activate', SCRIPT)

    def test_statistics_do_not_invent_sessions_points_or_streaks(self):
        self.assertIn('Array.isArray(stats.history)', SCRIPT)
        self.assertIn('Array.isArray(stats.daily)', SCRIPT)
        self.assertIn('stats.has_tracked_period===false', SCRIPT)
        self.assertIn("typeof stats.streak==='number'", SCRIPT)
        self.assertIn("typeof stats.provisional_points==='number'", SCRIPT)
        self.assertIn("stats.completion_rate*10)/10+'%'", SCRIPT)
        self.assertIn('entry.event_id', SCRIPT)
        self.assertIn('entry.verification_status', SCRIPT)
        self.assertIn('Future plans and missing records are not plotted.', SCRIPT)
        self.assertNotIn('.innerHTML', SCRIPT)
        self.assertIn("choice.decision.source==='ALTERNATE_DAY_ROUTINE'", HTML)


if __name__ == '__main__':
    unittest.main()
