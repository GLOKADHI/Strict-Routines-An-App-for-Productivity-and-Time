"""Workspace appearance preferences, isolated from session and score records."""
import json
import os
from pathlib import Path


def path():return Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'StrictRecords' / 'study_workspace_ui.json'


def load():
    try:
        value=json.loads(path().read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def save(layout):
    target=path()
    try:
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(ratio=layout.ratio,order=layout.order,active=layout.active,side_by_side=layout.side_by_side)),encoding='utf-8')
        os.replace(temporary,target)
    except OSError:pass
