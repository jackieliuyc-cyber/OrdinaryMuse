"""Exercise install/lifecycle boundaries without touching launchd or a real ECS."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("mac_worker", Path(__file__).resolve().parents[1] / "worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ordinary-muse-mac-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / 'Application Support/worker & "quoted"'
        self.agents = self.root / "LaunchAgents"
        for attribute, value in (("STATE", self.state), ("AGENTS", self.agents)):
            patcher = patch.object(worker, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = {"ecs_host": "ecs.example.com", "ecs_user": "muse-tunnel", "ssh_port": 22,
                       "remote_port": 19000, "local_port": 8000, "device": "auto", "wait_seconds": 0,
                       "identity_file": str(self.state / "ssh/id_ed25519"),
                       "project_dir": str(self.root / "Project with spaces & 中文"),
                       "ffmpeg_dir": "/opt/homebrew/bin", "cert_file": str(self.state / "ca.pem")}

    def result(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def test_invalid_host_user_and_ports_are_rejected(self):
        for key, values in {
            "ecs_host": ["-oProxyCommand=evil", "user@host", "https://host", "host;echo x", "a\nb"],
            "ecs_user": ["-root", "user;id", "user@host"],
            "ssh_port": [0, 65536, True],
            "remote_port": [22, 1023, 65536, "19000"],
            "local_port": [-1, 80, 70000],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    worker.validate({**self.config, key: value})
        with self.assertRaises(ValueError):
            worker.validate({**self.config, "ssh_port": 19000})

    @unittest.skipUnless(Path("/usr/bin/ssh").exists(), "requires OpenSSH client")
    def test_ssh_parses_private_known_hosts_path_with_spaces_and_quotes(self):
        # -G parses config without connecting. Override even the user's SSH config.
        worker.private_dir(self.state / "ssh")
        Path(self.config["identity_file"]).touch()
        command = worker.ssh_args(self.config) + [worker.destination(self.config)]
        result = subprocess.run(command[:1] + ["-G", "-F", "/dev/null"] + command[1:],
                                capture_output=True, text=True, check=True)
        lines = result.stdout.splitlines()
        known_hosts = next(line for line in lines if line.startswith("userknownhostsfile "))
        self.assertEqual(known_hosts.removeprefix("userknownhostsfile "), str(self.state / "ssh/known_hosts"))
        self.assertIn("stricthostkeychecking true", lines)
        self.assertIn("batchmode yes", lines)
        self.assertIn("port 22", lines)

    def test_plists_round_trip_paths_and_bind_only_loopback(self):
        plists = worker.agent_plists(self.config)
        for name, data in plists.items():
            path = self.agents / f"{worker.LABELS[name]}.plist"
            worker.write_file(path, plistlib.dumps(data))
            self.assertEqual(plistlib.loads(path.read_bytes()), data)
            if Path("/usr/bin/plutil").exists():
                subprocess.run(["/usr/bin/plutil", "-lint", str(path)], check=True, capture_output=True)
        model = plists["model"]["ProgramArguments"]
        self.assertEqual(model[model.index("--host") + 1], "127.0.0.1")
        self.assertEqual(model[2], str(self.state / "venv/bin/python"))
        self.assertNotIn("--reload", model)
        tunnel = plists["tunnel"]["ProgramArguments"]
        self.assertIn("127.0.0.1:19000:127.0.0.1:8000", tunnel)
        self.assertIn("ExitOnForwardFailure=yes", tunnel)
        self.assertNotIn("-f", tunnel)  # launchd must own the foreground SSH process.
        self.assertTrue(plists["tunnel"]["KeepAlive"])
        self.assertIn("ServerAliveInterval=30", tunnel)

    @unittest.skipUnless(Path("/usr/bin/ssh-keygen").exists(), "requires ssh-keygen")
    def test_key_is_created_privately_and_existing_identity_is_preserved(self):
        public_key = worker.ensure_key(self.config)
        key = Path(self.config["identity_file"])
        first = key.read_bytes()
        self.assertTrue(public_key.startswith("ssh-ed25519 "))
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.joinpath("ssh").stat().st_mode & 0o777, 0o700)
        self.assertEqual(worker.ensure_key(self.config), public_key)
        self.assertEqual(key.read_bytes(), first)
        missing = {**self.config, "identity_file": str(self.root / "external-key")}
        with self.assertRaisesRegex(RuntimeError, "不存在"):
            worker.ensure_key(missing)
        self.assertFalse(Path(missing["identity_file"]).exists())

    def test_authorization_preserves_existing_keys_and_quotes_public_key(self):
        home = self.root / "remote-home"
        (home / ".ssh").mkdir(parents=True)
        authorized = home / ".ssh/authorized_keys"
        authorized.write_text("existing-authorized-key\n")
        public_key = "ssh-ed25519 AAAATEST user 'quote' $(touch SHOULD_NOT_EXIST)"
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if "BatchMode=no" in args:
                # Execute only the generated remote shell fragment in a temp HOME.
                subprocess.run(["/bin/sh", "-c", args[-1]], check=True,
                               cwd=home, env={**os.environ, "HOME": str(home)})
                return self.result()
            return self.result(0 if len(calls) % 3 == 0 else 255)

        with patch.object(worker, "run", side_effect=fake_run), redirect_stdout(io.StringIO()):
            worker.authorize(self.config, public_key)
            worker.authorize(self.config, public_key)
        self.assertEqual(authorized.read_text().splitlines(), ["existing-authorized-key", public_key])
        self.assertEqual(authorized.stat().st_mode & 0o777, 0o600)
        self.assertFalse((home / "SHOULD_NOT_EXIST").exists())
        self.assertIn("StrictHostKeyChecking=ask", calls[1])
        self.assertIn("StrictHostKeyChecking=yes", calls[2])

    def test_pre_authorized_key_never_writes_remote_files(self):
        with patch.object(worker, "run", return_value=self.result()) as execute:
            worker.authorize(self.config, "ssh-ed25519 TEST")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.args[0][-1], "true")

    def test_no_authorize_does_not_append_a_key_even_on_first_connection(self):
        with patch.object(worker, "run", side_effect=[self.result(255), self.result(), self.result()]) as execute:
            with redirect_stdout(io.StringIO()):
                worker.authorize(self.config, "ssh-ed25519 TEST", no_authorize=True)
        self.assertTrue(all(call.args[0][-1] == "true" for call in execute.call_args_list))

    def test_authentication_failure_retains_config_without_starting_jobs(self):
        options = ["setup", "--ecs-host", "ecs.example.com", "--ecs-user", "muse-tunnel"]
        with patch.object(worker, "agent_info", return_value={"loaded": False}), \
             patch.object(worker.sys, "platform", "darwin"), \
             patch.object(worker.os, "geteuid", return_value=501), \
             patch.object(worker, "ensure_key", return_value="ssh-ed25519 TEST"), \
             patch.object(worker, "authorize", side_effect=RuntimeError("SSH denied")), \
             patch.object(worker, "install_dependencies") as dependencies, \
             patch.object(worker, "start") as start:
            with self.assertRaisesRegex(RuntimeError, "SSH denied"):
                worker.main(options)
        dependencies.assert_not_called()
        start.assert_not_called()
        config_path = self.state / "config.json"
        self.assertEqual(json.loads(config_path.read_text())["ecs_host"], "ecs.example.com")
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

    def prepare_worker_paths(self):
        python = self.state / "venv/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        server = Path(self.config["project_dir"]) / "demucs-server/server.py"
        server.parent.mkdir(parents=True)
        server.touch()

    def test_start_refuses_an_occupied_port_without_stopping_other_processes(self):
        self.prepare_worker_paths()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.config["local_port"] = listener.getsockname()[1]
            with patch.object(worker, "agent_info", return_value={"loaded": False}), \
                 patch.object(worker, "run") as execute:
                with self.assertRaisesRegex(RuntimeError, "已被其他程序占用"):
                    worker.start(self.config)
            execute.assert_not_called()
            self.assertTrue(listener.fileno() >= 0)

    def test_start_stop_only_manage_our_labels_and_stop_disables_login_startup(self):
        self.prepare_worker_paths()
        self.agents.mkdir(parents=True)
        unrelated = self.agents / "com.other.service.plist"
        unrelated.write_text("keep me")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.config["local_port"] = probe.getsockname()[1]
        with patch.object(worker, "agent_info", return_value={"loaded": False}), \
             patch.object(worker, "run", return_value=self.result()) as execute, redirect_stdout(io.StringIO()):
            worker.start(self.config)
        bootstrap = [call.args[0] for call in execute.call_args_list if "bootstrap" in call.args[0]]
        self.assertEqual(len(bootstrap), 2)
        self.assertTrue(all(path.endswith(".plist") for *_, path in bootstrap))
        with patch.object(worker, "agent_info", return_value={"loaded": True}), \
             patch.object(worker, "run", return_value=self.result()) as execute, redirect_stdout(io.StringIO()):
            worker.stop(uninstall=True)
        commands = [call.args[0] for call in execute.call_args_list]
        self.assertEqual([command[1] for command in commands], ["disable", "bootout", "disable", "bootout"])
        self.assertTrue(all(command[-1] in [f"{worker.DOMAIN}/{label}" for label in worker.LABELS.values()]
                            for command in commands))
        self.assertEqual(unrelated.read_text(), "keep me")
        self.assertTrue((self.state / "venv/bin/python").exists())

    def test_status_cannot_report_an_unrelated_remote_service_as_our_tunnel(self):
        info = {"loaded": True, "pid": None, "exit": 255}
        with patch.object(worker, "agent_info", return_value=info), \
             patch.object(worker, "local_health", return_value=True), \
             patch.object(worker, "remote_health", return_value=True) as remote, redirect_stdout(io.StringIO()):
            self.assertFalse(worker.status(self.config))
        remote.assert_not_called()

    def test_timeout_is_failure_and_leaves_jobs_running_for_model_download(self):
        with patch.object(worker, "local_health", return_value=False), \
             patch.object(worker, "status", return_value=False) as status, \
             patch.object(worker, "stop") as stop, redirect_stdout(io.StringIO()):
            self.assertFalse(worker.wait_ready(self.config))
        status.assert_called_once()
        stop.assert_not_called()

    def test_health_checks_reject_extraction_only_or_malformed_responses(self):
        self.assertTrue(worker.healthy({"status": "healthy", "model": "htdemucs_ft"}))
        self.assertFalse(worker.healthy({"status": "healthy", "separation_enabled": False}))
        self.assertFalse(worker.healthy({"status": "loading"}))
        self.assertFalse(worker.healthy(["healthy"]))
        with patch.object(worker, "run", return_value=self.result(stdout="not-json")):
            self.assertFalse(worker.remote_health(self.config))
        with patch.object(worker, "run", side_effect=subprocess.TimeoutExpired("ssh", 25)):
            self.assertFalse(worker.remote_health(self.config))


if __name__ == "__main__":
    unittest.main()
