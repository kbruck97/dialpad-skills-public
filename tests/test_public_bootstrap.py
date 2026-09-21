"""Public distributions omit provider data and bootstrap it locally without credentials."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_release
import update_catalog
import validate_pack


def synthetic_spec():
    return {
        'openapi': '3.1.0', 'info': {'version': 'fictional-test'},
        'paths': {'/api/v2/fictional': {'get': {
            'operationId': 'fictional.list',
            'responses': {'200': {'description': 'Fictional success'}},
        }}},
    }


class PublicBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'pack'
        self.source.mkdir()
        (self.source / 'VERSION').write_text('1.1.0\n')
        (self.source / 'pack.json').write_text(json.dumps({
            'version': '1.1.0', 'skills_directory': 'skills', 'credentials_included': False}))
        (self.source / 'THIRD_PARTY_NOTICES.md').write_text(
            'Provider data is generated locally and excluded from the distribution.\n')
        self.skill = self.source / 'skills/dialpad-api'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text(
            '---\nname: dialpad-api\ndescription: An explicitly fictional test skill for public packaging.\n---\n')
        self.catalog_path = self.skill / 'references/operations.json'
        self.fixture_path = self.root / 'synthetic-spec.json'
        self.fixture_bytes = (json.dumps(synthetic_spec()) + '\n').encode()
        self.fixture_path.write_bytes(self.fixture_bytes)

    def bootstrap(self):
        with patch.object(update_catalog, 'urlopen', side_effect=AssertionError('No network allowed')):
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
                result = update_catalog.main([
                    '--source-file', str(self.fixture_path), '--output', str(self.catalog_path),
                    '--checked-at', '2026-09-21'])
        self.assertEqual(result, 0)

    def copy_runner(self):
        destination = self.skill / 'scripts/dialpad.py'
        destination.parent.mkdir()
        shutil.copy2(ROOT / 'skills/dialpad-api/scripts/dialpad.py', destination)
        return destination

    def test_clean_distribution_valid_before_credential_free_bootstrap(self):
        result = validate_pack.validate(self.source)
        self.assertTrue(result['valid'], result['errors'])
        self.assertFalse(result['catalog']['generated'])
        self.bootstrap()
        generated = json.loads(self.catalog_path.read_text())
        self.assertEqual(generated['source_sha256'], hashlib.sha256(self.fixture_bytes).hexdigest())
        self.assertEqual(generated['coverage']['rest_operations'], 1)
        result = validate_pack.validate(self.source)
        self.assertTrue(result['valid'], result['errors'])
        self.assertTrue(result['catalog']['generated'])
        self.assertFalse(result['catalog']['included_in_release'])

    def test_public_archive_omits_provider_files_and_includes_notices(self):
        before = build_release.build(self.source, self.root / 'before')
        self.bootstrap()
        self.catalog_path.with_suffix('.md').write_text('Legacy local provider inventory.\n')
        after = build_release.build(self.source, self.root / 'after')
        self.assertEqual(before['sha256'], after['sha256'])
        with zipfile.ZipFile(after['archive']) as archive:
            names = archive.namelist()
            self.assertIn('dialpad-skills-1.1.0/THIRD_PARTY_NOTICES.md', names)
            self.assertFalse(any(name.endswith('/operations.json') or name.endswith('/operations.md')
                                 for name in names))
        self.assertTrue(build_release.verify(after['archive'])['verified'])

    def test_verify_rejects_provider_catalog_even_with_valid_integrity_manifest(self):
        built = build_release.build(self.source, self.root / 'dist')
        archive_path = Path(built['archive'])
        with zipfile.ZipFile(archive_path) as archive:
            payload = {name: archive.read(name) for name in archive.namelist()}
            metadata = {entry.filename: entry for entry in archive.infolist()}
        prefix = 'dialpad-skills-1.1.0/'
        provider_name = 'skills/dialpad-api/references/operations.json'
        data = b'{}'
        payload[prefix + provider_name] = data
        manifest_path = prefix + 'release-manifest.json'
        manifest = json.loads(payload[manifest_path])
        manifest['files'][provider_name] = {
            'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'mode': '0644'}
        payload[manifest_path] = json.dumps(manifest).encode()
        with zipfile.ZipFile(archive_path, 'w') as archive:
            for name, content in payload.items():
                info = metadata.get(name)
                if info is None:
                    info = zipfile.ZipInfo(name, build_release.STAMP)
                    info.create_system = 3
                    info.external_attr = (0o100644 << 16)
                archive.writestr(info, content)
        archive_path.with_suffix('.zip.sha256').write_text(
            hashlib.sha256(archive_path.read_bytes()).hexdigest() + '  ' + archive_path.name + '\n')
        with self.assertRaisesRegex(ValueError, 'provider catalog'):
            build_release.verify(archive_path)

    def test_missing_catalog_cli_explains_bootstrap_without_traceback(self):
        runner = self.copy_runner()
        result = subprocess.run([sys.executable, str(runner), 'catalog'],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Traceback', result.stderr)
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt['bootstrap_required'])
        self.assertIn('python3 scripts/update_catalog.py', receipt['error'])
        self.assertIn('no API credential needed', receipt['error'])
        self.assertIn('reinstall', receipt['error'])

    def test_missing_catalog_precedes_connection_and_credentials(self):
        runner = self.copy_runner()
        spec = importlib.util.spec_from_file_location('missing_catalog_runner', runner)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, 'resolve_connection', side_effect=AssertionError('No connection allowed')):
            with patch.object(module, 'load_key', side_effect=AssertionError('No credentials allowed')):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    result = module.main(['doctor', '--live'])
        self.assertEqual(result, 1)
        self.assertTrue(json.loads(output.getvalue())['bootstrap_required'])

    def test_bootstrapped_runner_discovers_synthetic_catalog_without_credentials(self):
        runner = self.copy_runner()
        self.bootstrap()
        result = subprocess.run([sys.executable, str(runner), 'catalog'],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['operations'][0]['operation'], 'fictional.list')

    def test_check_missing_catalog_does_not_download_or_claim_drift(self):
        with patch.object(update_catalog, 'urlopen', side_effect=AssertionError('No network allowed')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = update_catalog.main(['--check', '--output', str(self.catalog_path)])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'missing')
        self.assertFalse(self.catalog_path.exists())

    def test_check_detects_source_drift_without_rewriting_local_catalog(self):
        self.bootstrap()
        original = self.catalog_path.read_bytes()
        changed = synthetic_spec()
        changed['paths']['/api/v2/fictional']['get']['deprecated'] = True
        self.fixture_path.write_text(json.dumps(changed))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = update_catalog.main(['--check', '--source-file', str(self.fixture_path),
                                          '--output', str(self.catalog_path)])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'drift')
        self.assertEqual(self.catalog_path.read_bytes(), original)

    def test_invalid_local_generated_catalog_fails_validation(self):
        self.catalog_path.parent.mkdir()
        self.catalog_path.write_text('{}')
        result = validate_pack.validate(self.source)
        self.assertFalse(result['valid'])
        self.assertTrue(any('generated API catalog' in message for message in result['errors']))


if __name__ == '__main__':
    unittest.main()
