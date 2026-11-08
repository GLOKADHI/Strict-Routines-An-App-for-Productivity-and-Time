"""One-time browser package preparation; no automatic extension installation."""
import argparse
import json
import os
from pathlib import Path
from windows.browser_monitor_package import prepare, extension_id

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-label', required=True, help='Chosen Chrome profile name and Profile Path from chrome://version')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--storage', type=Path, default=Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'StrictRecords/extension_monitor')
    parser.add_argument('--update-existing', action='store_true', help='Reuse existing profile credentials and timer state; write updated folders to a new output path')
    parser.add_argument('--browser', default='chrome', choices=['chrome','edge','brave','comet','vivaldi','opera','arc'])
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, args.storage, args.profile_label, update_existing=args.update_existing, browser_key=args.browser), indent=2))
