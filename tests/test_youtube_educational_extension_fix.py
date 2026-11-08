import unittest
import os

class TestYouTubeEducationalExtensionFix(unittest.TestCase):
    def test_content_js_button_visibility_rule(self):
        """Verifies that Request Educational Access button is displayed whenever YouTube is restricted."""
        content_path = os.path.join('browser_extension', 'content.js')
        with open(content_path, 'r', encoding='utf-8') as f:
            code = f.read()

        self.assertIn('id="requestEdu"', code)
        self.assertTrue('🎓 Request Educational Access' in code or '🎓 REQUEST EDUCATIONAL VIDEO' in code)
        self.assertIn('id="eduInputPanel"', code)
        self.assertIn('id="eduUrlInput"', code)
        self.assertIn('id="eduSubmitUrlBtn"', code)
        self.assertIn('const isYouTube = status.service === \'youtube\' ||', code)
        self.assertIn('ui.requestEdu.style.display = \'inline-flex\';', code)

    def test_extension_popup_settings_feature(self):
        """Verifies that extension_popup.html has the YouTube video request feature in Settings and Websites tabs."""
        popup_path = os.path.join('browser_extension', 'extension_popup.html')
        with open(popup_path, 'r', encoding='utf-8') as f:
            html = f.read()

        # Settings tab feature
        self.assertIn('id="settingsEduCard"', html)
        self.assertIn('id="settingsEduUrlInput"', html)
        self.assertIn('id="settingsEduSubmitBtn"', html)
        self.assertIn('id="settingsEduReviewPanel"', html)
        self.assertIn('id="settingsEduOpenBtn"', html)

        # Websites tab feature
        self.assertIn('id="statusEduUrlInput"', html)
        self.assertIn('id="statusEduSubmitBtn"', html)

    def test_educational_history_js_handler(self):
        """Verifies that educational_history.js wires up the evaluation handlers."""
        edu_js_path = os.path.join('browser_extension', 'educational_history.js')
        with open(edu_js_path, 'r', encoding='utf-8') as f:
            code = f.read()

        self.assertIn('settingsEduSubmitBtn', code)
        self.assertIn('statusEduSubmitBtn', code)
        self.assertIn('STRICTBOT_REQUEST_EDUCATIONAL_VIDEO', code)
        self.assertIn('extractYouTubeVideoId', code)
        self.assertIn('strictActiveEduVideo', code)

    def test_service_worker_local_session_check(self):
        """Verifies service_worker.js verifies active educational session in getRestriction."""
        sw_path = os.path.join('browser_extension', 'service_worker.js')
        with open(sw_path, 'r', encoding='utf-8') as f:
            code = f.read()

        self.assertIn('strictActiveEduVideo', code)
        self.assertIn('EDUCATIONAL_VIDEO', code)
        self.assertIn('extractYouTubeVideoId', code)

    def test_ui_css_styles(self):
        """Verifies ui.css contains edu-stage and edu-result-box styling."""
        css_path = os.path.join('browser_extension', 'ui.css')
        with open(css_path, 'r', encoding='utf-8') as f:
            css = f.read()

        self.assertIn('.edu-stage', css)
        self.assertIn('.edu-result-box', css)

    def test_synced_across_all_browser_extensions(self):
        """Verifies all browser variants have the exact same updated files."""
        files = ['content.js', 'extension_popup.html', 'educational_history.js', 'service_worker.js', 'ui.css']
        ext_dirs = [
            'StrictRoutines-Browser-Extensions/Arc/StrictRoutines',
            'StrictRoutines-Browser-Extensions/Brave/StrictRoutines',
            'StrictRoutines-Browser-Extensions/Chrome/StrictRoutines',
            'StrictRoutines-Browser-Extensions/Comet/StrictRoutines',
            'StrictRoutines-Browser-Extensions/Edge/StrictRoutines',
            'StrictRoutines-Browser-Extensions/Opera/StrictRoutines',
            'StrictRoutines-Browser-Extensions/Vivaldi/StrictRoutines',
        ]
        for d in ext_dirs:
            for f in files:
                src_path = os.path.join('browser_extension', f)
                target = os.path.join(d, f)
                with open(src_path, 'rb') as fp1, open(target, 'rb') as fp2:
                    self.assertEqual(fp1.read(), fp2.read(), f"Mismatch in {target}")

if __name__ == '__main__':
    unittest.main()
