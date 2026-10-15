import io
import unittest
from urllib.error import URLError
from unittest.mock import patch

from app.repec import MAX_BYTES, SOURCE, fetch_redif, validate_redif


RAW = b'''Template-Type: ReDIF-Article 1.0
Title: Test article
Author-Name: A. Author
Abstract: Test abstract.
Volume: 37
Issue: 6
Year: 2026
Pages: 1-9
Handle: RePEc:taf:defpea:v:37:y:2026:i:6:p:1-9
'''


class RepecTests(unittest.TestCase):
    def test_valid_utf8_bom_and_invalid_records(self):
        self.assertEqual(len(validate_redif(b'\xef\xbb\xbf' + RAW)), 1)
        invalid = [b'', b'<html>Error</html>', RAW + RAW,
                   RAW.replace(b'Title: Test article\n', b''),
                   RAW.replace(b'ReDIF-Article', b'ReDIF-Paper'),
                   RAW.replace(b'RePEc:taf:defpea', b'RePEc:other:series'),
                   RAW.replace(b'Year: 2026\n', b''),
                   RAW + b'Template-Type: ReDIF-Article 1.0\nTitle: Truncated\n']
        for raw in invalid:
            with self.subTest(raw=raw[:60]), self.assertRaises(ValueError):
                validate_redif(raw)

    def test_fetch_is_bounded_and_returns_provenance(self):
        response = io.BytesIO(RAW)
        response.headers = {'Last-Modified': 'today'}
        with patch('app.repec.urllib.request.urlopen', return_value=response) as request:
            raw, manifest = fetch_redif()
        request.assert_called_once_with(SOURCE, timeout=20)
        self.assertEqual(raw, RAW)
        self.assertEqual(manifest['bytes'], len(RAW))
        self.assertEqual(manifest['article_count'], 1)
        self.assertEqual(manifest['url'], SOURCE)
        self.assertEqual(len(manifest['sha256']), 64)
        self.assertIn('retrieved_at', manifest)
        self.assertTrue(response.closed)

    def test_retries_transport_failures_but_not_invalid_content(self):
        with patch('app.repec.urllib.request.urlopen', side_effect=URLError('offline')) as request, \
                patch('app.repec.time.sleep'), self.assertRaisesRegex(RuntimeError, '3 attempts'):
            fetch_redif()
        self.assertEqual(request.call_count, 3)
        response = io.BytesIO(b'<html>Not ReDIF</html>')
        response.headers = {}
        with patch('app.repec.urllib.request.urlopen', return_value=response) as request, self.assertRaises(ValueError):
            fetch_redif()
        self.assertEqual(request.call_count, 1)

    def test_oversized_response_rejected(self):
        response = io.BytesIO(b'x' * (MAX_BYTES + 2))
        response.headers = {}
        with patch('app.repec.urllib.request.urlopen', return_value=response), self.assertRaisesRegex(ValueError, '32 MiB'):
            fetch_redif()


if __name__ == '__main__':
    unittest.main()
