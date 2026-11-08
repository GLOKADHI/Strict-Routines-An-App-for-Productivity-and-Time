"""Rebuild the Windows StrictBot presentation library with .NET Framework."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent
FRAMEWORK = Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319')
REFERENCES = ['/r:' + str(FRAMEWORK / 'WPF' / name) for name in
              ('PresentationFramework.dll', 'PresentationCore.dll', 'WindowsBase.dll', 'System.Speech.dll')]
REFERENCES += ['/r:System.Xaml.dll', '/r:System.Web.Extensions.dll']

if __name__ == '__main__':
    target = ROOT / 'assets' / 'StrictBotNative.dll'
    target.parent.mkdir(parents=True, exist_ok=True)
    sources = [str(SOURCE / name) for name in
               ('NativeGeometry.cs', 'NativeMotion.cs', 'StrictBotRig.cs', 'NativeHost.cs', 'NativeSettings.cs', 'NativeBoard.cs', 'NativeNight.cs', 'NativeOverlays.cs', 'NativeStudy.cs')]
    subprocess.run([str(FRAMEWORK / 'csc.exe'), '/nologo', '/optimize+',
                    '/target:library', '/out:' + str(target), *REFERENCES, *sources],
                   cwd=ROOT, check=True)
    print(target)
