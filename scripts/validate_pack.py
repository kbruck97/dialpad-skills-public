#!/usr/bin/env python3
"""Validate portable skill structure, local links and release hygiene (stdlib).

This is a defense-in-depth scanner, not proof that arbitrary prose contains no
private information. Review new fixtures and documents before publishing.
"""
import argparse
import json
from pathlib import Path
import re
import stat
import sys
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {'pack.json', 'VERSION', 'README.md', 'LICENSE', 'LICENSE.md',
             'SECURITY.md', 'CHANGELOG.md', 'CONTRIBUTING.md', '.gitignore', 'THIRD_PARTY_NOTICES.md'}
TOP_DIRS = {'skills', 'scripts', 'tests', 'docs', 'examples', '.github'}
EXCLUDED_DIRS = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.git',
                 '.venv', 'venv', 'dist', 'build', '.dialpad-skill-pack', '.archive'}
NAME = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
FORBIDDEN_EXTENSIONS = {'.pyc', '.pyo', '.log', '.tmp', '.swp', '.sqlite', '.sqlite3', '.db',
                        '.pem', '.key', '.p12', '.pfx', '.mp3', '.mp4', '.wav'}
TEXT_EXTENSIONS = {'.py', '.json', '.md', '.yaml', '.yml', '.txt', '.toml', '.sh', '.csv'}
GENERATED_PROVIDER_FILES = frozenset({
    'skills/dialpad-api/references/operations.json',
    'skills/dialpad-api/references/operations.md',
})


def release_files(root):
    """Allowlist code/documentation roots; never ship git, venvs or runtime data."""
    root = Path(root)
    found = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if relative.as_posix() in GENERATED_PROVIDER_FILES:
            continue
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if relative.parts[0] not in TOP_FILES | TOP_DIRS:
            continue
        if path.is_symlink():
            raise ValueError(f'Symlink is forbidden in package content: {relative}')
        if path.is_file():
            if path.suffix in {'.pyc', '.pyo'} or path.name == '.DS_Store':
                continue
            found.append(path)
        elif not path.is_dir():
            raise ValueError(f'Special file is forbidden: {relative}')
    return found


def validate_local_catalog(root):
    """A source distribution is valid before bootstrap; check local data if present."""
    path = root / 'skills/dialpad-api/references/operations.json'
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 5_000_000:
        raise ValueError('Local generated catalog must be a regular JSON file under 5 MB.')
    catalog = json.loads(path.read_text(encoding='utf-8'))
    if catalog.get('catalog_version') != 2:
        raise ValueError('Local generated catalog has an unsupported catalog_version.')
    operations, auth = catalog['operations'], catalog['auth_operations']
    coverage = catalog['coverage']
    if (coverage['rest_operations'] != len(operations) or
            coverage['auth_operations'] != len(auth) or
            coverage['source_operations'] != len(operations) + len(auth)):
        raise ValueError('Local generated catalog coverage differs from its inventory.')
    if (catalog.get('source_url') != 'https://dialpad.com/static/openapi/platform-v1.0.json' or
            not re.fullmatch(r'[a-f0-9]{64}', catalog.get('source_sha256', ''))):
        raise ValueError('Local generated catalog is missing official-source provenance.')
    for entries, prefix in ((operations, '/api/v2/'), (auth, '/oauth2/')):
        for operation in entries.values():
            if not operation['path'].startswith(prefix):
                raise ValueError('Local generated catalog contains an unexpected API path.')

    def check_refs(value):
        if isinstance(value, dict):
            if '$ref' in value:
                reference = value['$ref']
                if not reference.startswith('#/'):
                    raise ValueError('Local generated catalog contains an external schema reference.')
                current = catalog
                for part in reference[2:].split('/'):
                    current = current[part.replace('~1', '/').replace('~0', '~')]
            for item in value.values():
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)
    check_refs(catalog)
    return coverage


def frontmatter(path):
    text = path.read_text(encoding='utf-8')
    lines = text.splitlines()
    if not lines or lines[0] != '---':
        raise ValueError('Missing YAML frontmatter.')
    try:
        end = lines.index('---', 1)
    except ValueError as exc:
        raise ValueError('Unterminated YAML frontmatter.') from exc
    result = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not re.match(r'^[A-Za-z][A-Za-z0-9_-]*:', line):
            raise ValueError('Use simple single-line frontmatter fields for portable validation.')
        key, value = line.split(':', 1)
        if key in result:
            raise ValueError(f'Duplicate frontmatter field: {key}')
        result[key] = value.strip().strip('"\'')
    return result


