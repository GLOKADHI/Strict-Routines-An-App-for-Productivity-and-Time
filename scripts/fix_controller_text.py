with open('android/app/src/main/java/com/strictrecords/app/WakeUpVerificationController.kt', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, l in enumerate(lines):
    if 'text = if (step2Done)' in l:
        lines[i] = l.replace('text = if (step2Done)', 'step2Header.text = if (step2Done)')
        print(f"Fixed line {i+1}: {lines[i].strip()}")
    elif 'text = if (step3Done)' in l:
        lines[i] = l.replace('text = if (step3Done)', 'step3Header.text = if (step3Done)')
        print(f"Fixed line {i+1}: {lines[i].strip()}")

with open('android/app/src/main/java/com/strictrecords/app/WakeUpVerificationController.kt', 'w', encoding='utf-8') as f:
    f.writelines(lines)
