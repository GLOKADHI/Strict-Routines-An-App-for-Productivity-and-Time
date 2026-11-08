"""Explicit, offline enrollment of the existing account; never run automatically."""
import argparse
from datetime import datetime,timedelta,timezone
import hashlib,json,os,secrets,sqlite3,uuid
from pathlib import Path
from urllib.parse import urlsplit

def main():
    parser=argparse.ArgumentParser(description='Prepare existing device hashes for the cloud operator and protect a Windows replica credential locally.')
    parser.add_argument('--database',required=True);parser.add_argument('--account',required=True)
    parser.add_argument('--authority-device',required=True);parser.add_argument('--endpoint',required=True)
    parser.add_argument('--provisioning-output',required=True);args=parser.parse_args()
    url=urlsplit(args.endpoint)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('Use the deployed HTTPS origin, with no path, credentials or query')
    conn=sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    rows=list(conn.execute('''SELECT c.device_id,c.token_hash,d.platform FROM device_credentials c JOIN devices d ON d.device_id=c.device_id
        WHERE c.account_id=? AND c.revoked_at IS NULL AND d.pairing_state!='REVOKED' AND d.status!='REVOKED' ''',(args.account,)))
    conn.close()
    if not any(row['device_id']==args.authority_device and str(row['platform']).upper().startswith('ANDROID') for row in rows):
        raise ValueError('Authority must be an existing registered Android device belonging to this account')
    expiry=int((datetime.now(timezone.utc)+timedelta(days=30)).timestamp())
    devices=[{'device_id':r['device_id'],'token_hash':r['token_hash'],'role':'AUTHORITY' if r['device_id']==args.authority_device else 'REPLICA','expires_at':expiry} for r in rows]
    token=secrets.token_urlsafe(48);device='windows_points_'+uuid.uuid4().hex
    devices.append({'device_id':device,'token_hash':hashlib.sha256(token.encode()).hexdigest(),'role':'REPLICA','expires_at':expiry})
    import win32crypt
    config={'endpoint':args.endpoint.rstrip('/'),'account_id':args.account,'device_id':device,'device_token':token}
    target=Path(os.environ['LOCALAPPDATA'])/'StrictRecords/PointsCloud/client.enc';target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():raise FileExistsError('A cloud binding exists; preserve it or explicitly rotate it with the operator before replacement')
    encrypted=win32crypt.CryptProtectData(json.dumps(config).encode(),'StrictRoutines points device',None,None,None,0)
    output=Path(args.provisioning_output);output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise FileExistsError('Provisioning output already exists')
    output.write_text(json.dumps({'account_id':args.account,'authority_device':args.authority_device,'devices':devices},indent=2))
    temporary=target.with_suffix('.tmp');temporary.write_bytes(encrypted);os.replace(temporary,target)
    print('Prepared device credential hashes and a DPAPI-protected Windows binding. Existing records/credentials unchanged. Import the provisioning file on the cloud host before enabling the client.')

if __name__=='__main__':main()
