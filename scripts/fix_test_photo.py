import io
import sys
from PIL import Image

path = 'tests/test_college_schedule_invariance_and_wakeup_optional.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target = 'file_bytes=b"fake_image_bytes_for_test_12345",'
replacement = '''file_bytes=self.sample_photo,'''

target_setup = 'self.db.ensure_daily_record(self.date_str)'
replacement_setup = '''self.db.ensure_daily_record(self.date_str)
        buf = io.BytesIO()
        Image.new("RGB", (120, 120), (50, 100, 150)).save(buf, format="JPEG")
        self.sample_photo = buf.getvalue()'''

content = content.replace(target, replacement)
content = content.replace(target_setup, replacement_setup)

# Ensure io and Image are imported
if "from PIL import Image" not in content:
    content = "import io\nfrom PIL import Image\n" + content

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated test with valid sample photo")
