import sys

path = 'tests/test_college_schedule_invariance_and_wakeup_optional.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('ctx.exception.message', 'str(ctx.exception)')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated ctx.exception.message -> str(ctx.exception)")
