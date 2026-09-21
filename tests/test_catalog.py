import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('update_catalog', ROOT / 'scripts/update_catalog.py')
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


def fixture():
    return {
        'openapi': '3.1.0', 'info': {'version': 'v2'},
        'security': [{'bearer_token': ['base']}],
        'components': {'schemas': {'Payload': {
            'description': 'Discard vendor prose.', 'type': 'object',
            'properties': {
                'description': {'type': 'string', 'description': 'Preserve the property name.'},
                'title': {'type': 'string'},
                'settings': {'type': 'object', 'additionalProperties': {'type': 'integer'}},
            }, 'required': ['description'],
        }}},
        'paths': {
            '/api/v2/things/{id}': {
                'parameters': [{'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'integer'}}],
                'patch': {
                    'operationId': 'things.update', 'summary': 'Thing -- Update',
                    'x-ratelimit': [[2, 60]], 'x-access': ['admin'],
                    'requestBody': {'required': True, 'content': {'application/json': {'schema': {
                        '$ref': '#/components/schemas/Payload'}}}},
                    'responses': {'204': {'description': 'Success'}},
                },
                'get': {
                    'operationId': 'things.get', 'security': [],
                    'parameters': [{'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}},
                                   {'name': 'tags', 'in': 'query', 'style': 'form', 'explode': False,
                                    'schema': {'type': 'array', 'items': {'type': 'string'}}}],
                    'responses': {},
                },
            },
            '/oauth2/token': {'post': {
                'operationId': 'oauth2.token.post',
                'requestBody': {'content': {'application/json': {'schema': {
                    'oneOf': [{'$ref': '#/components/schemas/Payload'}, {'type': 'string'}]}}}},
                'responses': {},
            }},
        },
    }


def compile_fixture(spec=None, previous=None):
    return catalog.compile_catalog(json.dumps(spec or fixture()).encode(), '2026-09-21', previous)


class CatalogTests(unittest.TestCase):
    def test_inventory_separates_oauth_and_preserves_aliases(self):
        previous = {'operations': {'legacy.name': {'method': 'PATCH', 'path': '/api/v2/things/{id}'}}}
        result = compile_fixture(previous=previous)
        self.assertEqual(result['coverage'], {'source_operations': 3, 'rest_operations': 2,
                                             'auth_operations': 1, 'source_paths': 2})
        self.assertIn('legacy.name', result['operations'])
        self.assertNotIn('oauth2.token.post', result['operations'])
        self.assertEqual(result['auth_operations']['oauth2.token.post']['execution'], 'separate_oauth_flow')

    def test_schema_references_constraints_and_data_names_preserved(self):
        result = compile_fixture()
        schema = result['components']['schemas']['Payload']
        self.assertNotIn('description', schema)
        self.assertIn('description', schema['properties'])
        self.assertIn('title', schema['properties'])
        self.assertEqual(schema['properties']['settings']['additionalProperties'], {'type': 'integer'})
        self.assertEqual(result['operations']['things.update']['body'], {'$ref': '#/components/schemas/Payload'})
        self.assertEqual(len(result['auth_operations']['oauth2.token.post']['body']['oneOf']), 2)
        catalog.check_references(result)

    def test_operation_parameters_override_path_parameters_and_preserve_serialization(self):
        result = compile_fixture()['operations']['things.get']
        params = {p['name']: p for p in result['parameters']}
        self.assertEqual(params['id']['schema']['type'], 'string')
        self.assertFalse(params['tags']['explode'])
        self.assertEqual(params['tags']['style'], 'form')

    def test_metadata_and_reproducibility(self):
        source = json.dumps(fixture()).encode()
        result = catalog.compile_catalog(source, '2026-09-21')
        self.assertEqual(result['source_sha256'], hashlib.sha256(source).hexdigest())
        self.assertEqual(result, catalog.compile_catalog(source, '2026-09-21', result))
        self.assertEqual(result['operations']['things.update']['scopes'], ['base'])
        self.assertEqual(result['operations']['things.get']['scopes'], [])
        self.assertEqual(result['operations']['things.update']['rate_limits'], [[2, 60]])

    def test_absent_request_body_is_distinct_from_unconstrained_body(self):
        source = fixture()
        source['paths']['/api/v2/no-body'] = {'post': {'operationId': 'things.no_body'}}
        source['paths']['/api/v2/any-body'] = {'post': {'operationId': 'things.any_body',
            'requestBody': {'content': {'application/json': {'schema': {}}}}}}
        result = compile_fixture(source)['operations']
        self.assertIsNone(result['things.get']['body'])
        self.assertIsNone(result['things.no_body']['body'])
        self.assertEqual(result['things.any_body']['body'], {})
        self.assertFalse(result['things.no_body']['body_required'])

    def test_external_or_missing_references_fail_closed(self):
        for ref in ('https://example.invalid/schema', '#/components/schemas/Missing'):
            source = fixture()
            source['components']['schemas']['Payload'] = {'$ref': ref}
            with self.assertRaises((ValueError, KeyError)):
                compile_fixture(source)

    def test_unrecognized_path_media_or_duplicate_operation_requires_review(self):
        samples = []
        source = fixture()
        source['paths']['/future/thing'] = {'get': {'operationId': 'future.thing'}}
        samples.append(source)
        source = fixture()
        source['paths']['/api/v2/things/{id}']['patch']['requestBody']['content'] = {'multipart/form-data': {}}
        samples.append(source)
        source = fixture()
        source['paths']['/api/v2/things/{id}']['get']['operationId'] = 'things.update'
        samples.append(source)
        for source in samples:
            with self.assertRaises(ValueError):
                compile_fixture(source)

    def test_annotations_do_not_corrupt_default_or_enum_values(self):
        value = {'type': 'object', 'default': {'description': 'literal'},
                 'enum': [{'title': 'literal'}], 'description': 'discard'}
        self.assertEqual(catalog.structural(value), {
            'type': 'object', 'default': {'description': 'literal'}, 'enum': [{'title': 'literal'}]})

    def test_bootstrapped_catalog_is_complete_and_self_contained(self):
        self.assertTrue(catalog.DEFAULT_OUTPUT.is_file(),
                        'Bootstrap the local catalog with python3 scripts/update_catalog.py before running the full suite.')
        result = json.loads(catalog.DEFAULT_OUTPUT.read_text())
        catalog.check_references(result)
        self.assertEqual(result['coverage']['source_operations'],
                         len(result['operations']) + len(result['auth_operations']))
        self.assertGreaterEqual(len(result['operations']), 241)
        self.assertEqual(len(result['auth_operations']), 3)
        self.assertTrue(all(op['path'].startswith('/api/v2/') for op in result['operations'].values()))
        self.assertEqual(result['operations']['transcripts.get_url']['path'], '/api/v2/transcripts/{call_id}/url')


if __name__ == '__main__':
    unittest.main()
