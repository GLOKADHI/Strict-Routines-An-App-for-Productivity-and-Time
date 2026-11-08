"""Operator-only provisioning; no public account-enrollment or admin endpoint."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from .service import create_app,run_worker

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('worker');sub.add_parser('tick')
    provision=sub.add_parser('provision');provision.add_argument('--file',required=True)
    args=parser.parse_args()
    if args.action=='worker':run_worker();return
    app=create_app()
    if args.action=='tick':print(json.dumps(app.cycle()));return
    # Import hashes of existing StrictRecords device credentials, not user passwords.
    data=json.loads(Path(args.file).read_text(encoding='utf-8'))
    for device in data['devices']:
        app.store.provision(data['account_id'],device['device_id'],device['token_hash'],device['role'],device['expires_at'],authority_device=data['authority_device'])
    print('Provisioned existing account/device bindings; no raw credentials printed.')

if __name__=='__main__':main()
