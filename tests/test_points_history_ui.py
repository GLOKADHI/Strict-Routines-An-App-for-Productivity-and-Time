import ast
from pathlib import Path
import re
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js is required for dashboard checks')
def test_points_history_freshness_and_offline_ui(tmp_path):
    root=Path(__file__).resolve().parents[1]
    tree=ast.parse((root/'core/ui_dashboard.py').read_text(encoding='utf-8-sig'))
    assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and any(getattr(t,'id',None)=='DASHBOARD_HTML' for t in n.targets))
    html=ast.literal_eval(assignment.value)
    runtime=tmp_path/'dashboard.js'
    runtime.write_text('\n'.join(re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>',html,re.S)),encoding='utf-8')
    result=subprocess.run([shutil.which('node'),str(root/'tests/points_history_ui_checks.cjs'),str(runtime)],cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'checks passed' in result.stdout
