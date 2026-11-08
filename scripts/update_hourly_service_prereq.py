import sys

path = 'core/hourly_submission_service.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target = '''        gate_date = gate_activity["date"]
        gate = gate_service.reconcile(gate_date, account_id, received, is_dev_mode)'''

replacement = '''        gate_date = gate_activity["date"]
        gate = gate_service.reconcile(gate_date, account_id, received, is_dev_mode)

        if expected_type in ("HOURLY", "BATH") and not (gate.get("active") or gate.get("hardware_passed")):
            raise HourlySubmissionError(
                "Complete Wake-Up Verification first. Today's required wake-up verification is still pending. Complete it before proceeding with this activity.",
                status_code=403,
                code="WAKEUP_VERIFICATION_REQUIRED"
            )'''

assert target in content, "target not found in hourly_submission_service.py"
content = content.replace(target, replacement, 1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated hourly_submission_service.py with Wake-Up prerequisite check")
