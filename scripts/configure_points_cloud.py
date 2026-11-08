"""Install an explicitly supplied cloud device binding using Windows DPAPI."""
import argparse,json,os
from pathlib import Path
from urllib.parse import urlsplit

def main():
    p=argparse.ArgumentParser();p.add_argument('--binding-file',required=True);a=p.parse_args()
    binding=json.loads(Path(a.binding_file).read_text(encoding='utf-8'))
    url=urlsplit(binding['endpoint'])
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('A dedicated HTTPS origin is required')
    if not all(isinstance(binding.get(k),str) and binding[k] for k in ['account_id','device_id','device_token']):raise ValueError('Incomplete account/device binding')
    import win32crypt
    encrypted=win32crypt.CryptProtectData(json.dumps(binding).encode(),'StrictRoutines points device',None,None,None,0)
    target=Path(os.environ['LOCALAPPDATA'])/'StrictRecords/PointsCloud/client.enc';target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_suffix('.tmp');temp.write_bytes(encrypted);os.replace(temp,target)
    print('Cloud points binding protected. Restart the app normally to activate it. No credentials printed.')

if __name__=='__main__':main()
