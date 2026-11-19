"""Verify a deployed private gateway without submitting real feedback/mail."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def main(base, sample):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(path, *, data=None, headers=None, method=None):
        try:
            response = opener.open(urllib.request.Request(base + path, data=data, headers=headers or {}, method=method), timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    checks = []
    for path, status, destination in [('/', 302, '/score/'), ('/score', 308, '/score/'),
                                      ('/audio', 308, '/audio/'), ('/audio?from=test', 308, '/audio/?from=test')]:
        actual, headers, _ = request(path)
        assert (actual, headers.get('Location')) == (status, destination), (path, actual, dict(headers))
        checks.append(path)
    for prefix, assets in [('score', ['index.html', 'app.js', 'style.css', 'feedback-form.js', 'worker.js', 'engine.js', 'input.js', 'media.js', 'icon.svg']),
                           ('audio', ['index.html', 'app.js', 'style.css'])]:
        for asset in ['', *assets]:
            status, headers, body = request(f'/{prefix}/{asset}')
            assert status == 200 and body, (prefix, asset, status)
            if asset.endswith('.js'):
                assert 'javascript' in headers['Content-Type']
            checks.append(f'/{prefix}/{asset}')
    score = request('/score/')[2]
    assert b'action="./api/feedback"' in score
    assert b'fetch(feedbackForm.action,' in request('/score/feedback-form.js')[2]
    assert b'fetch(apiUrl(path)' in request('/audio/app.js')[2]
    status, headers, body = request('/score/demo.mp4', headers={'Range': 'bytes=0-31'})
    assert status == 206 and len(body) == 32 and headers.get('Content-Range')
    # Reject malformed feedback before any database insert or mail notification.
    assert request('/score/api/feedback')[0] == 405
    status, _, body = request('/score/api/feedback', data=b'{"message":""}', headers={'Content-Type': 'application/json'})
    assert status == 400 and json.loads(body)['result'] == 'invalid_message'
    for path in ['/api/feedback', '/api/v1/health', '/app.js', '/score/api/v1/health', '/audio/api/feedback']:
        assert request(path)[0] == 404, path
    config = json.loads(request('/audio/api/v1/web-config')[2])
    health = json.loads(request('/audio/api/v1/health')[2])
    assert health['status'] == 'healthy'
    if config['separation_enabled'] is False:
        assert request('/audio/api/v1/separate/async', data=b'')[0] in (422, 503)
    # Real FFmpeg extraction, then poll, full download and Range preview via /audio/.
    task = json.loads(subprocess.check_output([
        'curl', '--noproxy', '*', '--fail', '--silent', '--show-error', '--max-time', '60',
        '-F', 'file=@' + str(sample), base + '/audio/api/v1/extract/async?format=m4a']))
    path = '/audio/api/v1/task/' + task['task_id']
    for _ in range(120):
        info = json.loads(request(path)[2])
        if info['status'] in ('completed', 'failed'):
            break
        time.sleep(.5)
    assert info['status'] == 'completed', info
    status, _, audio = request(path + '/audio')
    assert status == 200 and len(audio) > 100
    status, headers, preview = request(path + '/audio?preview=true', headers={'Range': 'bytes=0-31'})
    assert status == 206 and len(preview) == 32 and headers.get('Content-Range')
    assert (datetime.fromisoformat(info['expires_at']) - datetime.fromisoformat(info['completed_at'])).total_seconds() == 86400
    result = {'static_and_redirect_checks': checks, 'namespace_isolation': 'passed',
              'feedback_validation': 'passed; no feedback saved or email sent',
              'extraction_task_id': task['task_id'], 'processing_time': info['processing_time'],
              'download_bytes': len(audio), 'preview_range': 'passed', 'retention_seconds': 86400}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8782')
    parser.add_argument('--sample', type=Path, default=Path('/srv/ordinary-muse-h5/benchmarks/video-30s.mp4'))
    args = parser.parse_args()
    main(args.base.rstrip('/'), args.sample)
