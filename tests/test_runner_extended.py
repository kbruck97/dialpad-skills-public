"""Offline behavior tests for the portable runner; never contact Dialpad."""
import argparse
import base64
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from test_dialpad import d, client


class ExtendedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {'HOME': str(self.home)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(setattr, d, 'ACTIVE_SECRET', None)

    def invoke(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            status = d.main(argv)
        return status, json.loads(out.getvalue())

    def body(self, value):
        path = self.home / 'request.json'
        path.write_text(json.dumps(value))
        return str(path)

    def test_unconfigured_runner_never_attempts_ssh(self):
        with patch.object(d.subprocess, 'run', side_effect=AssertionError('implicit SSH')):
            status, receipt = self.invoke(['doctor'])
        self.assertEqual(status, 1)
        self.assertIn('missing', receipt['error'])

    def test_doctor_is_offline_and_does_not_echo_key(self):
        os.environ['DIALPAD_API_KEY'] = 'ONLY_IN_ENV_SECRET'
        with patch.object(d.Client, 'request', side_effect=AssertionError('unexpected API')):
            status, receipt = self.invoke(['doctor'])
        self.assertEqual(status, 0)
        self.assertEqual(receipt['metrics']['requests'], 0)
        self.assertFalse(receipt['network_checked'])
        self.assertNotIn('ONLY_IN_ENV_SECRET', json.dumps(receipt))

    def test_doctor_live_uses_user_role_office_read(self):
        args = d.parser().parse_args(['doctor', '--live'])
        args.connection, args.env_file = 'local', None
        c = client([{'id': '20', 'name': 'Example', 'secret': 'hidden', 'other': 'not returned'}])
        receipt = d.execute(args, c)
        self.assertEqual(c.opener.seen[0].full_url, 'https://dialpad.com/api/v2/offices/primary')
        self.assertEqual(receipt['primary_office'], {'id': '20', 'name': 'Example'})
        self.assertTrue(receipt['authentication_verified'])

    def test_config_priority_and_hermes_persistent_default(self):
        persistent = self.home / 'persistent'
        config = persistent / '.config/dialpad-skills/connection.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'via': 'ssh', 'ssh_host': 'config-host', 'remote_env_file': '/service/config.env'}))
        os.environ['HERMES_HOME'] = str(persistent)
        os.environ['DIALPAD_SSH_HOST'] = 'environment-host'
        args = d.parser().parse_args(['--ssh-host', 'cli-host', 'doctor'])
        self.assertTrue(d.resolve_connection(args))
        self.assertEqual(args.ssh_host, 'cli-host')
        self.assertEqual(args.remote_env_file, '/service/config.env')

    def test_xdg_default_and_explicit_config(self):
        config = self.home / 'xdg/dialpad-skills/connection.json'
        config.parent.mkdir(parents=True)
        config.write_text('{"via":"ssh","ssh_host":"xdg-host"}')
        os.environ['XDG_CONFIG_HOME'] = str(self.home / 'xdg')
        args = d.parser().parse_args(['doctor'])
        self.assertTrue(d.resolve_connection(args))
        self.assertEqual(args.ssh_host, 'xdg-host')
        explicit = self.home / 'explicit.json'
        explicit.write_text('{"via":"local"}')
        args = d.parser().parse_args(['--config', str(explicit), 'doctor'])
        self.assertFalse(d.resolve_connection(args))

    def test_explicit_local_file_cannot_be_redirected_to_configured_remote(self):
        config = self.home / 'config.json'
        config.write_text('{"via":"ssh","ssh_host":"other-account"}')
        args = d.parser().parse_args(['--config', str(config), '--env-file', '/local/selected.env', 'doctor'])
        self.assertFalse(d.resolve_connection(args))
        self.assertEqual(args.connection, 'local')
        args = d.parser().parse_args(['--via', 'ssh', '--ssh-host', 'selected-host', '--env-file', '/local/selected.env', 'doctor'])
        with self.assertRaises(d.Failure):
            d.resolve_connection(args)

    def test_environment_credential_location_overrides_configured_transport(self):
        config = self.home / 'config.json'
        config.write_text('{"via":"ssh","ssh_host":"configured-host"}')
        os.environ['DIALPAD_ENV_FILE'] = '/selected/local.env'
        args = d.parser().parse_args(['--config', str(config), 'doctor'])
        self.assertFalse(d.resolve_connection(args))
        del os.environ['DIALPAD_ENV_FILE']
        os.environ['DIALPAD_REMOTE_ENV_FILE'] = '/selected/remote.env'
        config.write_text('{"via":"local","ssh_host":"configured-host"}')
        args = d.parser().parse_args(['--config', str(config), 'doctor'])
        self.assertTrue(d.resolve_connection(args))
        args = d.parser().parse_args(['--via', 'local', '--remote-env-file', '/remote/path', 'doctor'])
        with self.assertRaises(d.Failure):
            d.resolve_connection(args)

    def test_config_refuses_credentials_and_endpoint_overrides(self):
        for key in ('api_key', 'token', 'base_url'):
            config = self.home / 'config.json'
            config.write_text(json.dumps({key: 'never-echo-this-value'}))
            status, receipt = self.invoke(['--config', str(config), 'doctor'])
            self.assertEqual(status, 1)
            self.assertNotIn('never-echo-this-value', json.dumps(receipt))

    def test_remote_file_requires_an_explicit_host(self):
        status, receipt = self.invoke(['--remote-env-file', '/service/credential.env', 'doctor'])
        self.assertEqual(status, 1)
        self.assertIn('SSH host', receipt['error'])

    def test_ssh_transports_no_ambient_credential_or_local_path(self):
        os.environ['DIALPAD_API_KEY'] = 'MUST_STAY_LOCAL'
        args = d.parser().parse_args(['--via', 'ssh', '--ssh-host', 'example-host', '--remote-env-file', '/service/remote.env', 'doctor'])
        self.assertTrue(d.resolve_connection(args))
        def run(command, **kwargs):
            self.assertEqual(command[-2:], ['example-host', 'python3 -'])
            encoded = re.search(r"base64.b64decode\('([A-Za-z0-9+/=]+)'\)", kwargs['input']).group(1)
            package = json.loads(base64.b64decode(encoded))
            self.assertEqual(package['args']['env_file'], '/service/remote.env')
            self.assertNotIn('MUST_STAY_LOCAL', json.dumps(package))
            return SimpleNamespace(stdout='{"credential_available":true}', returncode=0)
        with patch.object(d.subprocess, 'run', side_effect=run), redirect_stdout(io.StringIO()):
            self.assertEqual(d.ssh_run(args), 0)

    def test_unexpected_ssh_output_is_suppressed(self):
        args = d.parser().parse_args(['--via', 'ssh', '--ssh-host', 'example-host', 'doctor'])
        d.resolve_connection(args)
        with patch.object(d.subprocess, 'run', return_value=SimpleNamespace(stdout='private login banner', returncode=0)):
            with self.assertRaises(d.Failure) as caught:
                d.ssh_run(args)
        self.assertNotIn('private login banner', str(caught.exception))

    def test_schema_refs_require_nested_fields_and_validate_oneof(self):
        args = d.parser().parse_args(['api', 'call.transfer_call', '--path-params', '{"id":"123"}',
                                     '--body-file', self.body({'to': {'target_id': 20, 'target_type': 'callcenter'}})])
        self.assertEqual(d.operation_request(args)[1], '/api/v2/call/123/transfer')
        for destination in ({'target_type': 'callcenter'}, {'target_id': 20, 'target_type': 'invalid'},
                            {'number': '+15555550101', 'target_id': 20, 'target_type': 'callcenter'}):
            args.body_file = self.body({'to': destination})
            with self.assertRaises(d.Failure):
                d.operation_request(args)

    def test_required_body_fields_fail_before_credentials(self):
        with patch.object(d, 'load_key', side_effect=AssertionError('loaded key')):
            status, receipt = self.invoke(['api', 'contacts.create', '--execute'])
        self.assertEqual(status, 1)
        self.assertIn('--body-file', receipt['error'])

    def test_absent_body_schema_rejects_a_delete_body(self):
        operation = next(k for k, value in d.CATALOG['operations'].items()
                         if value['method'] == 'DELETE' and value['body'] is None and value['path'].endswith('/{id}'))
        args = d.parser().parse_args(['api', operation, '--path-params', '{"id":123}', '--body-file', self.body({'unexpected': True})])
        with self.assertRaises(d.Failure):
            d.operation_request(args)

    def test_unconstrained_present_json_body_is_distinct_from_absent(self):
        operation = {'method': 'POST', 'path': '/api/v2/example', 'parameters': [], 'body': {}, 'body_required': False}
        with patch.dict(d.CATALOG['operations'], {'fixture': operation}):
            args = d.parser().parse_args(['api', 'fixture', '--body-file', self.body([1, 'valid json array'])])
            self.assertEqual(d.operation_request(args)[3], [1, 'valid json array'])

    def test_schema_constraints_and_additional_properties(self):
        schema = {'type': 'object', 'required': ['name', 'values'], 'additionalProperties': False,
                  'properties': {'name': {'type': 'string', 'minLength': 2, 'maxLength': 5, 'pattern': '^[a-z]+$'},
                                 'values': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                                            'items': {'type': 'number', 'minimum': 0, 'exclusiveMaximum': 5}}}}
        d.validate_value({'name': 'good', 'values': [1, 2]}, schema, 'body')
        for value in ({'name': 'x', 'values': [1]}, {'name': 'TOO', 'values': [1]},
                      {'name': 'good', 'values': [5]}, {'name': 'good', 'values': [1, 1]},
                      {'name': 'good', 'values': []}, {'name': 'good', 'values': [1], 'unknown': True}):
            with self.assertRaises(d.Failure):
                d.validate_value(value, schema, 'body')
        d.validate_value({'x': 3}, {'type': 'object', 'additionalProperties': {'type': 'integer'}}, 'body')
        with self.assertRaises(d.Failure):
            d.validate_value({'x': 'wrong'}, {'type': 'object', 'additionalProperties': {'type': 'integer'}}, 'body')

