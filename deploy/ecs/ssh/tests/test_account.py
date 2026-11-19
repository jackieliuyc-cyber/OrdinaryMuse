import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('configure_account', SOURCE / 'configure_account.py')
account = importlib.util.module_from_spec(spec)
spec.loader.exec_module(account)


class AccountTests(unittest.TestCase):
    def test_command_gate_allows_probe_and_never_evaluates_other_input(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'must-not-exist'
            for command, expected in [('true', 0), ('', 126), ('whoami', 126),
                                      (f'true; touch {marker}', 126), (f'$(touch {marker})', 126),
                                      ('internal-sftp', 126)]:
                result = subprocess.run(['/bin/sh', str(SOURCE / 'tunnel-command.sh')],
                                        env={**os.environ, 'SSH_ORIGINAL_COMMAND': command},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, command)
                self.assertFalse(marker.exists())

    def test_managed_block_is_idempotent_and_preserves_unrelated_config(self):
        original = 'Port 22\nAuthorizedKeysCommand /existing/admin-helper\n'
        fragment = (SOURCE / 'tunnel-account.conf').read_text()
        combined = account.compose(original, fragment)
        self.assertTrue(combined.startswith(original))
        self.assertEqual(account.compose(combined, fragment), combined)
        self.assertTrue(combined.rstrip().endswith(account.END))
        with self.assertRaises(ValueError):
            account.compose(combined + account.START, fragment)

    def test_config_race_is_rejected_before_any_system_command(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'sshd_config'
            config.write_text('Port 22\n')
            with patch.object(account, 'CONFIG', config), patch.object(account.os, 'geteuid', return_value=0), \
                 patch.object(account.subprocess, 'run') as process:
                with self.assertRaisesRegex(RuntimeError, 'changed since preflight'):
                    account.configure('wrong-hash')
            process.assert_not_called()

    def test_candidate_cannot_change_administrator_policy(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config = directory / 'sshd_config'
            config.write_text('Port 22\n')
            with patch.object(account, 'CONFIG', config), patch.object(account, 'GATE', directory / 'gate'), \
                 patch.object(account.os, 'geteuid', return_value=0), \
                 patch.object(account.pwd, 'getpwnam', side_effect=KeyError('not created')), \
                 patch.object(account.subprocess, 'run') as process, \
                 patch.object(account, 'effective', side_effect=[{'port': '22'}, {'port': '2222'}]):
                with self.assertRaisesRegex(RuntimeError, 'administrator SSH policy'):
                    account.configure(hashlib.sha256(config.read_bytes()).hexdigest())
            self.assertEqual(config.read_text(), 'Port 22\n')
            self.assertFalse((directory / 'gate').exists())
            self.assertTrue(all(call.args[0][0] == '/usr/sbin/sshd' for call in process.call_args_list))

    def test_complete_install_sets_permissions_and_reloads_only_sshd(self):
        import hashlib
        import shutil
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for name in ('tunnel-account.conf', 'tunnel-command.sh'):
                shutil.copyfile(SOURCE / name, directory / name)
            config, gate, keys = directory / 'sshd_config', directory / 'libexec/gate', directory / 'keys/muse-tunnel'
            config.write_text('Port 22\n')
            policy = {'port': '22', 'listenaddress': '0.0.0.0:22\n[::]:22'}
            target = {'authenticationmethods': 'publickey', 'passwordauthentication': 'no',
                      'authorizedkeysfile': str(keys), 'authorizedkeyscommand': 'none',
                      'allowtcpforwarding': 'remote', 'permitlisten': '127.0.0.1:19000',
                      'permitopen': 'none', 'gatewayports': 'no', 'permittty': 'no',
                      'allowstreamlocalforwarding': 'no', 'allowagentforwarding': 'no',
                      'x11forwarding': 'no', 'forcecommand': str(gate)}
            user = SimpleNamespace(pw_uid=1001, pw_dir='/home/muse-tunnel', pw_shell='/bin/bash')
            with patch.object(account, 'CONFIG', config), patch.object(account, 'GATE', gate), \
                 patch.object(account, 'KEYS', keys), patch.object(account, '__file__', str(directory / 'configure_account.py')), \
                 patch.object(account.os, 'geteuid', return_value=0), patch.object(account.os, 'chown') as chown, \
                 patch.object(account.pwd, 'getpwnam', return_value=user), patch.object(account.grp, 'getgrall', return_value=[]), \
                 patch.object(account.subprocess, 'run') as process, \
                 patch.object(account, 'effective', side_effect=[policy, policy, target, policy]), redirect_stdout(io.StringIO()):
                account.configure(hashlib.sha256(config.read_bytes()).hexdigest())
            self.assertIn('Match User muse-tunnel', config.read_text())
            self.assertEqual((directory / 'sshd_config.before').read_text(), 'Port 22\n')
            self.assertEqual(keys.read_bytes(), b'')
            self.assertEqual(keys.stat().st_mode & 0o777, 0o644)
            self.assertEqual(gate.stat().st_mode & 0o777, 0o755)
            self.assertEqual([call.args for call in chown.call_args_list], [(gate, 0, 0), (keys, 0, 0)])
            reloads = [call.args[0] for call in process.call_args_list if call.args[0][0] == 'systemctl']
            self.assertEqual(reloads, [['systemctl', 'reload', 'sshd']])


if __name__ == '__main__':
    unittest.main()
