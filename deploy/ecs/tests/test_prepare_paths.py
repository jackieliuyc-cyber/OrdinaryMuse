import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('prepare_paths', Path(__file__).resolve().parents[1] / 'prepare_paths.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.modules['prepare_paths'] = module
activation_spec = importlib.util.spec_from_file_location('activate_paths', Path(__file__).resolve().parents[1] / 'activate_paths.py')
activation = importlib.util.module_from_spec(activation_spec)
activation_spec.loader.exec_module(activation)


class PathReleaseTests(unittest.TestCase):
    def test_score_form_and_javascript_share_the_relative_action(self):
        index, javascript = module.score_sources('<form action="/api/feedback">', "const response = await fetch('/api/feedback', {")
        self.assertIn('action="./api/feedback"', index)
        self.assertIn('fetch(feedbackForm.action, {', javascript)
        self.assertEqual(module.score_sources(index, javascript), (index, javascript))

    def test_unknown_upstream_markup_is_not_silently_rewritten(self):
        with self.assertRaises(ValueError):
            module.score_sources('<form action="/other">', "fetch('/api/feedback', {")

    def test_build_preserves_original_assets_and_outputs_a_verifiable_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            score, audio, release = root / 'original-score', root / 'original-audio', root / 'release'
            score.mkdir()
            audio.mkdir()
            (score / 'index.html').write_text('<form action="/api/feedback">')
            (score / 'feedback-form.js').write_text("fetch('/api/feedback', {")
            (score / 'demo.mp4').write_bytes(b'video-fixture')
            for name in ('index.html', 'app.js', 'style.css'):
                (audio / name).write_text('audio-' + name)
            manifest = module.prepare(score, audio, release)
            self.assertEqual((score / 'index.html').read_text(), '<form action="/api/feedback">')
            self.assertEqual((release / 'score/demo.mp4').read_bytes(), b'video-fixture')
            self.assertEqual(json.loads((release / 'manifest.json').read_text()), manifest)
            for name, digest in manifest.items():
                self.assertEqual(hashlib.sha256((release / name).read_bytes()).hexdigest(), digest)
            with self.assertRaises(FileExistsError):
                module.prepare(score, audio, release)

    def test_failed_readiness_restores_previous_config_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            staging = base / 'staging'
            staging.mkdir()
            (staging / 'nginx.conf').write_text('new-private-config')
            config = base / 'nginx.conf'
            config.write_text('old-private-config')
            active = base / 'paths-current'
            active.symlink_to(base / 'previous-release')

            def prepare_release(score, audio, release):
                release.mkdir(parents=True)
                return {}

            with patch.object(activation, 'BASE', base), patch.object(activation, 'CONFIG', config), \
                 patch.object(activation, 'ACTIVE', active), patch.object(activation, '__file__', str(staging / 'activate_paths.py')), \
                 patch.object(activation, 'prepare', side_effect=prepare_release), \
                 patch.object(activation.subprocess, 'run') as process, \
                 patch.object(activation, 'verify', side_effect=RuntimeError('readiness failed')):
                with self.assertRaisesRegex(RuntimeError, 'readiness failed'):
                    activation.activate('new-release', hashlib.sha256(config.read_bytes()).hexdigest())
            self.assertEqual(config.read_text(), 'old-private-config')
            self.assertEqual(os.readlink(active), str(base / 'previous-release'))
            self.assertEqual((staging / 'nginx.before.conf').read_text(), 'old-private-config')
            reloads = [call.args[0] for call in process.call_args_list if call.args[0][:2] == ['systemctl', 'reload']]
            self.assertEqual(reloads, [['systemctl', 'reload', 'ordinary-muse-h5']] * 2)

    def test_concurrent_config_change_is_rejected_before_any_release_write(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'nginx.conf'
            config.write_text('changed config')
            with patch.object(activation, 'CONFIG', config), patch.object(activation, 'prepare') as prepare:
                with self.assertRaisesRegex(RuntimeError, 'changed since preflight'):
                    activation.activate('new-release', 'unexpected-hash')
            prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