    def test_nullable_composition_and_external_refs(self):
        d.validate_value(None, {'type': ['string', 'null']}, 'x')
        d.validate_value(3, {'allOf': [{'type': 'integer'}, {'minimum': 2}], 'anyOf': [{'const': 3}, {'const': 4}]}, 'x')
        for schema in ({'$ref': 'https://example.com/schema'}, {'type': 'integer'}, {'oneOf': [{}, {}]}):
            with self.assertRaises(d.Failure):
                d.validate_value(True, schema, 'x')

    def test_non_finite_numbers_rejected_and_large_int64_fails_structurally(self):
        for raw in ('{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(d.Failure):
                d.obj(raw)
        with self.assertRaises(d.Failure):
            d.validate_value(10**1000, {'type': 'integer', 'format': 'int64'}, 'id')
        d.validate_value(10**1000, {'type': 'integer'}, 'big')

    def test_path_enum_and_integer_validation(self):
        args = d.parser().parse_args(['api', 'call.get_call_info', '--path-params', '{"id":true}'])
        with self.assertRaises(d.Failure):
            d.operation_request(args)
        operation = next(k for k, value in d.CATALOG['operations'].items() if '{ivr_type}' in value['path'])
        args = d.parser().parse_args(['api', operation, '--path-params', '{"target_id":123,"target_type":"wrong","ivr_type":"GREETING"}'])
        with self.assertRaises(d.Failure):
            d.operation_request(args)

    def test_e164_paths_are_encoded_but_route_escape_is_rejected(self):
        args = d.parser().parse_args(['api', 'blockednumbers.get', '--path-params', '{"number":"+15555550101"}'])
        self.assertEqual(d.operation_request(args)[1], '/api/v2/blockednumbers/%2B15555550101')
        for value in ('.', '../users', 'x/users', '%2fusers', 'x\\users'):
            args = d.parser().parse_args(['api', 'contacts.get', '--path-params', json.dumps({'id': value})])
            with self.assertRaises(d.Failure):
                d.operation_request(args)

    def test_phone_requires_explicit_country_prefix(self):
        for value in ('5555550101', '15555550101', '+15555550101 ext 2', '+05555550101'):
            with self.assertRaises(d.Failure):
                d.normalize_phone(value)
        self.assertEqual(d.normalize_phone('+44 20 7946 0958'), '442079460958')

    def test_parameter_serialization_and_credential_query_rejection(self):
        params = [{'name': 'a', 'in': 'query', 'style': 'form', 'explode': False},
                  {'name': 'obj', 'in': 'query', 'style': 'deepObject'}]
        self.assertEqual(d.query_pairs({'a': [1, 2], 'obj': {'active': True}}, params),
                         [('a', '1,2'), ('obj[active]', 'true')])
        with self.assertRaises(d.Failure):
            d.query_pairs({'obj': {'access_token': 'never send'}})

    def test_secrets_redacted_from_nested_receipts_and_exact_key_echo(self):
        d.ACTIVE_SECRET = 'CURRENT_KEY'
        value = {'secret': 'hook secret', 'nested': [{'client_secret': 'oauth secret'}], 'description': 'echo CURRENT_KEY'}
        out = io.StringIO()
        with redirect_stdout(out):
            d.emit(value)
        for secret in ('hook secret', 'oauth secret', 'CURRENT_KEY'):
            self.assertNotIn(secret, out.getvalue())
        self.assertIn('[REDACTED]', out.getvalue())

    def test_catalog_details_bundle_only_transitive_selected_schemas(self):
        args = d.parser().parse_args(['catalog', '--search', 'call.transfer_call', '--details'])
        result = d.selected_catalog(args)
        self.assertEqual(len(result['operations']), 1)
        self.assertIn('protos.call.TransferCallMessage', result['components']['schemas'])
        self.assertIn('protos.call.NumberTransferDestination', result['components']['schemas'])
        self.assertLess(len(result['components']['schemas']), len(d.CATALOG['components']['schemas']))

    def test_oauth_catalog_entries_cannot_receive_bearer_key(self):
        operation = next(iter(d.CATALOG['auth_operations']))
        status, receipt = self.invoke(['api', operation])
        self.assertEqual(status, 1)
        self.assertIn('separate authorization flow', receipt['error'])

    def test_provider_pacing_and_http_date_retry_after(self):
        self.assertGreaterEqual(d.rate_policy('GET', '/api/v2/call/123')[1], 6)
        self.assertGreaterEqual(d.rate_policy('GET', '/api/v2/call/123/ai_recap')[1], 5)
        self.assertGreaterEqual(d.rate_policy('POST', '/api/v2/callrouters')[1], 300)
        with patch.object(d.time, 'time', return_value=0):
            self.assertEqual(d.retry_delay('Thu, 01 Jan 1970 00:01:30 GMT', 0), 90)
        self.assertIsNone(d.retry_delay('unreadable provider header', 0))

    def test_resumed_pagination_does_not_claim_complete_history(self):
        result = client([{'items': [{'id': 2}]}]).pages('/api/v2/contacts', {}, 1, cursor='next')
        self.assertFalse(result['complete'])
        self.assertTrue(result['remaining_complete'])
        self.assertTrue(result['started_from_cursor'])

    def test_generic_single_page_cursor_does_not_claim_complete_history(self):
        args = d.parser().parse_args(['api', 'contacts.list', '--query', '{"cursor":"next"}'])
        result = d.execute(args, client([{'items': []}]))
        self.assertFalse(result['complete'])
        self.assertTrue(result['remaining_complete'])
        self.assertTrue(result['started_from_cursor'])

    def test_transcript_scan_is_scoped_bounded_and_flags_unscanned_calls(self):
        calls = [{'call_id': str(i), 'target': {'id': '123', 'type': 'callcenter'}} for i in (1, 2)]
        c = client([{'items': calls}, {'call_id': '1', 'lines': [{'content': 'repair the relay', 'name': 'Customer'}]}])
        args = d.parser().parse_args(['search-transcripts', '--target-id', '123', '--after', '2026-09-14T00:00:00Z',
                                     '--before', '2026-09-15T00:00:00Z', '--contains', 'relay', '--max-calls', '1'])
        result = d.execute(args, c)
        self.assertEqual(c.requests, 2)
        self.assertEqual(result['unscanned_call_ids'], ['2'])
        self.assertFalse(result['complete'])
        self.assertTrue(result['scope_verified'])
        self.assertEqual(result['matches'][0]['lines'][0]['name'], 'Customer')

    def test_unavailable_transcript_is_not_a_negative_search_result(self):
        c = client([{'items': [{'call_id': '1', 'target': {'id': '123', 'type': 'callcenter'}}]},
                    HTTPError('u', 403, 'private', {}, None)])
        args = d.parser().parse_args(['search-transcripts', '--target-id', '123', '--after', '2026-09-14T00:00:00Z',
                                     '--before', '2026-09-15T00:00:00Z', '--contains', 'relay'])
        result = d.execute(args, c)
        self.assertFalse(result['complete'])
        self.assertEqual(result['unavailable'][0]['status'], 403)
        self.assertEqual(result['scanned_call_ids'], [])

    def test_transcript_scan_stops_before_fetching_out_of_scope_call(self):
        c = client([{'items': [{'call_id': '1', 'target': {'id': '999', 'type': 'callcenter'}}]}])
        args = d.parser().parse_args(['search-transcripts', '--target-id', '123', '--after', '2026-09-14T00:00:00Z',
                                     '--before', '2026-09-15T00:00:00Z', '--contains', 'relay'])
        with self.assertRaises(d.Failure):
            d.execute(args, c)
        self.assertEqual(c.requests, 1)


if __name__ == '__main__':
    unittest.main()
