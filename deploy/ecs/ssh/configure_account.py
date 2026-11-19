"""Install a key-only, loopback-only tunnel account on the existing Linux ECS.

Append a scoped Match block rather than requiring sshd Include support. OpenSSH
8.0 is supported. No keys are authorized by this provisioning step.
"""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile

USER = 'muse-tunnel'
CONFIG = Path('/etc/ssh/sshd_config')
KEYS = Path('/etc/ssh/authorized_keys/muse-tunnel')
GATE = Path('/usr/local/libexec/ordinary-muse-tunnel-command')
START = '# BEGIN ORDINARY MUSE TUNNEL ACCOUNT'
END = '# END ORDINARY MUSE TUNNEL ACCOUNT'


def effective(path, user):
    result = subprocess.run(['/usr/sbin/sshd', '-T', '-f', str(path), '-C',
                             f'user={user},host=localhost,addr=127.0.0.1'],
                            capture_output=True, text=True, check=True)
    values = {}
    for line in result.stdout.splitlines():
        name, value = line.split(' ', 1)
        values[name] = values[name] + '\n' + value if name in values else value
    return values


def compose(original, fragment):
    if START in original or END in original:
        if original.count(START) != 1 or original.count(END) != 1:
            raise ValueError('Ambiguous managed SSH block; inspect before editing')
        before, rest = original.split(START, 1)
        _, after = rest.split(END, 1)
        return before.rstrip() + '\n\n' + fragment.strip() + '\n' + after.lstrip('\n')
    return original.rstrip() + '\n\n' + fragment.strip() + '\n'


def configure(expected_hash):
    if os.geteuid() != 0:
        raise RuntimeError('Run as root on the ECS using the existing administrator connection')
    source = Path(__file__).resolve().parent
    before_bytes = CONFIG.read_bytes()
    if hashlib.sha256(before_bytes).hexdigest() != expected_hash:
        raise RuntimeError('sshd_config changed since preflight; inspect before applying')
    before_root = effective(CONFIG, 'root')
    fragment = (source / 'tunnel-account.conf').read_text()
    gate = (source / 'tunnel-command.sh').read_bytes()
    if GATE.exists() and GATE.read_bytes() != gate:
        raise RuntimeError('The command gate already exists with different content')
    try:
        user = pwd.getpwnam(USER)
    except KeyError:
        user = None
    if user:
        groups = [group.gr_name for group in grp.getgrall() if USER in group.gr_mem]
        if user.pw_uid < 1000 or user.pw_dir != '/home/' + USER or user.pw_shell != '/bin/bash' or groups:
            raise RuntimeError('Existing account does not match the dedicated unprivileged worker profile')
    with tempfile.NamedTemporaryFile(prefix='.ordinary-muse-candidate-', dir=CONFIG.parent, delete=False) as stream:
        candidate = Path(stream.name)
    try:
        shutil.copy2(CONFIG, candidate)
        candidate.write_text(compose(before_bytes.decode(), fragment))
        subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(candidate)], check=True)
        if effective(candidate, 'root') != before_root:
            raise RuntimeError('Candidate unexpectedly changes administrator SSH policy')
        target = effective(candidate, USER)
        expected = {'authenticationmethods': 'publickey', 'passwordauthentication': 'no',
                    'authorizedkeysfile': str(KEYS), 'authorizedkeyscommand': 'none',
                    'allowtcpforwarding': 'remote', 'permitlisten': '127.0.0.1:19000',
                    'permitopen': 'none', 'gatewayports': 'no', 'permittty': 'no',
                    'allowstreamlocalforwarding': 'no', 'allowagentforwarding': 'no',
                    'x11forwarding': 'no', 'forcecommand': str(GATE)}
        if any(target.get(key) != value for key, value in expected.items()):
            raise RuntimeError('Effective tunnel policy differs from the requested restrictions')
        backup = source / 'sshd_config.before'
        if backup.exists():
            raise FileExistsError('Use a new staging directory; the rollback backup already exists')
        shutil.copy2(CONFIG, backup)
        backup.chmod(0o600)
        if user is None:
            subprocess.run(['useradd', '--create-home', '--user-group', '--shell', '/bin/bash',
                            '--comment', 'Ordinary Muse reverse tunnel', USER], check=True)
            user = pwd.getpwnam(USER)
        GATE.parent.mkdir(parents=True, exist_ok=True)
        GATE.write_bytes(gate)
        os.chown(GATE, 0, 0)
        GATE.chmod(0o755)
        KEYS.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        if not KEYS.exists():
            KEYS.touch(mode=0o644)
        os.chown(KEYS, 0, 0)
        # Public keys must be readable by the target user, but only root can edit.
        KEYS.chmod(0o644)
        try:
            os.replace(candidate, CONFIG)
            subprocess.run(['/usr/sbin/sshd', '-t'], check=True)
            subprocess.run(['systemctl', 'reload', 'sshd'], check=True)
            if effective(CONFIG, 'root') != before_root:
                raise RuntimeError('Administrator policy changed after reload')
        except Exception:
            shutil.copy2(backup, candidate)
            os.replace(candidate, CONFIG)
            subprocess.run(['/usr/sbin/sshd', '-t'], check=True)
            subprocess.run(['systemctl', 'reload', 'sshd'], check=True)
            raise
        print(json.dumps({'user': USER, 'uid': user.pw_uid, 'home': user.pw_dir,
                          'authorized_keys': str(KEYS), 'authorized_key_count': len(KEYS.read_text().splitlines()),
                          'permit_listen': target['permitlisten'], 'administrator_policy_unchanged': True,
                          'backup': str(backup)}))
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-config-sha256', required=True)
    configure(parser.parse_args().expected_config_sha256)
