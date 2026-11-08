"""Set an owner-provided public Chrome-extension OAuth client ID before packaging."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re

parser=argparse.ArgumentParser()
parser.add_argument('--client-id',required=True)
parser.add_argument('--manifest',default='browser_extension/manifest.json')
args=parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_-]+\.apps\.googleusercontent\.com',args.client_id):
    parser.error('Supply only the public OAuth client ID, never a JSON secret document.')
path=Path(args.manifest)
document=json.loads(path.read_text())
digest=hashlib.sha256(base64.b64decode(document['key'])).hexdigest()[:32]
extension_id=''.join(chr(ord('a')+int(char,16)) for char in digest)
document['oauth2']={'client_id':args.client_id,'scopes':['openid','email','profile']}
path.write_text(json.dumps(document,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'extension_id':extension_id,'public_client_id_configured':True,
                  'registered_client_type_and_extension_id_must_match':True,'drive_scope_requested_separately':True}))
