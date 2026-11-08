"""Replace only view modules in the installed EXE; retain every backend byte."""
import hashlib
import filecmp
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zlib

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter,ZlibArchiveWriter
from PyInstaller.utils.win32.winutils import update_exe_pe_checksum

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports/strict-focus-frontend-20261103'
RELEASE=ROOT/('releases/StrictRecords-FocusFrontend-20261103-r5/Windows/StrictRecords' if '--revision-5' in sys.argv else 'releases/StrictRecords-FocusFrontend-20261103-r4/Windows/StrictRecords' if '--revision-4' in sys.argv else 'releases/StrictRecords-FocusFrontend-20261103-r3/Windows/StrictRecords' if '--revision-3' in sys.argv else 'releases/StrictRecords-FocusFrontend-20261103-r2/Windows/StrictRecords' if '--revision-2' in sys.argv else 'releases/StrictRecords-FocusFrontend-20261103/Windows/StrictRecords')
WORK=ROOT/'.codex-work/focus-frontend-20261103'
MODULES=('windows.embedded_strict_focus','windows.strict_focus_layout','windows.floating_camera_preview',
         'windows.strict_focus_chrome','windows.strict_focus_preferences','windows.strict_focus_badges','windows.strict_browser_pane',
         'windows.camera_preview_geometry','windows.strict_layout_toolbar','windows.strict_focus_ui_checks','windows.desktop_overlay_validation','core.ui_dashboard')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    REPORT.mkdir(parents=True,exist_ok=True);WORK.mkdir(parents=True,exist_ok=True);RELEASE.mkdir(parents=True,exist_ok=True)
    base=WORK/'installed-base.exe'
    if not base.exists():shutil.copy2(ROOT/'.dist/StrictRecords.exe',base)
    archive=CArchiveReader(str(base));original=archive.open_embedded_archive('PYZ.pyz')
    source_hashes={};code={};entries=[]
    for name in sorted(set(original.toc)|set(MODULES)):
        kind=original.toc[name][0] if name in original.toc else 0
        source='-' if kind==3 else str(Path(*name.split('.'))/ '__init__.py') if kind==1 else str(Path(*name.split('.')))+'.py'
        entries.append((name,source,'PYMODULE'))
        if name in MODULES:
            if name=='core.ui_dashboard':
                from types import CodeType
                replacements=[0]
                def patch_titlebar(value):
                    constants=[]
                    for item in value.co_consts:
                        if isinstance(item,CodeType):item=patch_titlebar(item)
                        elif isinstance(item,str):
                            replacements[0]+=item.count('class="pc-topbar pywebview-drag-region"')
                            item=item.replace('class="pc-topbar pywebview-drag-region"','class="pc-topbar"')
                        constants.append(item)
                    return value.replace(co_consts=tuple(constants))
                code[name]=patch_titlebar(original.extract(name))
                assert replacements[0]==1
                source_hashes['core/ui_dashboard.py']='installed bytecode; titlebar drag-class removal only'
                continue
            file=ROOT/source;source_hashes[source]=digest(file)
            code[name]=compile(file.read_text(encoding='utf-8'),source,'exec')
    # Copy every non-view module as its original serialized code, rather than
    # importing/recompiling the current checkout's independently edited backend.
    class FrontendPYZ(ZlibArchiveWriter):
        @classmethod
        def _write_entry(cls,fp,entry,code_dict):
            name=entry[0]
            if name in MODULES:return super()._write_entry(fp,entry,code_dict)
            kind=original.toc[name][0]
            if kind==3:return name,(kind,fp.tell(),0)
            raw=original.extract(name,raw=True);blob=zlib.compress(raw,cls._COMPRESSION_LEVEL)
            offset=fp.tell();fp.write(blob);return name,(kind,offset,len(blob))
    pyz=WORK/'frontend.pyz';FrontendPYZ(str(pyz),entries,code_dict=code)
    toc=[]
    for name,row in archive.toc.items():
        path=pyz if row[-1]=='z' else WORK/(name+'.bin')
        if row[-1]!='z':path.write_bytes(archive.extract(name))
        toc.append((name,str(path),bool(row[-2]),row[-1]))
    toc.extend((name,None,False,'o') for name in archive.options)
    class RawCArchive(CArchiveWriter):
        def _write_entry(self,fp,entry):
            name,source,compress,kind=entry
            if kind=='o':return self._write_blob(fp,b'',name,kind)
            return self._write_file(fp,source,name,kind,compress=compress)
    package=WORK/'frontend.pkg';RawCArchive(str(package),toc,'python314.dll')
    target=RELEASE/'StrictRecords.exe'
    with target.open('wb') as output:
        output.write(base.read_bytes()[:archive._start_offset]);output.write(package.read_bytes())
    update_exe_pe_checksum(str(target))
    updated=CArchiveReader(str(target));updated_pyz=updated.open_embedded_archive('PYZ.pyz')
    unchanged={name:original.extract(name,raw=True)==updated_pyz.extract(name,raw=True)
               for name in original.toc if name not in MODULES and original.toc[name][0]!=3}
    bootstrap_unchanged=all(archive.extract(name)==updated.extract(name) for name,row in archive.toc.items() if row[-1]!='z')
    assert bootstrap_unchanged and all(unchanged.values())
    def copy(source,destination):
        destination=Path(destination)
        if destination.exists() and filecmp.cmp(source,destination,shallow=False):return str(destination)
        return shutil.copy2(source,destination)
    shutil.copytree(ROOT/'.dist/_internal',RELEASE/'_internal',dirs_exist_ok=True,copy_function=copy)
    runtime_identical=all(filecmp.cmp(p,RELEASE/'_internal'/p.relative_to(ROOT/'.dist/_internal'),shallow=False)
                          for p in (ROOT/'.dist/_internal').rglob('*') if p.is_file())
    assert runtime_identical
    report=dict(base_sha256=digest(base),exe_sha256=digest(target),frontend_modules=list(MODULES),runtime_files_byte_identical=runtime_identical,
                source_sha256=source_hashes,unchanged_non_frontend_modules=len(unchanged),
                all_non_frontend_modules_byte_identical=all(unchanged.values()),bootstrap_byte_identical=bootstrap_unchanged)
    (REPORT/'package-preservation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    completed=subprocess.run([str(target),'--verify-build-report',str(REPORT/'frozen-verification.json')],cwd=ROOT,timeout=90)
    assert completed.returncode==0 and json.loads((REPORT/'frozen-verification.json').read_text())['success']
    print(json.dumps(report))


if __name__=='__main__':main()
