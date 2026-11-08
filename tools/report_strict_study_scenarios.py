"""Generate deterministic timing evidence in disposable databases, not user data."""
import json
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.database import StrictRecordsDB
from windows.strict_study_workflow import StrictStudyWorkflow
from tests.test_strict_study_workflow import Clock,window,advance

rows=[]
for minutes in (20,25,30,34,35,37,38,39,40,50):
    with tempfile.TemporaryDirectory(prefix='strict-study-report-') as temp:
        path=Path(temp);db=StrictRecordsDB(str(path/'fixture.db'));clock=Clock()
        workflow=StrictStudyWorkflow(db,clock=clock.wall,monotonic=clock.monotonic)
        rig=(db,clock,workflow,path);opening=window(rig,minutes)
        workflow.begin('engineering-timing-fixture','user_lok','fixture-android')
        target=workflow.s['focus_target_seconds'];started=clock.now
        while not workflow.s['focus_completed_at']:advance(rig,10)
        with db._connect() as conn:
            events=[dict(r) for r in conn.execute('SELECT state,at,details_json FROM strict_study_transitions ORDER BY sequence')]
        rows.append(dict(started_at=started.isoformat(),actual_scheduled_window=opening.isoformat(),
            window_in_minutes=minutes,calculated_base_focus_seconds=target,
            observed_focus_seconds=workflow.s['elapsed_seconds'],completed_at=workflow.s['focus_completed_at'],
            remaining_until_actual_window_seconds=workflow.view()['window_remaining_seconds'],
            end_enabled=workflow.view()['end_enabled'],transitions=events))
destination=ROOT/'reports/strict-study-20261102/timing-scenarios.json'
destination.write_text(json.dumps(dict(method='Virtual clock with continuous simulated monitoring; actual workflow and database schedule reads. No physical device or real attendance.',scenarios=rows),indent=2),encoding='utf-8')
print(destination)
