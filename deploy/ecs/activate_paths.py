"""Stage static pages and atomically switch the existing loopback-only gateway.

Run on the ECS after uploading a bundle beside this script. API/feedback units,
databases, sshd, firewalls and security groups are not modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from prepare_paths import prepare

BASE = Path('/srv/ordinary-muse-h5')
ACTIVE = BASE / 'paths-current'
CONFIG = BASE / 'nginx.conf'


def replace_link(target):
    temporary = BASE / '.paths-current.next'
    if temporary.is_symlink():
        temporary.unlink()
    os.symlink(target, temporary)
    os.replace(temporary, ACTIVE)


def replace_config(source):
    temporary = BASE / '.nginx.paths.next.conf'
    shutil.copyfile(source, temporary)
    temporary.chmod(0o644)
    os.replace(temporary, CONFIG)


def verify():
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for _ in range(30):
        try:
            try:
                opener.open('http://127.0.0.1:8782/', timeout=3)
                raise ValueError('root did not redirect')
            except urllib.error.HTTPError as response:
                assert response.code == 302 and response.headers['Location'] == '/score/'
            with opener.open('http://127.0.0.1:8782/score/', timeout=3) as response:
                assert b'action="./api/feedback"' in response.read()
            with opener.open('http://127.0.0.1:8782/audio/app.js', timeout=3) as response:
                assert b'fetch(apiUrl(path)' in response.read()
            with opener.open('http://127.0.0.1:8782/audio/api/v1/health', timeout=3) as response:
                assert json.load(response)['status'] == 'healthy'
            return
        except (OSError, ValueError, AssertionError):
            time.sleep(.2)
    raise RuntimeError('New private gateway did not pass its readiness checks')


def activate(release_name, expected_hash):
    if not release_name or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for char in release_name):
        raise ValueError('Use a simple, unique release name')
    if hashlib.sha256(CONFIG.read_bytes()).hexdigest() != expected_hash:
        raise RuntimeError('Gateway config changed since preflight; inspect before replacing it')
    if ACTIVE.exists() and not ACTIVE.is_symlink():
        raise RuntimeError('paths-current exists but is not a managed symlink')
    staging = Path(__file__).resolve().parent
    manifest = prepare('/srv/dynamic-score-h5/dist', staging / 'web', BASE / 'path-releases' / release_name)
    release = BASE / 'path-releases' / release_name
    for path in [release, *release.rglob('*')]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    subprocess.run(['/usr/sbin/nginx', '-t', '-c', str(staging / 'nginx.conf')], check=True)
    backup = staging / 'nginx.before.conf'
    if backup.exists():
        raise FileExistsError('Refusing to overwrite a rollback backup')
    shutil.copy2(CONFIG, backup)
    old_target = os.readlink(ACTIVE) if ACTIVE.is_symlink() else None
    (staging / 'previous-target.json').write_text(json.dumps(old_target))
    try:
        replace_link(str(release))
        replace_config(staging / 'nginx.conf')
        subprocess.run(['/usr/sbin/nginx', '-t', '-c', str(CONFIG)], check=True)
        subprocess.run(['systemctl', 'reload', 'ordinary-muse-h5'], check=True)
        verify()
    except Exception:
        replace_config(backup)
        if old_target is None:
            if ACTIVE.is_symlink():
                ACTIVE.unlink()
        else:
            replace_link(old_target)
        subprocess.run(['/usr/sbin/nginx', '-t', '-c', str(CONFIG)], check=True)
        subprocess.run(['systemctl', 'reload', 'ordinary-muse-h5'], check=True)
        raise
    print(json.dumps({'release': str(release), 'files': len(manifest), 'backup': str(backup),
                      'listen': '127.0.0.1:8782', 'root_redirect': '/score/'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-name', required=True)
    parser.add_argument('--expected-config-sha256', required=True)
    args = parser.parse_args()
    activate(args.release_name, args.expected_config_sha256)
