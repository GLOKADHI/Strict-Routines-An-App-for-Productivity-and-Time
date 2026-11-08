"""Update existing unpacked copies without editing browser profiles or setup credentials."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'browser_extension'
REPORT=ROOT/'reports/extension-bath-fix-20261104'
FILES=('content.js','yt_main_world.js','service_worker.js')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def update(folder,group):
    if not (folder/'manifest.json').is_file() or not (folder/'content.js').is_file():return None
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if 'Strict' not in manifest.get('name',''):return None
    backup=REPORT/'extension-backups'/group/folder.parent.name/folder.name
    backup.mkdir(parents=True,exist_ok=True)
    for name in (*FILES,'manifest.json'):
        old=folder/name
        if old.is_file() and not (backup/name).exists():shutil.copy2(old,backup/name)
    for name in FILES:shutil.copy2(SOURCE/name,folder/name)
    required=manifest.get('host_permissions',[])
    if '<all_urls>' in required:
        manifest.pop('optional_host_permissions',None)
    if tuple(map(int,manifest['version'].split('.'))) < (1,8,6):manifest['version']='1.8.6'
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    assert all(digest(folder/name)==digest(SOURCE/name) for name in FILES)
    return dict(folder=str(folder),version=manifest['version'],files={name:digest(folder/name) for name in FILES})


def main():
    REPORT.mkdir(parents=True,exist_ok=True)
    rows=[]
    for folder in sorted((ROOT/'StrictRoutines-Browser-Extensions').glob('*/StrictRoutines')):
        row=update(folder,'workspace')
        if row:rows.append(row)
    persistent=Path.home()/'AppData/Local/StrictRecords/browser_extension'
    if persistent.is_dir():
        for folder in sorted(persistent.iterdir()):
            if folder.is_dir():
                row=update(folder,'installed')
                if row:rows.append(row)
    (REPORT/'extension-deployment.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    release=ROOT/'releases/extension-error-fix-20261104'
    release.mkdir(parents=True,exist_ok=True)
    archive=release/'StrictRoutines-extension-1.8.6.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(SOURCE.rglob('*')):
            if path.is_file() and not any(p in {'node_modules','.git'} for p in path.parts):
                z.write(path,path.relative_to(SOURCE))
    print(json.dumps(dict(updated_copies=len(rows),archive=str(archive),sha256=digest(archive))))


if __name__=='__main__':main()
