"""Release publishing cannot bypass the authenticated server."""

import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from tools import publish_apk_release


class TestReleasePublisherSecurity(unittest.TestCase):
    def test_missing_admin_session_stops_before_network_or_publication(self):
        with tempfile.TemporaryDirectory(prefix="release_publisher_") as temp_dir:
            apk_path = os.path.join(temp_dir, "test.apk")
            with open(apk_path, "wb") as apk:
                apk.write(b"test-apk")
            with patch.object(publish_apk_release.urllib.request, "urlopen") as urlopen:
                with self.assertRaises(SystemExit) as exc:
                    publish_apk_release.publish_release(
                        apk_path=apk_path,
                        version_name="1.0.0",
                        version_code=1,
                        notes="test",
                    )
            self.assertEqual(exc.exception.code, 1)
            urlopen.assert_not_called()

    def test_server_failure_never_falls_back_to_filesystem_publication(self):
        with tempfile.TemporaryDirectory(prefix="release_publisher_") as temp_dir:
            apk_path = os.path.join(temp_dir, "test.apk")
            with open(apk_path, "wb") as apk:
                apk.write(b"test-apk")
            with (
                patch.object(publish_apk_release.urllib.request, "urlopen", side_effect=URLError("offline")),
                patch("os.makedirs") as makedirs,
                patch("shutil.copy2") as copy2,
            ):
                with self.assertRaises(SystemExit) as exc:
                    publish_apk_release.publish_release(
                        apk_path=apk_path,
                        version_name="1.0.0",
                        version_code=1,
                        notes="test",
                        admin_session="temporary-admin-session-token",
                    )
            self.assertEqual(exc.exception.code, 1)
            makedirs.assert_not_called()
            copy2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
