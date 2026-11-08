import json

path = 'browser_extension/manifest.json'
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

if "identity" not in data.get("permissions", []):
    data.setdefault("permissions", []).append("identity")

hosts = data.setdefault("host_permissions", [])
for h in ["https://www.googleapis.com/*", "https://accounts.google.com/*"]:
    if h not in hosts:
        hosts.append(h)

data["oauth2"] = {
    "client_id": "891283897103-strictrecords.apps.googleusercontent.com",
    "scopes": [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/drive.file"
    ]
}

with open(path, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)

print("Updated browser_extension/manifest.json with identity and OAuth2 scopes")
