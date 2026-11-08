"""Fast isolated JVM rig tests; no APK build, device or application data access."""
from pathlib import Path
import os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
CACHE=Path.home()/'.gradle/caches/modules-2/files-2.1'
def jar(group,name,version=None):
    base=CACHE/group/name
    matches=sorted((base/version).rglob('*.jar')) if version else sorted(base.rglob('*.jar'))
    if not matches:raise RuntimeError('Missing cached '+name)
    return str(matches[-1])
stdlib=jar('org.jetbrains.kotlin','kotlin-stdlib','1.9.0')
compiler=[jar('org.jetbrains.kotlin','kotlin-compiler-embeddable','1.9.0'),stdlib,
    jar('org.jetbrains.kotlin','kotlin-script-runtime','1.9.0'),
    jar('org.jetbrains.kotlin','kotlin-reflect'),jar('org.jetbrains.intellij.deps','trove4j'),
    jar('org.jetbrains','annotations')]
junit=jar('junit','junit','4.13.2');hamcrest=jar('org.hamcrest','hamcrest-core')
work=ROOT/'.codex-work/robot-rig-20261031';work.mkdir(parents=True,exist_ok=True)
output=work/'rig-tests.jar'
java='C:/Program Files/Java/jdk-11/bin/java.exe'
sources=[ROOT/'android/app/src/main/java/com/strictrecords/app/PremiumBotMotion.kt',
    ROOT/'android/app/src/test/java/com/strictrecords/app/PremiumBotMotionTest.kt']
subprocess.run([java,'-Xmx512m','-cp',os.pathsep.join(compiler),'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler',
    '-jvm-target','11','-no-stdlib','-no-reflect','-classpath',os.pathsep.join([stdlib,junit,hamcrest]),
    '-d',str(output),*map(str,sources)],cwd=ROOT,check=True)
raise SystemExit(subprocess.run([java,'-cp',os.pathsep.join([str(output),stdlib,junit,hamcrest]),
    'org.junit.runner.JUnitCore','com.strictrecords.app.PremiumBotMotionTest'],cwd=ROOT).returncode)
