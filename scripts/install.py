#!/usr/bin/env python3
"""Install portable skills without touching unrelated skills or credentials.

Each skill is replaced with an atomic directory rename on the target filesystem.
An interrupted multi-skill install can be inspected in the transaction receipt;
handled failures restore all previously replaced skills. Existing content is
always retained in a backup. Rollback refuses any post-install drift.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
# Hermes explicitly excludes .archive while recursively discovering SKILL.md.
# A generic hidden directory is not sufficient: backups must never shadow the
# newly installed skills in the runtime index.
STATE = '.archive/dialpad-skill-pack'
NAME = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
TRANSACTION = re.compile(r'^[a-f0-9]{32}$')


class InstallError(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def assert_safe_path(path):
    """Reject symlinks in every existing path component, including ancestors."""
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    for item in [*reversed(path.parents), path]:
        if item.is_symlink():
            raise InstallError(f'Symlink paths are refused: {item}')
        if item.exists() and item != path and not item.is_dir():
            raise InstallError(f'Parent is not a directory: {item}')
    return path


def manifest(path, package=False):
    """Inventory files, directories, permissions and bytes; no links/devices."""
    path = assert_safe_path(path)
    if not path.is_dir():
        raise InstallError(f'Expected a directory: {path}')
    entries = {}
    for item in [path, *sorted(path.rglob('*'))]:
        relative = item.relative_to(path)
        if package and (any(part in {'__pycache__', '.pytest_cache'} for part in relative.parts)
                        or item.suffix in {'.pyc', '.pyo'} or item.name == '.DS_Store'):
            continue
        mode = item.lstat().st_mode
        key = '.' if item == path else item.relative_to(path).as_posix()
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise InstallError(f'Symlinks and special files are refused: {item}')
        entry = {'kind': 'directory' if stat.S_ISDIR(mode) else 'file',
                 'mode': stat.S_IMODE(mode)}
        if stat.S_ISREG(mode):
            data = item.read_bytes()
            entry.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        entries[key] = entry
    return {'entries': entries, 'sha256': hashlib.sha256(canonical(entries)).hexdigest()}


def discover(source):
    source = assert_safe_path(source)
    skills = source / 'skills'
    assert_safe_path(skills)
    if not skills.is_dir():
        raise InstallError(f'No skills directory: {skills}')
    found = {}
    for child in sorted(skills.iterdir()):
        if child.is_symlink():
            raise InstallError(f'Skill symlink is refused: {child}')
        if child.is_dir() and (child / 'SKILL.md').is_file():
            if not NAME.fullmatch(child.name):
                raise InstallError(f'Unsafe skill name: {child.name}')
            found[child.name] = manifest(child, package=True)
    if not found:
        raise InstallError('The source contains no skills.')
    return found


def select(source, names=None):
    found = discover(source)
    names = sorted(set(names or found))
    unknown = set(names) - set(found)
    if unknown:
        raise InstallError('Unknown skills: ' + ', '.join(sorted(unknown)))
    return {name: found[name] for name in names}


def compare(source, target, names=None):
    target = assert_safe_path(target)
    if target.exists() and not target.is_dir():
        raise InstallError('Target root must be a directory.')
    results = []
    for name, desired in select(source, names).items():
        dest = assert_safe_path(target / name)
        current = manifest(dest) if dest.exists() else None
        status = 'current' if current == desired else 'different' if current else 'missing'
        results.append({'skill': name, 'status': status, 'source_sha256': desired['sha256'],
                        'installed_sha256': current['sha256'] if current else None})
    return results


def write_json(path, value):
    assert_safe_path(path)
    temp = path.with_name(path.name + '.new')
    assert_safe_path(temp)
    with temp.open('xb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def write_receipt(folder, receipt):
    write_json(folder / 'receipt.json', receipt)
    write_json(folder / 'receipt.sha256.json', {
        'sha256': hashlib.sha256(canonical(receipt)).hexdigest()})


@contextmanager
def locked(target):
    state = assert_safe_path(target / STATE)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = assert_safe_path(state / 'lock')
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise InstallError(f'Installer lock exists: {lock}. Inspect an interrupted install before removing it.') from exc
    try:
        yield state
    finally:
        lock.rmdir()


def install(source, target, names=None, replace=False, dry_run=False, runtime='codex'):
    source, target = assert_safe_path(source), assert_safe_path(target)
    desired = select(source, names)
    # The provider's catalog is downloaded by the user, not redistributed with
    # the MIT-licensed source. Never replace a working API skill with an
    # unbootstrapped copy from a fresh clone or release ZIP.
    api = source / 'skills' / 'dialpad-api'
    if 'dialpad-api' in desired and (api / 'scripts' / 'dialpad.py').is_file():
        catalog_path = api / 'references' / 'operations.json'
        if not catalog_path.is_file():
            raise InstallError('API catalog is missing. Run python3 scripts/update_catalog.py from the public pack root before installing.')
        try:
            catalog = json.loads(catalog_path.read_text())
            if not isinstance(catalog.get('operations'), dict) or not catalog['operations']:
                raise ValueError('No operations found')
        except (ValueError, TypeError, AttributeError) as exc:
            raise InstallError('API catalog is invalid. Regenerate it with python3 scripts/update_catalog.py before installing.') from exc
    metadata_path = source / 'pack.json'
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    requires = metadata.get('requires_skills', [])
    if not isinstance(requires, list) or any(not isinstance(name, str) or not NAME.fullmatch(name) for name in requires):
        raise InstallError('Pack dependencies must be a list of safe skill names.')
    for dependency in requires:
        if dependency not in desired:
            entrypoint = assert_safe_path(target / dependency / 'SKILL.md')
            if not entrypoint.is_file():
                raise InstallError(f'Install the required skill first: {dependency}')
    plan = compare(source, target, names)
    changes = [row for row in plan if row['status'] != 'current']
    if any(row['status'] == 'different' for row in changes) and not replace:
        raise InstallError('Existing skill content differs. Use --replace to back it up and install the selected pack.')
    result = {'runtime': runtime, 'target_root': str(target), 'dry_run': dry_run, 'skills': plan}
    if dry_run or not changes:
        return result
    target.mkdir(parents=True, exist_ok=True)
    with locked(target) as state:
        # Recheck after acquiring the lock; abort if any preflight facts changed.
        if compare(source, target, names) != plan or select(source, names) != desired:
            raise InstallError('Source or target changed during preflight; no skills were replaced.')
        transaction = uuid.uuid4().hex
        folder = assert_safe_path(state / 'backups' / transaction)
        folder.mkdir(parents=True, mode=0o700)
        (folder / 'before').mkdir()
        (folder / 'staged').mkdir()
        (folder / 'removed').mkdir()
        receipt = {'schema': 1, 'transaction': transaction, 'runtime': runtime,
                   'target_root': str(target), 'status': 'prepared', 'skills': {}}
        for row in changes:
            name = row['skill']
            dest = target / name
            old = manifest(dest) if dest.exists() else None
            staged = folder / 'staged' / name
            shutil.copytree(source / 'skills' / name, staged,
                            ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '*.pyc', '*.pyo', '.DS_Store'))
            if manifest(staged) != desired[name]:
                raise InstallError('Source changed during staging; target content has not been replaced.')
            receipt['skills'][name] = {'before': old, 'after': desired[name]}
        write_receipt(folder, receipt)
        touched = []
        try:
            for name, facts in receipt['skills'].items():
                dest = target / name
                if (manifest(dest) if dest.exists() else None) != facts['before']:
                    raise InstallError(f'Target changed before replacement: {name}')
                touched.append(name)
                if facts['before'] is not None:
                    os.replace(dest, folder / 'before' / name)
                os.replace(folder / 'staged' / name, dest)
            receipt['status'] = 'installed'
            write_receipt(folder, receipt)
        except Exception:
            for name in reversed(touched):
                dest = target / name
                staged = folder / 'staged' / name
                backup = folder / 'before' / name
                if not staged.exists() and dest.exists():
                    os.replace(dest, folder / 'removed' / name)
                if backup.exists():
                    os.replace(backup, dest)
            receipt['status'] = 'failed-restored'
            write_receipt(folder, receipt)
            raise
        result.update(transaction=transaction, receipt=str(folder / 'receipt.json'))
    return result


def read_receipt(target, transaction):
    if not TRANSACTION.fullmatch(transaction):
        raise InstallError('Transaction must be the 32-character ID returned by install.')
    folder = assert_safe_path(target / STATE / 'backups' / transaction)
    try:
        receipt = json.loads(assert_safe_path(folder / 'receipt.json').read_text())
        digest = json.loads(assert_safe_path(folder / 'receipt.sha256.json').read_text())
    except (OSError, ValueError) as exc:
        raise InstallError('Missing or invalid backup receipt.') from exc
    if digest.get('sha256') != hashlib.sha256(canonical(receipt)).hexdigest():
        raise InstallError('Backup receipt integrity check failed.')
    if (receipt.get('schema') != 1 or receipt.get('transaction') != transaction or
            receipt.get('target_root') != str(target) or receipt.get('status') != 'installed'):
        raise InstallError('Receipt does not identify a completed installation at this target.')
    skills = receipt.get('skills')
    if not isinstance(skills, dict) or not skills or any(not NAME.fullmatch(name) for name in skills):
        raise InstallError('Invalid skill names in receipt.')
    return folder, receipt


def rollback(target, transaction, dry_run=False):
    target = assert_safe_path(target)
    folder, receipt = read_receipt(target, transaction)

    def preflight():
        for name, facts in receipt['skills'].items():
            dest = target / name
            if not dest.exists() or manifest(dest) != facts['after']:
                raise InstallError(f'Installed skill has drifted; preserve/reconcile it before rollback: {name}')
            backup = folder / 'before' / name
            if facts['before'] is not None:
                if not backup.exists() or manifest(backup) != facts['before']:
                    raise InstallError(f'Backup integrity check failed: {name}')
            elif backup.exists():
                raise InstallError(f'Unexpected backup content: {name}')
            removed = assert_safe_path(folder / 'removed' / name)
            if removed.exists():
                raise InstallError(f'Rollback destination already exists: {removed}')

    preflight()
    result = {'transaction': transaction, 'target_root': str(target), 'dry_run': dry_run,
              'skills': sorted(receipt['skills'])}
    if dry_run:
        return result
    with locked(target):
        _, reloaded = read_receipt(target, transaction)
        if reloaded != receipt:
            raise InstallError('Receipt changed during rollback preflight.')
        preflight()
        moved = []
        try:
            for name, facts in receipt['skills'].items():
                dest = target / name
                os.replace(dest, folder / 'removed' / name)
                moved.append(name)
                if facts['before'] is not None:
                    os.replace(folder / 'before' / name, dest)
            receipt['status'] = 'rolled-back'
            write_receipt(folder, receipt)
        except Exception:
            for name in reversed(moved):
                dest = target / name
                backup = folder / 'before' / name
                if dest.exists():
                    os.replace(dest, backup)
                os.replace(folder / 'removed' / name, dest)
            receipt['status'] = 'installed'
            write_receipt(folder, receipt)
            raise
    result['status'] = 'rolled-back'
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=ROOT, help='Pack root (default: this repository).')
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='List dynamically discovered skills and content hashes.')
    for command in ['check', 'install']:
        sub = commands.add_parser(command, help='Compare installed skills.' if command == 'check' else 'Install with retained backups.')
        sub.add_argument('--target-root', type=Path, required=True, help='Explicit runtime skills directory; never inferred.')
        sub.add_argument('--skill', action='append', help='Select a skill (repeatable); default all. Install dependencies together.')
        if command == 'install':
            sub.add_argument('--runtime', choices=['codex', 'hermes'], required=True, help='Recorded in receipt; target path remains explicit.')
            sub.add_argument('--replace', action='store_true', help='Replace differing skills after backing them up; unrelated skills are untouched.')
            sub.add_argument('--dry-run', action='store_true', help='Show the plan without writing anything.')
    sub = commands.add_parser('rollback', help='Restore an exact backup; refuses installed or backup drift.')
    sub.add_argument('--target-root', type=Path, required=True)
    sub.add_argument('--transaction', required=True, help='ID returned by the installation to reverse.')
    sub.add_argument('--dry-run', action='store_true')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        code = 0
        if args.command == 'list':
            output = {'skills': [{'name': name, 'sha256': value['sha256']} for name, value in discover(args.source).items()]}
        elif args.command == 'check':
            output = {'skills': compare(args.source, args.target_root, args.skill)}
            code = int(any(row['status'] != 'current' for row in output['skills']))
        elif args.command == 'install':
            from validate_pack import validate
            validation = validate(args.source)
            if not validation['valid']:
                raise InstallError('Package validation failed: ' + '; '.join(validation['errors']))
            output = install(args.source, args.target_root, args.skill, args.replace, args.dry_run, args.runtime)
        else:
            output = rollback(args.target_root, args.transaction, args.dry_run)
        print(json.dumps(output, indent=2, sort_keys=True))
        return code
    except (InstallError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
