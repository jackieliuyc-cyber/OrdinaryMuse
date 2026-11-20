"""Private local H5 preview over existing Workbench credentials; loopback only."""
import argparse
import base64
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

PORT = 18882
GATEWAY_PORT = 8782
LOCAL = None
REMOTE = None
WORKBENCH = None
TARGET = []
LOCK = threading.Lock()
CACHE = {}
MEDIA_CACHE = {}


@dataclass(frozen=True)
class FileSlice:
    path: Path
    start: int
    length: int


def slice_media(headers, path, byte_range):
    """Honor the entire requested range; never cap a browser's open-ended read."""
    size = path.stat().st_size
    headers = {k: v for k, v in headers.items() if k.lower() not in ('content-range', 'content-length', 'accept-ranges')}
    headers['Accept-Ranges'] = 'bytes'
    if not byte_range:
        return 200, headers, FileSlice(path, 0, size)
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', byte_range.strip())
    if match and any(match.groups()):
        first, last = match.groups()
        start = int(first) if first else max(0, size - int(last))
        end = min(int(last), size - 1) if first and last else size - 1
        if start <= end and start < size:
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            return 206, headers, FileSlice(path, start, end - start + 1)
    headers['Content-Range'] = f'bytes */{size}'
    return 416, headers, b''


def cached_media(key, byte_range):
    cached = MEDIA_CACHE.get(key)
    if cached is None:
        return None
    headers, path, expires = cached
    if time.time() >= expires:
        return 410, {'Content-Type': 'application/json'}, b'{"detail":"Task results expired"}'
    if path.is_file():
        return slice_media(headers, path, byte_range)
    return None


REMOTE_HTTP = r'''
import base64,http.client,json,sys,uuid,os
from pathlib import Path
spec=json.loads(sys.argv[1])
c=http.client.HTTPConnection('127.0.0.1',spec.get('gateway_port',8782),timeout=120)
headers=spec.get('headers',{})
uploaded=spec.get('uploaded')
stream=None
try:
 if uploaded:
  stream=open(uploaded,'rb'); headers['Content-Length']=str(os.stat(uploaded).st_size)
 c.request(spec['method'],spec['path'],body=stream,headers=headers)
 r=c.getresponse(); meta={'status':r.status,'headers':dict(r.getheaders())}
 if spec['head']:
  meta['body']=''
 elif spec['file']:
  target=Path(spec['directory'])/('response-'+uuid.uuid4().hex+'.bin')
  size=0
  with target.open('xb') as output:
   while True:
    chunk=r.read(65536)
    if not chunk: break
    size+=len(chunk)
    if size>536870912: raise ValueError('test download exceeds 512 MiB')
    output.write(chunk)
  meta.update(file=str(target),size=size)
 else:
  body=r.read(45001)
  if len(body)>45000: raise ValueError('test inline response too large')
  meta['body']=base64.b64encode(body).decode()
 print(json.dumps(meta))
finally:
 if stream: stream.close()
 if uploaded: os.unlink(uploaded)
 c.close()
'''


