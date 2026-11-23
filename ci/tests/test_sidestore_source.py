import importlib.util
import json
import plistlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sidestore_source', Path(__file__).resolve().parents[1] / 'sidestore_source.py')
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class SideStoreSourceTests(unittest.TestCase):
    def test_metadata_matches_ipa(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / 'AyuGram-standard-213802.ipa'
            with zipfile.ZipFile(ipa, 'w') as archive:
                archive.writestr('Payload/Telegram.app/Info.plist', plistlib.dumps({'CFBundleIdentifier': 'example.telegram', 'CFBundleShortVersionString': '13.1', 'CFBundleVersion': '213802', 'MinimumOSVersion': '15.0'}))
            result = source.make_source(ipa, 'Xitaoyou/AyuGram-iOS-Swiftgram', '213802')
            app = result['apps'][0]
            version = app['versions'][0]
            self.assertEqual(app['bundleIdentifier'], 'example.telegram')
            self.assertEqual(version['version'], '13.1')
            self.assertEqual(version['buildVersion'], '213802')
            self.assertEqual(version['size'], ipa.stat().st_size)
            self.assertEqual(version['sha256'], source.sha256(ipa))
            self.assertTrue(version['downloadURL'].endswith('/build-213802/AyuGram-standard-213802.ipa'))
            with self.assertRaises(ValueError):
                source.make_source(ipa, 'Xitaoyou/AyuGram-iOS-Swiftgram', '999')

    def test_older_build_does_not_replace_newer_source(self):
        class Response:
            returncode = 0
            stderr = ''
            stdout = json.dumps({'sha': 'abc', 'content': __import__('base64').b64encode(json.dumps({'apps': [{'versions': [{'buildVersion': '12'}]}]}).encode()).decode()})
        with patch.object(source.subprocess, 'run', return_value=Response()) as run:
            source.publish('owner/repo', {'apps': [{'versions': [{'buildVersion': '11'}]}]})
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
