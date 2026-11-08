path = 'windows/strict_session_preparation.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace(
    "fresh=bool(seen and 0<=now-seen<20)",
    "fresh=bool(seen and 0<=now-seen<45)"
)

text = text.replace(
    "required=('camera','microphone','visible','secure_context','online') if role=='WINDOWS' else ('timer_ready','visible','online')",
    "required=('camera','visible','secure_context','online') if role=='WINDOWS' else ('timer_ready','visible','online')"
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)

print("Updated windows/strict_session_preparation.py successfully")
