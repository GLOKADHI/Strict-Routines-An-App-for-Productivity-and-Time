"""Check popup Kotlin sources against cached classes; never run Gradle or package/install an APK."""
import os
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    profile = Path(os.environ['USERPROFILE'])
    cache = profile / '.gradle/caches/modules-2/files-2.1'

    def jar(group, artifact, version):
        return next((cache / group / artifact / version).rglob('*.jar'))

    kotlin = [jar('org.jetbrains.kotlin', name, '1.9.0') for name in (
        'kotlin-compiler-embeddable', 'kotlin-stdlib', 'kotlin-script-runtime', 'kotlin-daemon-embeddable')]
    kotlin += [jar('org.jetbrains.kotlin', 'kotlin-reflect', '1.6.10'),
               jar('org.jetbrains.intellij.deps', 'trove4j', '1.0.20200330'),
               jar('org.jetbrains', 'annotations', '13.0')]
    junit = jar('junit', 'junit', '4.13.2')
    hamcrest = jar('org.hamcrest', 'hamcrest-core', '1.3')
    android = profile / 'AppData/Local/Android/Sdk/platforms/android-34/android.jar'
    existing = profile / '.strictrecords_build/app/tmp/kotlin-classes/debug'
    resources = profile / '.strictrecords_build/app/intermediates/compile_and_runtime_not_namespaced_r_class_jar/debug/R.jar'
    java = Path(os.environ.get('JAVA_HOME', r'C:\Program Files\Java\jdk-11')) / 'bin/java.exe'
    javac = java.with_name('javac.exe')
    for required in (android, existing, resources, java, javac):
        if not required.exists():
            raise SystemExit(f'Cached Android classes/SDK required for source-only checks: {required}')
    output = root / '.codex-work/android-popup-source-check'
    output.mkdir(parents=True, exist_ok=True)
    classpath = os.pathsep.join(map(str, [*kotlin, junit, hamcrest, android, existing, resources]))
    source_root = root / 'android/app/src/main/java/com/strictrecords/app'
    sources = [source_root / (name + '.kt') for name in (
        'RestrictionPopupSurface', 'StrictBotOverlayController', 'RestrictionLockActivity',
        'StrictAccessibilityService', 'StrictEnforcementService', 'PopupEntrySession', 'ForegroundWindowRules',
        'TargetAppMonitoringPolicy', 'StrictRecordsLocalEngine', 'DisciplinePermissionManager', 'PopupForegroundRecheck',
        'FinancialSafePause', 'BankingBreakPlan', 'BankingBreakController', 'StrictRecordsBootReceiver')]
    test_root = root / 'android/app/src/test/java/com/strictrecords/app'
    test_names = ('PopupEntrySessionTest', 'ForegroundWindowRulesTest', 'TargetAppMonitoringPolicyTest',
                  'PopupForegroundRecheckTest', 'BankingBreakPlanTest', 'FinancialSafePauseTest')
    sources += [test_root / (name + '.kt') for name in test_names]
    compile_result = subprocess.run([
        str(java), '-cp', os.pathsep.join(map(str, kotlin)), 'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler',
        '-no-stdlib', '-no-reflect', '-jvm-target', '11', '-classpath', classpath,
        '-d', str(output), *map(str, sources)
    ], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    print(compile_result.stdout.decode('utf-8', errors='replace'))
    if compile_result.returncode:
        raise SystemExit(compile_result.returncode)
    stubs = output / 'test-only-android-stubs'
    stubs.mkdir(exist_ok=True)
    subprocess.run([str(javac), '-d', str(stubs),
                    str(root / 'tests/android_jvm_stubs/android/util/Log.java')], cwd=root, check=True)
    result = subprocess.run([
        str(java), '-cp', str(stubs) + os.pathsep + str(output) + os.pathsep + classpath, 'org.junit.runner.JUnitCore',
        *['com.strictrecords.app.' + name for name in test_names]
    ], cwd=root, timeout=30)
    print('Source checks only: no Gradle task, APK packaging, signing, installation or app-data mutation.')
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
