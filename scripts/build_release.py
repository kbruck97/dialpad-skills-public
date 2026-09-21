#!/usr/bin/env python3
"""Build or verify a deterministic, credential-free code/documentation ZIP.

Build: python scripts/build_release.py --out-dir dist
Verify: python scripts/build_release.py --verify dist/dialpad-skills-VERSION.zip
ZIP_STORED plus fixed timestamps avoids compressor/platform nondeterminism.
The adjacent .sha256 file and embedded manifest detect accidental tampering;
they are integrity checks, not a cryptographic publisher signature.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

from validate_pack import GENERATED_PROVIDER_FILES, release_files, validate

ROOT = Path(__file__).resolve().parents[1]
STAMP = (1980, 1, 1, 0, 0, 0)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(root, out_dir):
    root, out_dir = Path(root).resolve(), Path(out_dir)
    result = validate(root)
    if not result['valid']:
        raise ValueError('Pack validation failed: ' + '; '.join(result['errors']))
    version = (root / 'VERSION').read_text().strip()
    prefix = f'dialpad-skills-{version}'
    payload = {}
    for path in release_files(root):
        relative = path.relative_to(root).as_posix()
        payload[relative] = path.read_bytes()
    manifest = {'schema': 1, 'name': 'dialpad-skills', 'version': version,
                'files': {name: {'sha256': digest(data), 'size': len(data), 'mode': '0644'}
                          for name, data in sorted(payload.items())}}
    payload['release-manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f'{prefix}.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as output:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(f'{prefix}/{name}', STAMP)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            output.writestr(info, data)
    checksum = digest(archive.read_bytes())
    archive.with_suffix('.zip.sha256').write_text(f'{checksum}  {archive.name}\n')
    verify(archive)
    return {'archive': str(archive), 'sha256': checksum, 'files': len(payload), 'version': version}


def verify(archive):
    archive = Path(archive)
    checksum = archive.with_suffix('.zip.sha256')
    if not checksum.is_file():
        raise ValueError('Missing adjacent .zip.sha256 file.')
    expected = checksum.read_text().strip().split()
    if len(expected) != 2 or expected[1] != archive.name or expected[0] != digest(archive.read_bytes()):
        raise ValueError('Release ZIP checksum mismatch.')
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if len(names) != len(set(names)) or not names:
            raise ValueError('Duplicate or missing ZIP entries.')
        for info in source.infolist():
            parts = PurePosixPath(info.filename).parts
            if (info.filename.startswith('/') or '\\' in info.filename or '..' in parts or
                    len(parts) < 2 or not stat.S_ISREG(info.external_attr >> 16) or
                    stat.S_IMODE(info.external_attr >> 16) != 0o644 or
                    info.flag_bits & 1 or info.file_size > 5_000_000):
                raise ValueError('Unsafe ZIP member.')
        prefixes = {PurePosixPath(name).parts[0] for name in names}
        if len(prefixes) != 1:
            raise ValueError('ZIP must contain one pack root.')
        prefix = prefixes.pop()
        manifest = json.loads(source.read(f'{prefix}/release-manifest.json'))
        version = manifest.get('version', '')
        if (manifest.get('schema') != 1 or manifest.get('name') != 'dialpad-skills' or
                not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version) or
                prefix != f'dialpad-skills-{version}'):
            raise ValueError('Invalid release manifest identity.')
        records = manifest.get('files', {})
        if set(records) & GENERATED_PROVIDER_FILES:
            raise ValueError('Generated provider catalog must not be redistributed in release ZIPs.')
        if set(names) != {f'{prefix}/{name}' for name in records} | {f'{prefix}/release-manifest.json'}:
            raise ValueError('ZIP inventory differs from release manifest.')
        for name, facts in records.items():
            data = source.read(f'{prefix}/{name}')
            if facts != {'sha256': digest(data), 'size': len(data), 'mode': '0644'}:
                raise ValueError(f'Release member checksum mismatch: {name}')
        if source.read(f'{prefix}/VERSION').decode().strip() != version:
            raise ValueError('Embedded VERSION differs from manifest.')
    return {'verified': True, 'archive': str(archive), 'files': len(names)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out-dir', type=Path, default=ROOT / 'dist')
    parser.add_argument('--verify', type=Path, help='Verify an existing archive and adjacent checksum.')
    args = parser.parse_args(argv)
    try:
        result = verify(args.verify) if args.verify else build(args.root, args.out_dir)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
