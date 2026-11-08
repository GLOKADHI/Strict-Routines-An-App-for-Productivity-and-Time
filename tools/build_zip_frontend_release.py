"""Package the supplied React frontend without rebuilding backend modules."""
import filecmp
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zlib
from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter
from PyInstaller.utils.win32.winutils import update_exe_pe_checksum

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports/strict-session-zip-20261103'
RELEASE=ROOT/'releases/StrictRecords-ZipFrontend-20261103/Windows/StrictRecords'
WORK=ROOT/'.codex-work/zip-frontend-20261103'
MODULES=('windows.embedded_strict_focus','windows.strict_zip_frontend','windows.strict_zip_state','windows.desktop_overlay_validation')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root):
    return {p.relative_to(root).as_posix():digest(p) for p in sorted(root.rglob('*')) if p.is_file()}


def main():
    REPORT.mkdir(parents=True,exist_ok=True);WORK.mkdir(parents=True,exist_ok=True);RELEASE.mkdir(parents=True,exist_ok=True)
    base=WORK/'installed-base.exe'
    if not base.exists():shutil.copy2(ROOT/'.dist/StrictRecords.exe',base)
    archive=CArchiveReader(str(base));original=archive.open_embedded_archive('PYZ.pyz')
    source_hashes={};code={};entries=[]
    for name in sorted(set(original.toc)|set(MODULES)):
        kind=original.toc[name][0] if name in original.toc else 0
        source='-' if kind==3 else str(Path(*name.split('.'))/'__init__.py') if kind==1 else str(Path(*name.split('.')))+'.py'
        entries.append((name,source,'PYMODULE'))
        if name in MODULES:
            file=ROOT/source;source_hashes[source]=digest(file)
            code[name]=compile(file.read_text(encoding='utf-8'),source,'exec')
            selected=globals().get('PATCH_METHODS',{}).get(name)
            if selected:
                from types import CodeType
                replacements={}
                def collect(value):
                    if value.co_qualname in selected:replacements[value.co_qualname]=value
                    for item in value.co_consts:
                        if isinstance(item,CodeType):collect(item)
                collect(code[name]);assert set(replacements)==set(selected)
                def patch(value):
                    if value.co_qualname in replacements:return replacements[value.co_qualname]
                    return value.replace(co_consts=tuple(patch(item) if isinstance(item,CodeType) else item for item in value.co_consts))
                code[name]=patch(original.extract(name))
    class FrontendPYZ(ZlibArchiveWriter):
        @classmethod
        def _write_entry(cls,fp,entry,code_dict):
            name=entry[0]
            if name in MODULES:return super()._write_entry(fp,entry,code_dict)
            kind=original.toc[name][0]
            if kind==3:return name,(kind,fp.tell(),0)
            blob=zlib.compress(original.extract(name,raw=True),cls._COMPRESSION_LEVEL)
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
    bootstrap=all(archive.extract(name)==updated.extract(name) for name,row in archive.toc.items() if row[-1]!='z')
    assert bootstrap and all(unchanged.values())
    def copy(source,destination):
        destination=Path(destination)
        if destination.exists() and filecmp.cmp(source,destination,shallow=False):return str(destination)
        return shutil.copy2(source,destination)
    shutil.copytree(ROOT/'.dist/_internal',RELEASE/'_internal',dirs_exist_ok=True,copy_function=copy)
    runtime_identical=all(filecmp.cmp(p,RELEASE/'_internal'/p.relative_to(ROOT/'.dist/_internal'),shallow=False)
                          for p in (ROOT/'.dist/_internal').rglob('*') if p.is_file())
    assert runtime_identical
    frontend=ROOT/'strictfocus/imported-session-ui/dist';assert (frontend/'index.html').is_file()
    assets=RELEASE/'_internal/strict_session_frontend'
    if assets.exists():
        # Only this dedicated generated subtree can be cleaned between builds.
        assert assets.resolve().is_relative_to(RELEASE.resolve())
        shutil.rmtree(assets)
    shutil.copytree(frontend,assets)
    extra_hashes={}
    for relative in globals().get('EXTRA_ASSETS',()):
        source=ROOT/relative;destination=RELEASE/'_internal'/relative
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
        extra_hashes[relative]=digest(source)
    assets_hashes=tree_hashes(assets)
    report=dict(base_sha256=digest(base),exe_sha256=digest(target),frontend_modules=list(MODULES),
        bootstrap_byte_identical=bootstrap,all_other_modules_byte_identical=all(unchanged.values()),
        unchanged_modules=len(unchanged),existing_runtime_byte_identical=runtime_identical,
        source_sha256=source_hashes,frontend_assets=assets_hashes,extra_assets=extra_hashes,
        frontend_asset_manifest_sha256=hashlib.sha256(json.dumps(assets_hashes,sort_keys=True).encode()).hexdigest())
    (REPORT/'package-preservation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    subprocess.run([str(target),'--verify-build-report',str(REPORT/'frozen-verification.json')],cwd=ROOT,timeout=90,check=True)
    assert json.loads((REPORT/'frozen-verification.json').read_text())['success']
    print(json.dumps({key:value for key,value in report.items() if key not in ('source_sha256','frontend_assets')}))


if __name__=='__main__':main()
