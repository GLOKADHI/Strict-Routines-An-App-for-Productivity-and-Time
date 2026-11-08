import sys

path = 'tests/test_college_schedule_invariance_and_wakeup_optional.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target = '''        with self.assertRaises(HourlySubmissionError) as ctx:
            self.submission_service.submit_hourly_proof(
                activity_id=act_id,
                account_id=self.account_id,
                submission_id=f"sub_test_{act_id}",
                category="CODING",
                description="Completed python backend unit tests and architecture review.",
                file_bytes=b"fake_image_bytes_for_test_12345",
                filename="proof.jpg",
                received_at=ref_time,
            )'''

replacement = '''        with self.assertRaises(HourlySubmissionError) as ctx:
            self.submission_service.accept(
                {
                    "activity_id": act_id,
                    "submission_id": f"sub_test_{act_id}",
                    "category": "CODING",
                    "description": "Completed python backend unit tests and architecture review.",
                },
                file_bytes=b"fake_image_bytes_for_test_12345",
                filename="proof.jpg",
                account_id=self.account_id,
                device_id="test_device_01",
                platform="windows",
                received_at=ref_time,
            )'''

assert target in content
content = content.replace(target, replacement, 1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated test_college_schedule_invariance_and_wakeup_optional.py to use accept()")
