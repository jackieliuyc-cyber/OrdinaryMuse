import concurrent.futures
import importlib.util
from http.server import ThreadingHTTPServer
import sys
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

spec = importlib.util.spec_from_file_location('preview', Path(__file__).resolve().parents[1] / 'private_preview.py')
preview = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = preview
spec.loader.exec_module(preview)


class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.data = bytes(range(256)) * 1100
        cls.path = Path(cls.temp.name) / 'track.m4a'
        cls.path.write_bytes(cls.data)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), preview.Handler)
        preview.PORT = cls.server.server_address[1]
        cls.base = f'http://127.0.0.1:{preview.PORT}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)
        cls.temp.cleanup()

    def setUp(self):
        preview.MEDIA_CACHE.clear()
        for name in ('drums', 'bass', 'other', 'vocals'):
            preview.MEDIA_CACHE[self.route(name)] = ({'Content-Type':'audio/mp4'}, self.path, time.time()+3600)

    @staticmethod
    def route(name='vocals'):
        return f'/audio/api/v1/task/job/stem/{name}?format=m4a'

    def get(self, name='vocals', byte_range=None, method='GET'):
        headers = {'Range':byte_range} if byte_range else {}
        request = urllib.request.Request(self.base+self.route(name), headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_full_and_open_ended_reads_exceed_32k(self):
        for range_header, status in [(None,200),('bytes=0-',206)]:
            code, headers, data = self.get(byte_range=range_header)
            self.assertEqual(code,status)
            self.assertEqual(data,self.data)
            self.assertEqual(int(headers['Content-Length']),len(self.data))

    def test_seeking_and_suffix_ranges(self):
        for range_header, expected in [('bytes=100000-220000',self.data[100000:220001]),
                                       ('bytes=200000-',self.data[200000:]),
                                       ('bytes=-4000',self.data[-4000:])]:
            code, headers, data = self.get(byte_range=range_header)
            self.assertEqual(code,206)
            self.assertEqual(data,expected)
            self.assertEqual(int(headers['Content-Length']),len(expected))

    def test_bad_ranges_and_head(self):
        for value in ['bytes=999999-', 'bytes=100-10', 'bytes=-0', 'bytes=0-1,4-5']:
            code, headers, data = self.get(byte_range=value)
            self.assertEqual(code,416)
            self.assertEqual(headers['Content-Range'],f'bytes */{len(self.data)}')
            self.assertEqual(data,b'')
        code, headers, data = self.get(byte_range='bytes=40000-90000',method='HEAD')
        self.assertEqual(code,206)
        self.assertEqual(int(headers['Content-Length']),50001)
        self.assertEqual(data,b'')

    def test_four_players_work_without_remote_calls_or_transport_lock(self):
        with patch.object(preview,'remote',side_effect=AssertionError('unexpected Workbench request')):
            with preview.LOCK:
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    replies=list(pool.map(lambda name:self.get(name, 'bytes=0-100000'), ('drums','bass','other','vocals')))
        for code, headers, data in replies:
            self.assertEqual(code,206)
            self.assertEqual(data,self.data[:100001])

    def test_cached_results_obey_task_expiry(self):
        preview.MEDIA_CACHE[self.route()] = ({'Content-Type':'audio/mp4'},self.path,time.time()-1)
        self.assertEqual(self.get()[0],410)


if __name__ == '__main__':
    unittest.main()

