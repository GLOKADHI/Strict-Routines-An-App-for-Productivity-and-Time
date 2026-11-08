with open('android/app/src/main/java/com/strictrecords/app/WakeUpVerificationController.kt', 'r', encoding='utf-8') as f:
    text = f.read()

# Fix inside apply blocks:
text = text.replace('step2Header.text = if (step2Done) {\n                "Earbud audio output verified:', 'text = if (step2Done) {\n                "Earbud audio output verified:')
text = text.replace('step2Header.text = if (step2Done) "RE-VERIFY ROUTE" else "Connect Earbuds', 'text = if (step2Done) "RE-VERIFY ROUTE" else "Connect Earbuds')
text = text.replace('val step2Header = TextView(activity).apply {\n            step2Header.text =', 'val step2Header = TextView(activity).apply {\n            text =')
text = text.replace('val step3Header = TextView(activity).apply {\n            step3Header.text =', 'val step3Header = TextView(activity).apply {\n            text =')

with open('android/app/src/main/java/com/strictrecords/app/WakeUpVerificationController.kt', 'w', encoding='utf-8') as f:
    f.write(text)

print("Rewrote WakeUpVerificationController.kt cleanly")