def cli(args, timeout=180):
    result = subprocess.run([WORKBENCH, *args, *TARGET], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Workbench transport failed: ' + result.stderr[-500:])
    return result.stdout


def remote(command, timeout=145):
    reply = json.loads(cli(['exec', '--timeout', str(timeout), '--output', 'json', '--command', command], timeout=timeout+30))
    if reply.get('exit_code', 0):
        raise RuntimeError('Remote request failed: ' + reply.get('stderr', '')[-500:])
    return reply.get('stdout') or reply.get('output') or ''


def forward(method, path, headers, uploaded=None, head=False):
    url = urlsplit(path)
    media = bool(re.fullmatch(r'/audio/api/v1/task/[A-Za-z0-9_-]+/(?:audio|download|stem/[A-Za-z0-9_-]+)', url.path)) or url.path == '/score/demo.mp4'
    byte_range = headers.get('Range')
    if media:
        cached = cached_media(path, byte_range)
        if cached is not None:
            return cached
    file_response = media
    static = url.path in ('/audio/app.js', '/audio/style.css', '/audio/', '/score/', '/score/app.js', '/score/style.css')
    cache_key = path
    with LOCK:
        expires = time.time() + 300
        if media:
            cached = cached_media(path, byte_range)
            if cached is not None:
                return cached
            # Fetch complete media once, then serve every playback/seek locally.
            headers = {key: value for key, value in headers.items() if key.lower() != 'range'}
            match = re.match(r'(/audio/api/v1/task/[A-Za-z0-9_-]+)', url.path)
            if match:
                spec = {'method': 'GET', 'path': match[1], 'headers': {}, 'head': False, 'file': False, 'directory': REMOTE, 'gateway_port': GATEWAY_PORT}
                command = ' '.join(shlex.quote(arg) for arg in ['python3.11', '-c', 'exec(' + json.dumps(REMOTE_HTTP) + ')', json.dumps(spec)])
                task_reply = json.loads(remote(command))
                task_body = base64.b64decode(task_reply['body'])
                if task_reply['status'] != 200:
                    return task_reply['status'], task_reply['headers'], task_body
                task = json.loads(task_body)
                if task.get('expires_at'):
                    expires = datetime.fromisoformat(task['expires_at']).timestamp()
        if static and method == 'GET' and not head and cache_key in CACHE:
            return CACHE[cache_key]
        if uploaded:
            destination = REMOTE + '/request-' + uuid.uuid4().hex + '.bin'
            remote('test ! -e ' + shlex.quote(destination))
            cli(['upload', str(uploaded), destination], timeout=300)
        else:
            destination = None
        spec = {'method': method, 'path': path, 'headers': headers, 'uploaded': destination,
                'head': head and not media, 'file': file_response, 'directory': REMOTE, 'gateway_port': GATEWAY_PORT}
        command = ' '.join(shlex.quote(arg) for arg in ['python3.11', '-c', 'exec(' + json.dumps(REMOTE_HTTP) + ')', json.dumps(spec)])
        reply = json.loads(remote(command))
        if 'file' in reply:
            downloaded = LOCAL / Path(reply['file']).name
            cli(['download', reply['file'], str(downloaded)], timeout=300)
            remote('python3.11 -c ' + shlex.quote('from pathlib import Path; Path(' + repr(reply['file']) + ').unlink()'))
            if downloaded.stat().st_size != reply['size']:
                raise RuntimeError('Incomplete media transfer')
            result = (reply['status'], reply['headers'], downloaded)
        else:
            result = (reply['status'], reply['headers'], base64.b64decode(reply['body']))
        if static and method == 'GET' and not head and result[0] == 200:
            CACHE[cache_key] = result
        if media and result[0] == 200 and isinstance(result[2], Path):
            MEDIA_CACHE[path] = (result[1], result[2], expires)
            return slice_media(result[1], result[2], byte_range)
        return result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.serve('GET')

    def do_HEAD(self):
        self.serve('GET', head=True)

    def do_POST(self):
        self.serve('POST')

    def serve(self, method, head=False):
        self.server.last_request = time.monotonic()
        if self.headers.get('Host') not in {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}:
            return self.send_error(403)
        path = urlsplit(self.path)
        if path.netloc or path.scheme or not (path.path in ('/', '/score', '/audio') or
                path.path.startswith('/score/') or path.path.startswith('/audio/')):
            return self.send_error(404)
        payload = None
        headers = {key: self.headers[key] for key in ('Range', 'Content-Type', 'X-Request-ID') if self.headers.get(key)}
        if method == 'POST':
            if self.headers.get('Origin') not in (None, f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'):
                return self.send_error(403)
            if not (path.path in ('/audio/api/v1/separate/async', '/audio/api/v1/extract/async') or
                    re.fullmatch(r'/audio/api/v1/task/[A-Za-z0-9_-]+/separate', path.path)):
                return self.send_error(405)
            size = int(self.headers.get('Content-Length', '0'))
            if size < 0 or size > 501 * 1024 * 1024:
                return self.send_error(413)
            payload = LOCAL / ('upload-' + uuid.uuid4().hex + '.bin')
            with payload.open('xb') as output:
                left = size
                while left:
                    chunk = self.rfile.read(min(left, 1024*1024))
                    if not chunk:
                        return self.send_error(400)
                    output.write(chunk); left -= len(chunk)
        try:
            status, response_headers, body = forward(method, self.path, headers, payload, head)
            self.send_response(status)
            for key, value in response_headers.items():
                if key.lower() in ('content-type', 'content-disposition', 'location', 'content-range', 'accept-ranges'):
                    self.send_header(key, value)
            length = body.length if isinstance(body, FileSlice) else (body.stat().st_size if isinstance(body, Path) else len(body))
            if head and not isinstance(body, (Path, FileSlice)):
                length = int(next((v for k, v in response_headers.items() if k.lower() == 'content-length'), len(body)))
            self.send_header('Content-Length', str(length))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if not head:
                if isinstance(body, (Path, FileSlice)):
                    sliced = body if isinstance(body, FileSlice) else FileSlice(body, 0, body.stat().st_size)
                    with sliced.path.open('rb') as stream:
                        stream.seek(sliced.start)
                        remaining = sliced.length
                        while remaining:
                            chunk = stream.read(min(65536, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                else:
                    self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.log_error('%s: %s', type(error).__name__, str(error)[:500])
            self.send_error(502, 'Private test connection could not complete the request')
        finally:
            if payload:
                payload.unlink(missing_ok=True)


def main(argv=None):
    global PORT, GATEWAY_PORT, LOCAL, REMOTE, WORKBENCH, TARGET
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance-id', default='i-bp1dhypbynl7gfsclml5')
    parser.add_argument('--region', default='cn-hangzhou')
    parser.add_argument('--profile', default='h5-hangzhou')
    parser.add_argument('--port', type=int, default=18882)
    parser.add_argument('--gateway-port', type=int, default=8782)
    parser.add_argument('--cache-dir', type=Path, default=Path.home()/'.cache/ordinary-muse/private-preview')
    parser.add_argument('--remote-dir', help='Private ECS scratch directory; defaults to a unique directory per run.')
    parser.add_argument('--workbench', default=shutil.which('workbench') or str(Path.home()/'.local/bin/workbench'))
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535 or not 1 <= args.gateway_port <= 65535:
        parser.error('Invalid local or gateway port')
    PORT, GATEWAY_PORT = args.port, args.gateway_port
    LOCAL = args.cache_dir.expanduser().resolve()
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    if LOCAL.stat().st_mode & 0o077:
        parser.error('Use a private cache directory (permissions 0700)')
    REMOTE = args.remote_dir or '/srv/ordinary-muse-h5/preview-cache/' + uuid.uuid4().hex
    if not REMOTE.startswith('/') or '..' in Path(REMOTE).parts:
        parser.error('Remote scratch directory must be an absolute path without ..')
    WORKBENCH = args.workbench
    TARGET = ['-i', args.instance_id, '-r', args.region, '-P', args.profile]
    # Bind before contacting ECS so an occupied port cannot start a duplicate preview.
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    try:
        setup = 'from pathlib import Path; Path(' + repr(REMOTE) + ').mkdir(parents=True, exist_ok=True, mode=0o700)'
        remote('python3.11 -c ' + shlex.quote(setup), timeout=30)
        print(f'Private H5 preview: http://127.0.0.1:{PORT}/audio/', flush=True)
        print(f'Cache: {LOCAL}; runs until stopped (no idle shutdown).', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

