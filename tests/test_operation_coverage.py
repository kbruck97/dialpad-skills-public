"""Offline request-building/transport coverage, not provider acceptance tests.

All IDs, numbers and content are synthetic. An in-memory opener intercepts every
HTTP request; credentials, SSH and real sockets are forbidden during this suite.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qsl, quote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dialpad_coverage', ROOT / 'skills/dialpad-api/scripts/dialpad.py')
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


def sample(schema, label='', populate=False, depth=0):
    """Create deterministic JSON-schema fixtures; fail on unsupported features.

    Deliberately independent from runner validation so fixture errors and runner
    failures remain visible. Provider-only rules (licenses, valid IDs, actual
    phone routing, scheduling times) are outside this offline test's claims.
    """
    if depth > 40:
        raise AssertionError('Fixture generator encountered recursive schema: ' + label)
    if schema is True or schema == {}:
        return {}
    if schema is False or not isinstance(schema, dict):
        raise AssertionError('Unsatisfiable or invalid fixture schema: ' + label)
    if '$ref' in schema:
        node = d.CATALOG
        for part in schema['$ref'][2:].split('/'):
            node = node[part.replace('~1', '/').replace('~0', '~')]
        return sample(node, label, populate, depth + 1)
    if 'const' in schema:
        return copy.deepcopy(schema['const'])
    if 'enum' in schema:
        return copy.deepcopy(next((v for v in schema['enum'] if v is not None), schema['enum'][0]))
    if 'oneOf' in schema or 'anyOf' in schema:
        return sample(schema.get('oneOf', schema.get('anyOf'))[0], label, populate, depth + 1)
    if 'allOf' in schema:
        parts = [sample(branch, label, populate, depth + 1) for branch in schema['allOf']]
        if not all(isinstance(part, dict) for part in parts):
            raise AssertionError('Fixture allOf needs an explicit test value: ' + label)
        return {k: v for part in parts for k, v in part.items()}
    kinds = schema.get('type', 'object' if 'properties' in schema else 'string')
    kinds = [kinds] if isinstance(kinds, str) else kinds
    kind = next((t for t in kinds if t != 'null'), 'null')
    if kind == 'object':
        properties = schema.get('properties', {})
        names = properties if populate else schema.get('required', [])
        return {name: sample(properties.get(name, {}), label + '.' + name, populate, depth + 1)
                for name in names}
    if kind == 'array':
        count = max(1, schema.get('minItems', 0))
        if schema.get('maxItems') == 0:
            return []
        return [sample(schema.get('items', {}), label + '[]', populate, depth + 1) for _ in range(count)]
    if kind == 'boolean':
        return False
    if kind == 'null':
        return None
    if kind in ('integer', 'number'):
        return max(1, schema.get('minimum', 1), schema.get('exclusiveMinimum', 0) + 1)
    if kind == 'string':
        if 'pattern' in schema:
            raise AssertionError('New string pattern requires an explicit fixture: ' + label)
        if schema.get('format') == 'byte':
            return 'Zml4dHVyZQ=='
        if label.rsplit('.', 1)[-1] in ('number', 'from_number', 'to_number', 'phone_number'):
            return '+15555550101'
        return 'fixture' + ('x' * max(0, schema.get('minLength', 0) - 7))
    raise AssertionError('Unrecognized schema type: ' + str(kind))


def arguments(name, operation, populate=False):
    values, query = {}, {}
    for parameter in operation['parameters']:
        if parameter['in'] == 'path':
            values[parameter['name']] = sample(parameter['schema'], parameter['name'], populate)
        elif parameter['in'] == 'query' and (populate or parameter.get('required')):
            query[parameter['name']] = sample(parameter['schema'], parameter['name'], populate)
    args = argparse.Namespace(command='api', operation=name, path_params=json.dumps(values),
                              query=json.dumps(query), body_file=None, execute=False, max_pages=1)
    if operation['body'] is not None:
        args._body_data = sample(operation['body'], 'body', populate)
    return args, values, query


class Reply:
    def __init__(self, name):
        self.raw = json.dumps({'fixture_operation': name}).encode()

    def read(self, maximum):
        return self.raw[:maximum]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class InterceptingOpener:
    def __init__(self, name):
        self.name, self.requests = name, []

    def open(self, request, timeout):
        self.requests.append(request)
        return Reply(self.name)


class OperationCoverageTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.create_connection', 'socket.socket.connect'):
            guard = patch(target, side_effect=AssertionError('Network forbidden in operation coverage'))
            guard.start()
            self.addCleanup(guard.stop)
        for owner, name in ((d, 'load_key'), (d.subprocess, 'run')):
            guard = patch.object(owner, name, side_effect=AssertionError('Credential/SSH access forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    def exercise(self, name, operation, populate=False):
        args, values, query = arguments(name, operation, populate)
        op, path, actual_query, body = d.operation_request(args)
        expected_path = operation['path']
        for key, value in values.items():
            expected_path = expected_path.replace('{' + key + '}', quote(str(value), safe=''))
        self.assertEqual(path, expected_path)
        self.assertEqual(actual_query, query)
        self.assertEqual(op['method'], operation['method'])
        self.assertEqual(body, getattr(args, '_body_data', None))

        if operation['method'] != 'GET':
            preview = d.execute(args, None)
            self.assertTrue(preview['dry_run'])
            self.assertEqual(preview['path'], expected_path)
            self.assertEqual(preview['body'], body)

        # Use the real Client request serializer, but intercept before any socket.
        client = d.Client('FIXTURE_ONLY_NOT_A_CREDENTIAL')
        opener = InterceptingOpener(name)
        client.opener = opener
        client.wait = lambda seconds: None
        args.execute = True
        result = d.execute(args, client)
        self.assertEqual(result['data'], {'fixture_operation': name})
        self.assertEqual(result['method'], operation['method'])
        resumed = bool(query.get('cursor'))
        self.assertEqual(result['started_from_cursor'], resumed)
        self.assertEqual(result['complete'], not resumed)
        self.assertTrue(result['remaining_complete'])
        self.assertEqual(client.requests, 1)
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        url = urlsplit(request.full_url)
        self.assertEqual((url.scheme, url.netloc, url.path), ('https', 'dialpad.com', expected_path))
        self.assertEqual(request.get_method(), operation['method'])
        self.assertEqual(dict(parse_qsl(url.query, keep_blank_values=True)),
                         {key: str(value).lower() if isinstance(value, bool) else '' if value is None else str(value)
                          for key, value in query.items()})
        self.assertEqual(json.loads(request.data) if request.data is not None else None, body)
        self.assertEqual(request.get_header('Authorization'), 'Bearer FIXTURE_ONLY_NOT_A_CREDENTIAL')

    def test_all_rest_operations_minimal_requests_and_safe_previews(self):
        self.assertGreaterEqual(len(d.CATALOG['operations']), 241)
        for name, operation in d.CATALOG['operations'].items():
            with self.subTest(operation=name, fixture='required fields'):
                self.exercise(name, operation)

    def test_all_rest_operations_populated_optional_fields(self):
        for name, operation in d.CATALOG['operations'].items():
            with self.subTest(operation=name, fixture='all documented input fields'):
                self.exercise(name, operation, populate=True)

    def test_no_body_operations_reject_body_before_transport(self):
        checked = 0
        for name, operation in d.CATALOG['operations'].items():
            if operation['body'] is not None:
                continue
            with self.subTest(operation=name):
                args, _, _ = arguments(name, operation)
                args._body_data = {'unexpected': 'fixture'}
                with self.assertRaises(d.Failure):
                    d.operation_request(args)
                checked += 1
        self.assertGreater(checked, 0)

    def test_oauth_catalog_never_executes_as_rest(self):
        for name, operation in d.CATALOG['auth_operations'].items():
            with self.subTest(operation=name):
                args = argparse.Namespace(operation=name)
                with self.assertRaises(d.Failure) as error:
                    d.operation_request(args)
                self.assertIn('OAuth', str(error.exception))


if __name__ == '__main__':
    unittest.main()
