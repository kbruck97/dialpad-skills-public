#!/usr/bin/env python3
"""Bootstrap the local API catalog from Dialpad's official OpenAPI collection.

Use --source-file for an offline, reproducible import. Tests never fetch the web.
This compiler retains structural schemas, operation summaries and provenance;
it omits narrative descriptions and examples.
The generated provider catalog is local data, excluded from Git and release ZIPs.
No Dialpad API credentials are needed to download the public specification.
"""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen


SOURCE_URL = 'https://dialpad.com/static/openapi/platform-v1.0.json'
INDEX_URL = 'https://developers.dialpad.com/llms.txt'
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'skills/dialpad-api/references/operations.json'
METHODS = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options'}
ANNOTATIONS = {'description', 'title', 'example', 'examples', 'externalDocs', '$comment'}
MAX_SOURCE_BYTES = 16 * 1024 * 1024


def structural(value):
    """Remove explanatory annotations without changing schema semantics.

    Names inside properties/$defs/definitions are data, even when called title
    or description. Enum/const/default values must also remain byte-for-byte.
    """
    if isinstance(value, list):
        return [structural(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in ANNOTATIONS:
            continue
        if key in {'properties', 'patternProperties', '$defs', 'definitions', 'schemas'}:
            result[key] = {name: structural(schema) for name, schema in item.items()}
        elif key in {'enum', 'const', 'default'}:
            result[key] = item
        else:
            result[key] = structural(item)
    return result


def resolve(document, reference):
    if not reference.startswith('#/'):
        raise ValueError('Only local OpenAPI references are supported: ' + reference)
    current = document
    for part in reference[2:].split('/'):
        current = current[part.replace('~1', '/').replace('~0', '~')]
    return current


def check_references(document):
    def visit(value):
        if isinstance(value, dict):
            if '$ref' in value:
                resolve(document, value['$ref'])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(document)


def dereference(document, value):
    seen = set()
    while isinstance(value, dict) and '$ref' in value:
        reference = value['$ref']
        if reference in seen:
            raise ValueError('Cyclic top-level reference: ' + reference)
        seen.add(reference)
        value = {**resolve(document, reference), **{k: v for k, v in value.items() if k != '$ref'}}
    return value


def compile_catalog(raw, checked_at, previous=None):
    spec = json.loads(raw)
    if not str(spec.get('openapi', '')).startswith('3.'):
        raise ValueError('Expected an OpenAPI 3 collection.')
    check_references(spec)
    aliases = {(operation['method'].lower(), operation['path']): name
               for name, operation in (previous or {}).get('operations', {}).items()}
    operations, auth_operations, seen_ids = {}, {}, set()
    for path, path_item in sorted(spec['paths'].items()):
        path_item = dereference(spec, path_item)
        for method, operation in sorted(path_item.items()):
            if method not in METHODS:
                continue
            operation = dereference(spec, operation)
            operation_id = operation['operationId']
            if operation_id in seen_ids:
                raise ValueError('Duplicate operationId: ' + operation_id)
            seen_ids.add(operation_id)
            name = aliases.get((method, path), operation_id)
            # OpenAPI operation parameters override same-name path parameters.
            parameter_map = {}
            for parameter in path_item.get('parameters', []) + operation.get('parameters', []):
                parameter = dereference(spec, parameter)
                parameter_map[(parameter['in'], parameter['name'])] = structural(parameter)
            body = dereference(spec, operation.get('requestBody', {}))
            media = body.get('content', {})
            if media and set(media) != {'application/json'}:
                raise ValueError('Review new request media before importing: ' + operation_id)
            security = operation.get('security', spec.get('security', []))
            entry = {
                'method': method.upper(), 'path': path,
                'summary': operation.get('summary', operation_id),
                'source': 'https://developers.dialpad.com/reference/' + operation_id.replace('.', ''),
                'operation_id': operation_id,
                'parameters': list(parameter_map.values()),
                'body_required': body.get('required', False),
                'body': (structural(media.get('application/json', {}).get('schema', {}))
                         if 'requestBody' in operation else None),
                'responses': structural(operation.get('responses', {})),
                'security': security,
                'scopes': sorted({scope for alternative in security for scopes in alternative.values() for scope in scopes}),
                'access': operation.get('x-access', []),
                'rate_limits': operation.get('x-ratelimit', []),
                'tags': operation.get('tags', []),
                'deprecated': operation.get('deprecated', False),
            }
            if path.startswith('/api/v2/'):
                target = operations
            elif path.startswith('/oauth2/'):
                entry['execution'] = 'separate_oauth_flow'
                target = auth_operations
            else:
                raise ValueError('Review unrecognized API path before importing: ' + path)
            if name in target:
                raise ValueError('Operation alias collision: ' + name)
            target[name] = entry
    catalog = {
        'catalog_version': 2,
        'checked_at': checked_at,
        'source_url': SOURCE_URL,
        'source_index': INDEX_URL,
        'source_sha256': hashlib.sha256(raw).hexdigest(),
        'source_version': {'openapi': spec['openapi'], 'api': spec.get('info', {}).get('version')},
        'coverage': {'source_operations': len(seen_ids), 'rest_operations': len(operations),
                     'auth_operations': len(auth_operations), 'source_paths': len(spec['paths'])},
        'operations': dict(sorted(operations.items())),
        'auth_operations': dict(sorted(auth_operations.items())),
        'components': structural(spec.get('components', {})),
    }
    check_references(catalog)
    return catalog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', type=Path, help='Use an already downloaded official specification.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--checked-at', default=date.today().isoformat(), help='ISO date of source verification.')
    parser.add_argument('--check', action='store_true', help='Compare the current official source with the local catalog without writing; fails if missing.')
    args = parser.parse_args(argv)
    date.fromisoformat(args.checked_at)
    if args.check and not args.output.is_file():
        print(json.dumps({'status': 'missing', 'error': 'Local API catalog is not generated. Run python3 scripts/update_catalog.py first; no API credential is needed.'}))
        return 1
    if args.source_file:
        raw = args.source_file.read_bytes()
    else:
        request = Request(SOURCE_URL, headers={'User-Agent': 'DialpadSkillCatalog/2.0', 'Accept': 'application/json'})
        with urlopen(request, timeout=30) as response:
            raw = response.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError('Source specification exceeds the size budget.')
    previous = json.loads(args.output.read_text()) if args.output.exists() else {}
    catalog = compile_catalog(raw, args.checked_at, previous)
    text = json.dumps(catalog, indent=2, ensure_ascii=False) + '\n'
    if args.check:
        # A verification date changes without implying schema drift.
        existing = dict(previous)
        existing['checked_at'] = args.checked_at
        if catalog != existing:
            print(json.dumps({'status': 'drift', **catalog['coverage']}))
            return 1
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(json.dumps({'status': 'verified' if args.check else 'updated',
                      **catalog['coverage'], 'source_sha256': catalog['source_sha256']}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError) as error:
        print('Catalog import failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
