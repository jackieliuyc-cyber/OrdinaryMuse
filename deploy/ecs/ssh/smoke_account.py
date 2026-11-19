"""Exercise SSH restrictions locally on ECS with an ephemeral key; always revoke it.

This proves ECS-side SSH policy, not new-Mac connectivity or model performance.
All test listeners bind to 127.0.0.1 and are closed after the check.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

KEYS = Path('/etc/ssh/authorized_keys/muse-tunnel')
HEALTH_COMMAND = "curl --noproxy '*' --fail --silent --show-error --max-time 8 http://127.0.0.1:19000/api/v1/health"


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"healthy","test":"ecs-ssh-loopback"}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Run only as ECS administrator')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 19000))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='ordinary-muse-ssh-', dir='/root') as directory:
        directory = Path(directory)
        key = directory / 'id_ed25519'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key),
                        '-C', 'ordinary-muse-ephemeral-smoke'], check=True)
        public_key = key.with_suffix('.pub').read_bytes()
        original = KEYS.read_bytes()
        appended = original + (b'\n' if original and not original.endswith(b'\n') else b'') + public_key
        (directory / 'known_hosts').write_text('127.0.0.1 ' + Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text())
        common = ['ssh', '-F', '/dev/null', '-T', '-i', str(key), '-o', 'BatchMode=yes',
                  '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                  '-o', 'UserKnownHostsFile=' + str(directory / 'known_hosts'),
                  '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=5']
        destination = 'muse-tunnel@127.0.0.1'
        processes = []
        server = None
        KEYS.write_bytes(appended)
        try:
            result = subprocess.run(common + [destination, 'true'], capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr
            for command in ('whoami', ''):
                result = subprocess.run(common + [destination, command], input='', capture_output=True, text=True, timeout=10)
                assert result.returncode == 126, (command, result.returncode, result.stderr)
            server = ThreadingHTTPServer(('127.0.0.1', 0), Health)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            model_port = server.server_address[1]
            with (directory / 'reverse.log').open('w') as log:
                reverse = subprocess.Popen(common + ['-N', '-R', f'127.0.0.1:19000:127.0.0.1:{model_port}', destination],
                                           stdout=subprocess.DEVNULL, stderr=log)
                processes.append(reverse)
                for _ in range(60):
                    if reverse.poll() is not None:
                        raise RuntimeError((directory / 'reverse.log').read_text())
                    try:
                        with opener.open('http://127.0.0.1:19000/api/v1/health', timeout=1) as response:
                            assert json.load(response)['test'] == 'ecs-ssh-loopback'
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(.1)
                else:
                    raise RuntimeError('Allowed reverse tunnel did not become ready')
                result = subprocess.run(common + [destination, HEALTH_COMMAND], capture_output=True, text=True, timeout=15)
                assert result.returncode == 0 and json.loads(result.stdout)['test'] == 'ecs-ssh-loopback', result.stderr
                denied = subprocess.run(common + ['-N', '-R', f'127.0.0.1:19001:127.0.0.1:{model_port}', destination],
                                        capture_output=True, text=True, timeout=10)
                assert denied.returncode == 255 and 'remote port forwarding failed' in denied.stderr, denied.stderr
                local_port = free_port()
                with (directory / 'local.log').open('w') as local_log:
                    local = subprocess.Popen(common + ['-N', '-L', f'127.0.0.1:{local_port}:127.0.0.1:{model_port}', destination],
                                             stdout=subprocess.DEVNULL, stderr=local_log)
                    processes.append(local)
                    for _ in range(60):
                        if local.poll() is not None:
                            raise RuntimeError('Local-forward SSH session unexpectedly exited')
                        try:
                            with socket.create_connection(('127.0.0.1', local_port), timeout=.2) as connection:
                                connection.settimeout(3)
                                connection.sendall(b'GET / HTTP/1.0\r\n\r\n')
                                try:
                                    reply = connection.recv(100)
                                except ConnectionResetError:
                                    reply = b''
                                assert not reply, 'Local forwarding unexpectedly succeeded'
                            break
                        except ConnectionRefusedError:
                            time.sleep(.1)
                    else:
                        raise RuntimeError('Local-forward negative test never established its listener')
                    stop(local)
                assert 'administratively prohibited' in (directory / 'local.log').read_text()
        finally:
            for process in processes:
                stop(process)
            if server:
                server.shutdown()
                server.server_close()
            # Preserve any other key a separate administrator may have added.
            current = KEYS.read_bytes()
            if current == appended:
                KEYS.write_bytes(original)
            else:
                KEYS.write_bytes(b''.join(line for line in current.splitlines(keepends=True) if line.strip() != public_key.strip()))
        print(json.dumps({'key_login_probe': 'passed', 'general_shell': 'denied',
                          'health_command': 'passed', 'reverse_127_0_0_1_19000': 'passed',
                          'other_reverse_port': 'denied', 'local_forwarding': 'denied',
                          'temporary_key_revoked': public_key.strip() not in KEYS.read_bytes().splitlines(),
                          'new_mac_connection_tested': False}))


if __name__ == '__main__':
    main()