def validate(root):
    root = Path(root).resolve()
    errors = []
    try:
        files = release_files(root)
    except ValueError as exc:
        return {'valid': False, 'errors': [str(exc)], 'skills': [], 'files': 0}
    try:
        pack = json.loads((root / 'pack.json').read_text())
        version = (root / 'VERSION').read_text().strip()
        if pack.get('version') != version or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version):
            errors.append('VERSION must be a semantic version matching pack.json.')
        if pack.get('skills_directory') != 'skills' or pack.get('credentials_included') is not False:
            errors.append('pack.json must declare skills_directory=skills and credentials_included=false.')
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f'Invalid pack metadata: {exc}')
    local_catalog = None
    try:
        local_catalog = validate_local_catalog(root)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(f'Invalid local generated API catalog: {exc}')
    skills = []
    for skill in sorted((root / 'skills').glob('*')):
        if not skill.is_dir():
            errors.append(f'Unexpected entry under skills/: {skill.name}')
            continue
        if not (skill / 'SKILL.md').is_file():
            errors.append(f'{skill.name}: missing SKILL.md')
            continue
        skills.append(skill.name)
        try:
            fields = frontmatter(skill / 'SKILL.md')
            if fields.get('name') != skill.name or not NAME.fullmatch(skill.name):
                errors.append(f'{skill.name}: frontmatter name must match the directory.')
            if len(fields.get('description', '')) < 20:
                errors.append(f'{skill.name}: provide a useful trigger description.')
        except (ValueError, UnicodeError) as exc:
            errors.append(f'{skill.name}: {exc}')
    if not skills:
        errors.append('No skills found.')
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.name == '.env' or (path.name.startswith('.env.') and path.name != '.env.example') or path.suffix.lower() in FORBIDDEN_EXTENSIONS:
            errors.append(f'Forbidden credential/data/temp file: {relative}')
        if path.stat().st_size > 5_000_000:
            errors.append(f'Unexpected large artifact: {relative}')
        if path.suffix not in TEXT_EXTENSIONS and path.name not in TOP_FILES:
            errors.append(f'Unrecognized package file type: {relative}')
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeError:
            errors.append(f'Binary content is not allowed: {relative}')
            continue
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', text):
            errors.append(f'Private key material: {relative}')
        if re.search(r'\b(?:ghp|gho|github_pat|sk_live)[_-][A-Za-z0-9_]{24,}\b', text):
            errors.append(f'Likely credential: {relative}')
        if re.search(r'https?://[^\s/:]+:[^\s/@]+@', text):
            errors.append(f'Credential embedded in URL: {relative}')
        # Customer fixtures use reserved example domains and fictional 555 numbers.
        for email in re.findall(r'(?<![\w.-])[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})', text):
            if email.lower() not in {'example.com', 'example.org', 'example.net', 'localhost.invalid'} and not email.lower().endswith('.example'):
                errors.append(f'Possible personal email; use a reserved example address: {relative}')
        for phone in re.findall(r'(?<![\w])\+[1-9][0-9]{9,14}\b', text):
            if not re.fullmatch(r'\+1[0-9]{3}555[0-9]{4}', phone):
                errors.append(f'Possible customer phone; use a fictional 555 number: {relative}')
        if re.search(r'\b(?:job|cus|appt)_[a-f0-9]{24,}\b', text):
            errors.append(f'Possible live service-system record ID; use a placeholder: {relative}')
        # Literal account credentials must never be bundled. Placeholder/test values are allowed.
        for match in re.finditer(r'(?i)["\']?(?:dialpad_api_key|api_key|access_token|bearer_token)["\']?\s*[:=]\s*["\']([^"\'\n]{16,})["\']', text):
            value = match.group(1)
            if value != 'literal$(no_execution)' and not any(word in value.lower() for word in ['example', 'test', 'placeholder', 'your_', 'replace_', 'os.environ']):
                errors.append(f'Possible literal credential: {relative}')
        if path.suffix == '.md':
            for raw in re.findall(r'\[[^\]\n]+\]\(([^)\n]+)\)', text):
                link = raw.split(' "', 1)[0].strip('<>')
                parsed = urlparse(link)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                # Templates with clearly marked placeholders do not claim a real resource.
                if any(token in parsed.path for token in ['{', '}', '<', '>']):
                    continue
                resolved = (path.parent / unquote(parsed.path)).resolve()
                try:
                    resolved.relative_to(root)
                except ValueError:
                    errors.append(f'Local resource link escapes package: {relative}: {link}')
                    continue
                if not resolved.exists():
                    errors.append(f'Missing local resource: {relative}: {link}')
        if path.suffix == '.json':
            try:
                json.loads(text)
            except ValueError as exc:
                errors.append(f'Invalid JSON: {relative}: {exc}')
    return {'valid': not errors, 'errors': errors, 'skills': skills, 'files': len(files),
            'catalog': {'generated': local_catalog is not None, 'included_in_release': False,
                        'coverage': local_catalog},
            'hygiene_scope': 'Structure, links, known secrets and common customer identifiers; prose still requires content review.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    result = validate(args.root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(not result['valid'])


if __name__ == '__main__':
    sys.exit(main())
