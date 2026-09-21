import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import install as installer
import build_release
import validate_pack


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'pack'
        self.target = self.root / 'target'
        self.source.mkdir()
        (self.source / 'VERSION').write_text('1.0.0\n')
        (self.source / 'pack.json').write_text(json.dumps({
            'version': '1.0.0', 'skills_directory': 'skills', 'credentials_included': False}))
        self.skill('dialpad-api')
        self.skill('dialpad-messaging')

    def skill(self, name, body='A harmless fictional test skill.'):
        folder = self.source / 'skills' / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: An explicitly fictional test skill for installation.\n---\n\n{body}\n')
        return folder

    def installed(self):
        return installer.install(self.source, self.target)

    def test_dry_run_makes_no_target_or_state(self):
        result = installer.install(self.source, self.target, dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertFalse(self.target.exists())
        self.assertEqual([row['status'] for row in result['skills']], ['missing', 'missing'])

    def test_preserves_unrelated_and_repeat_install_is_noop(self):
        unrelated = self.target / 'user-skill'
        unrelated.mkdir(parents=True)
        (unrelated / 'notes.txt').write_text('Retain user work')
        first = self.installed()
        before = installer.manifest(self.target)
        second = self.installed()
        self.assertIn('transaction', first)
        self.assertNotIn('transaction', second)
        self.assertEqual(installer.manifest(self.target), before)
        self.assertEqual((unrelated / 'notes.txt').read_text(), 'Retain user work')

    def test_requires_replace_and_retains_all_old_content(self):
        self.installed()
        (self.target / 'dialpad-api' / 'personal.txt').write_text('Personal changes')
        before = installer.manifest(self.target / 'dialpad-api')
        with self.assertRaises(installer.InstallError):
            self.installed()
        result = installer.install(self.source, self.target, replace=True)
        backup = self.target / installer.STATE / 'backups' / result['transaction'] / 'before' / 'dialpad-api'
        self.assertEqual(installer.manifest(backup), before)
        self.assertFalse((self.target / 'dialpad-api' / 'personal.txt').exists())

    def test_archived_backups_cannot_shadow_skills_in_hermes_discovery(self):
        self.installed()
        self.skill('dialpad-api', 'Updated live skill')
        result = installer.install(self.source, self.target, replace=True)
        archived = self.target / installer.STATE / 'backups' / result['transaction'] / 'before' / 'dialpad-api' / 'SKILL.md'
        self.assertTrue(archived.exists())
        # Hermes excludes .archive explicitly; ordinary dot directories are
        # not excluded. Model that distinction so a generic hidden state root
        # cannot regress and let an old SKILL.md sort ahead of the live one.
        excluded = {'.archive', '.git', '__pycache__'}
        discovered = sorted(path.relative_to(self.target).as_posix()
                            for path in self.target.rglob('SKILL.md')
                            if not any(part in excluded for part in path.relative_to(self.target).parts))
        self.assertEqual(discovered, ['dialpad-api/SKILL.md', 'dialpad-messaging/SKILL.md'])
        self.assertIn('Updated live skill', (self.target / discovered[0]).read_text())

    def test_rollback_restores_exact_backup_and_preserves_replaced_version(self):
        old = self.target / 'dialpad-api'
        old.mkdir(parents=True)
        (old / 'my-note.txt').write_text('User-owned content')
        (old / 'empty').mkdir()
        (old / 'my-note.txt').chmod(0o600)
        before = installer.manifest(old)
        result = installer.install(self.source, self.target, replace=True)
        transaction = result['transaction']
        installer.rollback(self.target, transaction)
        self.assertEqual(installer.manifest(old), before)
        self.assertFalse((self.target / 'dialpad-messaging').exists())
        removed = self.target / installer.STATE / 'backups' / transaction / 'removed'
        self.assertTrue((removed / 'dialpad-api' / 'SKILL.md').is_file())
        self.assertTrue((removed / 'dialpad-messaging' / 'SKILL.md').is_file())
        with self.assertRaises(installer.InstallError):
            installer.rollback(self.target, transaction)

    def test_rollback_dry_run_makes_no_changes(self):
        result = self.installed()
        before = installer.manifest(self.target)
        installer.rollback(self.target, result['transaction'], dry_run=True)
        self.assertEqual(installer.manifest(self.target), before)

    def test_rollback_refuses_user_edits(self):
        result = self.installed()
        changed = self.target / 'dialpad-api' / 'SKILL.md'
        changed.write_text('New user work')
        with self.assertRaisesRegex(installer.InstallError, 'drifted'):
            installer.rollback(self.target, result['transaction'])
        self.assertEqual(changed.read_text(), 'New user work')

    def test_rollback_refuses_backup_tampering(self):
        self.installed()
        self.skill('dialpad-api', 'New version')
        result = installer.install(self.source, self.target, replace=True)
        backup = self.target / installer.STATE / 'backups' / result['transaction'] / 'before' / 'dialpad-api'
        (backup / 'SKILL.md').write_text('Tampered backup')
        before = installer.manifest(self.target / 'dialpad-api')
        with self.assertRaisesRegex(installer.InstallError, 'Backup integrity'):
            installer.rollback(self.target, result['transaction'])
        self.assertEqual(installer.manifest(self.target / 'dialpad-api'), before)

    def test_rollback_refuses_receipt_tampering_and_path_escape(self):
        result = self.installed()
        receipt = Path(result['receipt'])
        data = json.loads(receipt.read_text())
        data['skills']['../../outside'] = data['skills'].pop('dialpad-api')
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(installer.InstallError, 'integrity'):
            installer.rollback(self.target, result['transaction'])
        with self.assertRaises(installer.InstallError):
            installer.rollback(self.target, '../../outside')

    def test_source_and_target_symlink_escapes_refused(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'private.txt').write_text('Keep private')
        (self.source / 'skills' / 'dialpad-api' / 'leak').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(installer.InstallError):
            self.installed()
        (self.source / 'skills' / 'dialpad-api' / 'leak').unlink()
        self.target.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(installer.InstallError):
            self.installed()
        self.assertEqual(list(outside.iterdir()), [outside / 'private.txt'])

    def test_target_skill_and_backup_symlinks_refused(self):
        self.target.mkdir()
        (self.target / 'dialpad-api').symlink_to(self.source / 'skills' / 'dialpad-api', target_is_directory=True)
        with self.assertRaises(installer.InstallError):
            self.installed()
        (self.target / 'dialpad-api').unlink()
        result = self.installed()
        receipt = Path(result['receipt'])
        receipt.unlink()
        receipt.symlink_to(self.source / 'pack.json')
        with self.assertRaises(installer.InstallError):
            installer.rollback(self.target, result['transaction'])

    def test_archive_ancestor_symlink_cannot_escape_target(self):
        self.target.mkdir()
        outside = self.root / 'outside-archive'
        outside.mkdir()
        (self.target / '.archive').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(installer.InstallError, 'Symlink'):
            self.installed()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.target / 'dialpad-api').exists())

    def test_handled_mid_install_failure_restores_every_prior_skill(self):
        self.installed()
        old = {name: installer.manifest(self.target / name) for name in ['dialpad-api', 'dialpad-messaging']}
        self.skill('dialpad-api', 'Updated API')
        self.skill('dialpad-messaging', 'Updated messaging')
        real_replace = os.replace

        def fail_once(source, target):
            if Path(source).parts[-2:] == ('staged', 'dialpad-messaging'):
                raise OSError('Simulated disk failure')
            return real_replace(source, target)

        with patch.object(installer.os, 'replace', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'Simulated'):
                installer.install(self.source, self.target, replace=True)
        for name, expected in old.items():
            self.assertEqual(installer.manifest(self.target / name), expected)

    def test_compiled_cache_not_installed(self):
        cache = self.source / 'skills' / 'dialpad-api' / '__pycache__'
        cache.mkdir()
        (cache / 'cached.pyc').write_bytes(b'compiled test fixture')
        self.installed()
        self.assertFalse((self.target / 'dialpad-api' / '__pycache__').exists())

    def test_check_detects_changes_and_selection_preserves_others(self):
        installer.install(self.source, self.target, names=['dialpad-api'])
        rows = installer.compare(self.source, self.target)
        self.assertEqual([row['status'] for row in rows], ['current', 'missing'])
        with self.assertRaises(installer.InstallError):
            installer.install(self.source, self.target, names=['unknown-skill'])

    def test_existing_lock_refuses_concurrent_install(self):
        (self.target / installer.STATE / 'lock').mkdir(parents=True)
        with self.assertRaisesRegex(installer.InstallError, 'lock exists'):
            self.installed()
        self.assertFalse((self.target / 'dialpad-api').exists())

    def test_missing_provider_catalog_cannot_replace_working_runtime(self):
        self.installed()
        before = installer.manifest(self.target)
        api = self.source / 'skills' / 'dialpad-api'
        (api / 'scripts').mkdir()
        (api / 'scripts' / 'dialpad.py').write_text('# Synthetic runtime fixture\n')
        with self.assertRaisesRegex(installer.InstallError, 'update_catalog.py'):
            installer.install(self.source, self.target, replace=True)
        self.assertEqual(installer.manifest(self.target), before)
        (api / 'references').mkdir()
        (api / 'references' / 'operations.json').write_text('{"operations": {"synthetic": {}}}')
        self.assertIn('transaction', installer.install(self.source, self.target, replace=True))

    def test_companion_dependency_checked_without_removing_other_skills(self):
        self.installed()
        old = installer.manifest(self.target / 'dialpad-api')
        companion = self.root / 'companion'
        entry = companion / 'skills' / 'custom-workflow'
        entry.mkdir(parents=True)
        (entry / 'SKILL.md').write_text('Custom workflow fixture')
        metadata = companion / 'pack.json'
        metadata.write_text(json.dumps({'requires_skills': ['missing-dependency']}))
        with self.assertRaisesRegex(installer.InstallError, 'required skill'):
            installer.install(companion, self.target)
        self.assertFalse((self.target / 'custom-workflow').exists())
        metadata.write_text(json.dumps({'requires_skills': ['dialpad-api']}))
        receipt = installer.install(companion, self.target)
        self.assertEqual(installer.manifest(self.target / 'dialpad-api'), old)
        installer.rollback(self.target, receipt['transaction'])
        self.assertFalse((self.target / 'custom-workflow').exists())
        self.assertEqual(installer.manifest(self.target / 'dialpad-api'), old)

    def test_companion_dependency_path_escape_is_rejected(self):
        metadata = self.source / 'pack.json'
        value = json.loads(metadata.read_text())
        value['requires_skills'] = ['../outside']
        metadata.write_text(json.dumps(value))
        with self.assertRaisesRegex(installer.InstallError, 'dependencies'):
            self.installed()
        self.assertFalse(self.target.exists())

    def test_release_is_deterministic_and_tampering_is_detected(self):
        first = build_release.build(self.source, self.root / 'one')
        second = build_release.build(self.source, self.root / 'two')
        self.assertEqual(Path(first['archive']).read_bytes(), Path(second['archive']).read_bytes())
        self.assertTrue(build_release.verify(Path(first['archive']))['verified'])
        with Path(first['archive']).open('ab') as output:
            output.write(b'tampered')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            build_release.verify(Path(first['archive']))

    def test_validation_rejects_broken_links_secrets_and_symlinks(self):
        skill = self.skill('dialpad-api', '[missing](references/absent.md)')
        self.assertFalse(validate_pack.validate(self.source)['valid'])
        self.skill('dialpad-api')
        (skill / '.env').write_text('DIALPAD_API_KEY=fictional_placeholder')
        self.assertFalse(validate_pack.validate(self.source)['valid'])
        (skill / '.env').unlink()
        (skill / 'outside').symlink_to(self.target)
        self.assertFalse(validate_pack.validate(self.source)['valid'])

    def test_validation_rejects_nonfictional_customer_identifiers(self):
        # Concatenate fixtures so the pack's own hygiene scanner sees no real-looking value.
        for value in ['fictional_person' + '@' + 'private.invalid', '+' + '12123456789', 'job_' + 'a' * 32]:
            self.skill('dialpad-api', value)
            self.assertFalse(validate_pack.validate(self.source)['valid'])


if __name__ == '__main__':
    unittest.main()
