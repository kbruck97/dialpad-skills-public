#!/usr/bin/env python3
"""Portable Dialpad API runner. Standard library only; never prints credentials."""
import argparse
import base64
import csv
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

BASE = 'https://dialpad.com'
CATALOG = globals().get('BUNDLED_CATALOG')
CATALOG_ERROR = None
if CATALOG is None:
    try:
        CATALOG = json.loads((Path(__file__).resolve().parent.parent / 'references/operations.json').read_text())
        if not isinstance(CATALOG, dict) or not isinstance(CATALOG.get('operations'), dict):
            raise ValueError('Invalid catalog structure.')
    except FileNotFoundError:
        CATALOG_ERROR = 'Local API catalog is not generated.'
    except (OSError, ValueError):
        CATALOG = None
        CATALOG_ERROR = 'Local API catalog could not be read or is invalid.'


class Failure(Exception):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def require_catalog():
    if CATALOG is None:
        raise Failure((CATALOG_ERROR or 'Local API catalog is unavailable.') +
                      ' In a skill-pack checkout, run python3 scripts/update_catalog.py' +
                      ' (no API credential needed), then rerun this command.' +
                      ' For an installed skill, regenerate in the checkout and reinstall the skills.',
                      bootstrap_required=True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Failure('Redirect refused; bearer credentials were not forwarded.', status=code)


SECRET_FIELDS = frozenset({'authorization', 'api_key', 'apikey', 'dialpad_api_key', 'access_token',
                           'refresh_token', 'client_secret', 'clientsecret', 'webhook_secret', 'signing_secret',
                           'private_key', 'secret_key', 'bearer_token', 'secret', 'password', 'token'})
ACTIVE_SECRET = None


def redact(value):
    """Do not echo API keys, webhook secrets, or OAuth tokens in receipts."""
    if isinstance(value, dict):
        return {k: '[REDACTED]' if k.lower().replace('-', '_') in SECRET_FIELDS else redact(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and ACTIVE_SECRET:
        return value.replace(ACTIVE_SECRET, '[REDACTED]')
    return value


def emit(value):
    print(json.dumps(redact(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False))


def load_key(env_file=None):
    if env_file:
        try:
            for raw in Path(env_file).expanduser().read_text().splitlines():
                line = raw.strip()
                if line.startswith('export '):
                    line = line[7:].lstrip()
                if not re.match(r'^DIALPAD_API_KEY\s*=', line):
                    continue
                value = line.split('=', 1)[1].strip()
                if value[:1] in ('"', "'"):
                    end = value.find(value[0], 1)
                    if end == -1:
                        raise Failure('Malformed quoted DIALPAD_API_KEY.')
                    value = value[1:end]
                else:
                    value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
                if value and not any(c in value for c in '\r\n'):
                    return value
        except OSError:
            raise Failure('Selected credential file is unavailable.') from None
        raise Failure('Selected credential file has no usable DIALPAD_API_KEY.')
    value = os.environ.get('DIALPAD_API_KEY', '')
    if not value or any(c in value for c in '\r\n'):
        raise Failure('DIALPAD_API_KEY is missing or malformed.')
    return value


def json_value(text):
    def invalid_constant(_):
        raise Failure('JSON must not contain NaN or infinity.')
    try:
        return json.loads(text, parse_constant=invalid_constant)
    except (ValueError, UnicodeError):
        raise Failure('Invalid JSON input.') from None


def obj(text):
    value = json_value(text)
    if not isinstance(value, dict):
        raise Failure('Expected a JSON object.')
    return value


def resolve_ref(reference):
    if not isinstance(reference, str) or not reference.startswith('#/components/schemas/'):
        raise Failure('Only bundled local schema references are supported.')
    node = CATALOG
    try:
        for part in reference[2:].split('/'):
            node = node[part.replace('~1', '/').replace('~0', '~')]
    except (KeyError, TypeError):
        raise Failure('Bundled schema reference is missing.', reference=reference) from None
    return node


def json_equal(left, right):
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


def validate_value(value, schema, label, depth=0):
    """Validate the pinned OpenAPI JSON schemas, resolving local refs only.

    Supports refs, unions/composition, JSON types, enums, const, object/array
    structure, and numeric/string/collection constraints. Format is an annotation
    except int64 and byte. This is not a general JSON Schema implementation.
    """
    if depth > 64:
        raise Failure('Schema/input nesting limit exceeded.', field=label)
    if schema is True:
        return
    if schema is False:
        raise Failure('Value is forbidden by the documented schema.', field=label)
    if not isinstance(schema, dict):
        raise Failure('Invalid bundled schema.', field=label)
    def check(candidate, sublabel=label, item=value):
        validate_value(item, candidate, sublabel, depth + 1)
    if '$ref' in schema:
        check(resolve_ref(schema['$ref']))
    for branch in schema.get('allOf', []):
        check(branch)
    for keyword in ('oneOf', 'anyOf'):
        if keyword in schema:
            matches = 0
            for branch in schema[keyword]:
                try:
                    check(branch)
                    matches += 1
                except Failure:
                    pass
            if not matches or (keyword == 'oneOf' and matches != 1):
                raise Failure('Value does not match the documented schema union.', field=label,
                              constraint=keyword, matching_branches=matches)
    if 'not' in schema:
        try:
            check(schema['not'])
        except Failure:
            pass
        else:
            raise Failure('Value matches a forbidden schema.', field=label)
    types = schema.get('type', [])
    types = [types] if isinstance(types, str) else types
    if schema.get('nullable') and value is None:
        return
    tests = {'null': lambda v: v is None, 'string': lambda v: isinstance(v, str),
             'integer': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
                                  and (isinstance(v, int) or (math.isfinite(v) and v == int(v))),
             'number': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and (isinstance(v, int) or math.isfinite(v)),
             'boolean': lambda v: isinstance(v, bool), 'array': lambda v: isinstance(v, list),
             'object': lambda v: isinstance(v, dict)}
    if types and not any(tests.get(t, lambda v: False)(value) for t in types):
        raise Failure('Incorrect parameter type.', field=label, expected=types)
    if 'enum' in schema and not any(json_equal(value, v) for v in schema['enum']):
        raise Failure('Parameter is outside documented enum.', field=label, allowed=schema['enum'])
    if 'const' in schema and not json_equal(value, schema['const']):
        raise Failure('Parameter does not match the documented constant.', field=label)
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        missing = set(schema.get('required', [])) - set(value)
        if missing:
            raise Failure('Required object fields are missing.', field=label, missing=sorted(missing))
        for key, item in value.items():
            matched = False
            if key in properties:
                check(properties[key], label + '.' + key, item)
                matched = True
            for pattern, subschema in schema.get('patternProperties', {}).items():
                if re.search(pattern, key):
                    check(subschema, label + '.' + key, item)
                    matched = True
            if not matched:
                additional = schema.get('additionalProperties', True)
                check(additional, label + '.' + key, item)
        for keyword, comparison in (('minProperties', len(value) < schema.get('minProperties', 0)),
                                    ('maxProperties', len(value) > schema.get('maxProperties', math.inf))):
            if comparison:
                raise Failure('Object size is outside the documented limits.', field=label, constraint=keyword)
    if isinstance(value, list):
        for index, item in enumerate(value):
            check(schema.get('items', {}), label + '[' + str(index) + ']', item)
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', math.inf):
            raise Failure('Array size is outside the documented limits.', field=label)
        if schema.get('uniqueItems'):
            canonical = [json.dumps(v, sort_keys=True, allow_nan=False) for v in value]
            if len(canonical) != len(set(canonical)):
                raise Failure('Array items must be unique.', field=label)
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', math.inf):
            raise Failure('String length is outside the documented limits.', field=label)
        if 'pattern' in schema and re.search(schema['pattern'], value) is None:
            raise Failure('String does not match the documented pattern.', field=label)
        if schema.get('format') == 'byte':
            try:
                base64.b64decode(value, validate=True)
            except (ValueError, UnicodeError):
                raise Failure('Expected base64-encoded bytes.', field=label) from None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise Failure('Numbers must be finite.', field=label)
        for keyword, failed in (('minimum', value < schema.get('minimum', -math.inf)),
                                ('maximum', value > schema.get('maximum', math.inf)),
                                ('exclusiveMinimum', value <= schema.get('exclusiveMinimum', -math.inf)),
                                ('exclusiveMaximum', value >= schema.get('exclusiveMaximum', math.inf))):
            if failed:
                raise Failure('Number is outside the documented limits.', field=label, constraint=keyword)
        if schema.get('format') == 'int64' and not -(2**63) <= value < 2**63:
            raise Failure('Integer is outside the int64 range.', field=label)
        if 'multipleOf' in schema:
            quotient = value / schema['multipleOf']
            if not math.isclose(quotient, round(quotient), rel_tol=1e-9, abs_tol=1e-9):
                raise Failure('Number is not a documented multiple.', field=label)


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise Failure('Dates need an explicit UTC offset, for example 2026-09-14T00:00:00-05:00.')
        return int(dt.timestamp() * 1000)
    except ValueError:
        raise Failure('Invalid ISO date/time.') from None


def normalize_phone(value):
    text = str(value).strip()
    if not re.fullmatch(r'\+[1-9][0-9 ()-]*', text):
        raise Failure('Use an explicit E.164 phone number beginning with + and country code; local numbers and suffix matching are not supported.')
    digits = re.sub(r'[^0-9]', '', text)
    if not 7 <= len(digits) <= 15:
        raise Failure('Use a full E.164 phone number with country code, up to 15 digits.')
    return digits


def checked_id(value):
    if not re.fullmatch(r'[0-9]+', str(value)):
        raise Failure('Expected a numeric Dialpad ID.')
    return str(value)


def call_in_scope(call, target_id, target_type):
    kind = target_type.lower().replace('_', '')
    for field in ('target', 'entry_point_target', 'proxy_target'):
        target = call.get(field) or {}
        if str(target.get('id')) == str(target_id) and str(target.get('type', '')).lower().replace('_', '') == kind:
            return True
    return False


def compact_call(call):
    keys = ('call_id', 'date_started', 'date_connected', 'date_ended', 'direction', 'state',
            'duration', 'total_duration', 'was_recorded', 'is_transferred', 'master_call_id',
            'entry_point_call_id', 'operator_call_id', 'target', 'entry_point_target', 'proxy_target')
    result = {k: call[k] for k in keys if k in call}
    for k in ('target', 'entry_point_target', 'proxy_target'):
        if isinstance(result.get(k), dict):
            result[k] = {f: result[k][f] for f in ('id', 'type', 'name') if f in result[k]}
    contact = call.get('contact') or {}
    result['contact'] = {k: contact[k] for k in ('id', 'name', 'type') if k in contact}
    phone = str(call.get('external_number') or contact.get('phone') or '')
    result['phone_last4'] = re.sub(r'\D', '', phone)[-4:]
    return result


def query_pairs(query, parameters=None):
    """Serialize OpenAPI query values; the pinned spec currently uses scalars."""
    definitions = {p['name']: p for p in (parameters or []) if p.get('in') == 'query'}
    pairs = []
    def scalar(value):
        if value is None:
            return ''
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (list, dict)):
            raise Failure('Nested query collections are not supported by OpenAPI form serialization.')
        return str(value)
    for name, value in query.items():
        param = definitions.get(name, {})
        style = param.get('style', 'form')
        explode = param.get('explode', style == 'form')
        if isinstance(value, list):
            if style == 'form' and explode:
                pairs.extend((name, scalar(v)) for v in value)
            elif style in ('form', 'spaceDelimited', 'pipeDelimited'):
                delimiter = {'form': ',', 'spaceDelimited': ' ', 'pipeDelimited': '|'}[style]
                pairs.append((name, delimiter.join(scalar(v) for v in value)))
            else:
                raise Failure('Unsupported documented query serialization.', field=name, style=style)
        elif isinstance(value, dict):
            if style == 'deepObject':
                pairs.extend((name + '[' + k + ']', scalar(v)) for k, v in value.items())
            elif style == 'form' and explode:
                pairs.extend((k, scalar(v)) for k, v in value.items())
            elif style == 'form':
                pairs.append((name, ','.join(x for k, v in value.items() for x in (k, scalar(v)))))
            else:
                raise Failure('Unsupported documented query serialization.', field=name, style=style)
        elif style == 'form':
            pairs.append((name, scalar(value)))
        else:
            raise Failure('Unsupported documented scalar query serialization.', field=name, style=style)
    if any(name.lower().replace('-', '_') in SECRET_FIELDS for name, _ in pairs):
        raise Failure('Credentials cannot be supplied in query parameters.')
    return pairs


class Client:
    def __init__(self, key, budget=120):
        global ACTIVE_SECRET
        self.key = key
        ACTIVE_SECRET = key
        self.opener = build_opener(NoRedirect())
        self.started = time.monotonic()
        self.budget = budget
        self.requests = 0
        self.bytes = 0
        self.last = {}

    def wait(self, seconds):
        if time.monotonic() - self.started + seconds >= self.budget:
            raise Failure('Request time budget exhausted; resume from the last receipt or cursor.', requests=self.requests)
        time.sleep(max(0, seconds))

    def request(self, method, path, query=None, body=None, parameters=None):
        if not re.fullmatch(r'/api/v2/[A-Za-z0-9_/%.-]+', path) or '..' in path or any(escaped in path.lower() for escaped in ('%2e', '%2f', '%5c', '%25')):
            raise Failure('Invalid Dialpad API path.')
        if query and any(k.lower() in ('apikey', 'api_key', 'token', 'access_token', 'authorization') for k in query):
            raise Failure('Credentials cannot be supplied in query parameters.')
        retryable = method == 'GET'
        bucket, interval = rate_policy(method, path)
        payload = None if body is None else json.dumps(body, allow_nan=False).encode()
        params = query_pairs(query or {}, parameters)
        url = BASE + path + ('?' + urlencode(params) if params else '')
        for attempt in range(3 if retryable else 1):
            self.wait(max(0, self.last.get(bucket, 0) + interval - time.monotonic()))
            self.last[bucket] = time.monotonic()
            self.requests += 1
            request = Request(url, data=payload, method=method, headers={
                'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json',
                'Content-Type': 'application/json', 'User-Agent': 'DialpadAgentSkill/2.0'})
            try:
                remaining = self.budget - (time.monotonic() - self.started)
                if remaining <= 0:
                    raise Failure('Request time budget exhausted.', requests=self.requests)
                with self.opener.open(request, timeout=min(30, remaining)) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                    self.bytes += len(raw)
                    if len(raw) > 16 * 1024 * 1024:
                        raise Failure('Response exceeded the 16 MiB limit.')
                    return json.loads(raw) if raw else {}
            except HTTPError as error:
                status = error.code
                retry_after = error.headers.get('Retry-After', '')
                error.close()
                if retryable and status in (429, 500, 502, 503, 504) and attempt < 2:
                    delay = retry_delay(retry_after, attempt)
                    if delay is not None and delay <= 10:
                        self.wait(delay)
                        continue
                hint = {401: 'Authentication failed; check the selected credential, do not try other tenants.',
                        403: 'Permission or scope denied; this is not an empty result.',
                        404: 'Record unavailable, wrong ID, or artifact not generated; not proof of no conversation.',
                        429: 'Rate limited; resume later and honor Retry-After.'}.get(status, 'Dialpad rejected the request.')
                raise Failure(hint, status=status, retry_after=retry_after or None, method=method,
                              path=path, outcome='unknown' if method != 'GET' and status >= 500 else 'rejected') from None
            except (URLError, TimeoutError, OSError):
                if retryable and attempt < 2:
                    self.wait(2 ** attempt)
                    continue
                raise Failure('Transport failed. No automatic write retry.', method=method, path=path,
                              outcome='unknown' if method != 'GET' else 'unavailable') from None
            except (ValueError, UnicodeError):
                raise Failure('Provider returned a non-JSON response; no automatic retry.',
                              outcome='unknown' if method != 'GET' else 'unavailable') from None

    def pages(self, path, query, maximum, cursor=None, on_item=None, parameters=None):
        items, seen_ids, seen_cursors = [], set(), set()
        resumed = bool(cursor)
        scanned = 0
        for page in range(maximum):
            params = dict(query)
            if cursor:
                if cursor in seen_cursors:
                    raise Failure('Provider repeated a pagination cursor; completeness unknown.')
                seen_cursors.add(cursor)
                params['cursor'] = cursor
            try:
                data = self.request('GET', path, params, parameters=parameters)
            except Failure as error:
                error.details['partial_result'] = {'items':items,'pages':page,'scanned':scanned,
                    'complete':False,'next_cursor':cursor,'started_from_cursor':resumed}
                raise
            if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                raise Failure('Unexpected list schema; expected an items array.')
            for item in data['items']:
                if not isinstance(item, dict):
                    raise Failure('Unexpected list item schema; expected objects.')
                scanned += 1
                ident = item.get('call_id', item.get('id'))
                if ident is not None:
                    ident = str(ident)
                    if ident in seen_ids:
                        continue
                    seen_ids.add(ident)
                selected = on_item(item) if on_item else item
                if selected is not None:
                    items.append(selected)
            cursor = data.get('cursor')
            if not cursor:
                break
        return {'items': items, 'pages': page + 1, 'scanned': scanned,
                'complete': not bool(cursor) and not resumed, 'remaining_complete': not bool(cursor), 'next_cursor': cursor or None,
                'started_from_cursor': resumed}


def read_call(client, call_id, target_id, target_type):
    data = client.request('GET', '/api/v2/call/' + checked_id(call_id))
    if not isinstance(data, dict) or str(data.get('call_id')) != str(call_id):
        raise Failure('Provider returned a different call ID.')
    if not call_in_scope(data, target_id, target_type):
        raise Failure('Call target does not match the requested client; transcript/recap was not fetched.',
                      call_id=str(call_id), expected_target_id=str(target_id), expected_target_type=target_type)
    return data


def retry_delay(retry_after, attempt):
    if not retry_after:
        return 2 ** attempt
    try:
        if retry_after.replace('.', '', 1).isdigit():
            return max(0, float(retry_after))
        return max(0, parsedate_to_datetime(retry_after).timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return None  # An unreadable provider backoff is not permission to retry.


def required_body_fields(schema, depth=0):
    if not isinstance(schema, dict) or depth > 64:
        return False
    if schema.get('required'):
        return True
    if '$ref' in schema and required_body_fields(resolve_ref(schema['$ref']), depth + 1):
        return True
    if any(required_body_fields(branch, depth + 1) for branch in schema.get('allOf', [])):
        return True
    return any(schema.get(k) and all(required_body_fields(branch, depth + 1) for branch in schema[k])
               for k in ('oneOf', 'anyOf'))


def rate_policy(method, path):
    # Per-process pacing only. Other clients can share the provider quota.
    for name, operation in CATALOG['operations'].items():
        pattern = re.sub(r'\\\{[^}]+\\\}', '[^/]+', re.escape(operation['path']))
        if operation['method'] == method and re.fullmatch(pattern, path):
            intervals = [seconds / count for count, seconds in operation.get('rate_limits', []) if count > 0]
            interval = max([.15] + intervals)
            if method in ('POST', 'PATCH', 'PUT', 'DELETE') and operation['path'].startswith('/api/v2/callrouters'):
                interval = max(interval, 300.1)  # The public rate-limits guide is stricter than the schema.
            return name, interval
    return 'general', .15


def operation_request(args):
    operation = CATALOG['operations'].get(args.operation)
    if not operation:
        if args.operation in CATALOG.get('auth_operations', {}):
            raise Failure('OAuth setup is a separate authorization flow. See the authentication reference; bearer API execution is disabled for OAuth endpoints.')
        raise Failure('Unknown operation. Use catalog to find documented operations.')
    values, query = obj(args.path_params), obj(args.query)
    parameters = operation.get('parameters', [])
    path_parameters = {p['name']: p for p in parameters if p['in'] == 'path'}
    if set(values) != set(path_parameters):
        raise Failure('Path parameters must match exactly.', required=sorted(path_parameters))
    allowed = {p['name'] for p in parameters if p['in'] == 'query'}
    missing = {p['name'] for p in parameters if p.get('required') and p['in'] == 'query'} - set(query)
    if set(query) - allowed or missing:
        raise Failure('Query parameters do not match the documented operation.', unknown=sorted(set(query)-allowed), missing=sorted(missing))
    for param in parameters:
        if param['in'] == 'query' and param['name'] in query:
            validate_value(query[param['name']], param.get('schema', {}), param['name'])
    path = operation['path']
    for key, value in values.items():
        if not isinstance(value, (int, str)) or isinstance(value, bool):
            raise Failure('Path parameters must be strings or integers.', field=key)
        schema = path_parameters[key].get('schema', {})
        # IDs are often pasted as strings; validate their numeric value while
        # preserving exact representation in the encoded URL segment.
        checked = value
        if schema.get('type') == 'integer' and isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value):
            checked = int(value)
        validate_value(checked, schema, key)
        text = str(value)
        if not text or text == '.' or len(text) > 2048 or '..' in text or any(ord(c) < 32 for c in text) or any(c in text for c in '/%\\'):
            raise Failure('Invalid path parameter.', field=key)
        path = path.replace('{' + key + '}', quote(text, safe=''))
    if hasattr(args, '_body_data'):
        body = args._body_data
    elif args.body_file:
        file = Path(args.body_file).expanduser()
        if file.stat().st_size > 16 * 1024 * 1024:
            raise Failure('Body file exceeds the 16 MiB limit.')
        body = json_value(file.read_text())
    else:
        body = None
    has_body = bool(args.body_file) or hasattr(args, '_body_data')
    if has_body:
        if operation['method'] == 'GET' or operation.get('body') is None:
            raise Failure('This operation has no documented JSON request body.')
        validate_value(body, operation['body'], 'body')
    elif operation.get('body_required') or required_body_fields(operation.get('body', {})):
        raise Failure('This operation requires --body-file because its documented schema has required body fields.')
    return operation, path, query, body


def csv_filter(args):
    phone = normalize_phone(args.phone) if args.phone else None
    matches, seen = [], set()
    with open(args.input, newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        scanned = 0
        for row in reader:
            scanned += 1
            if phone:
                numbers = re.split(r'[,;]', str(row.get('from_phone', '')) + ',' + str(row.get('to_phone', '')))
                if phone not in [re.sub(r'\D', '', n) for n in numbers]:
                    continue
            if args.contains and args.contains.casefold() not in ' '.join(str(v or '') for v in row.values()).casefold():
                continue
            ident = row.get('message_id')
            if ident and ident in seen:
                continue
            if ident:
                seen.add(ident)
            matches.append(row)
    return {'items': matches, 'matched': len(matches), 'scanned': scanned, 'columns': fields,
            'dedupe': 'message_id when present', 'complete': True,
            'coverage_note': 'Complete for this file only; provider export coverage and text permissions require verification.'}


def transcript_excerpt(data, call_id, contains=None, context=2, offset=0, max_lines=150):
    if not isinstance(data, dict) or str(data.get('call_id')) != str(call_id) or not isinstance(data.get('lines'), list):
        raise Failure('Unexpected transcript schema or mismatched call ID.')
    lines = data['lines']
    if any(not isinstance(line, dict) for line in lines):
        raise Failure('Unexpected transcript line schema.')
    if contains:
        found = [i for i, line in enumerate(lines) if contains.casefold() in str(line.get('content', '')).casefold()]
        selected = sorted({j for i in found for j in range(max(0, i-context), min(len(lines), i+context+1))})
    else:
        selected = list(range(len(lines)))
    total = len(selected)
    selected = selected[offset:offset+max_lines]
    return {'lines': [dict(lines[i], line_number=i+1) for i in selected],
            'total_lines': len(lines), 'matching_context_lines': total, 'excerpt': bool(contains),
            'complete': offset == 0 and len(selected) == total,
            'next_offset': offset+len(selected) if offset+len(selected) < total else None}


def search_transcripts(args, client):
    if not args.contains.strip():
        raise Failure('Transcript search requires a nonempty literal search phrase.')
    listing_args = argparse.Namespace(**vars(args))
    listing_args.command = 'calls'
    listing = execute(listing_args, client)
    calls = listing.pop('items')
    matches, scanned, unavailable, unscanned = [], [], [], []
    bounded = calls[:args.max_calls]
    for index, call in enumerate(bounded):
        call_id = checked_id(call['call_id'])
        # The same scoped call-list response already verified this exact call
        # ID and client target. No extra call-detail request is needed here.
        try:
            data = client.request('GET', '/api/v2/transcripts/' + call_id)
            excerpt = transcript_excerpt(data, call_id, args.contains, args.context, 0, args.max_lines)
        except Failure as error:
            if error.details.get('status') == 401:
                raise
            unavailable.append({'call_id': call_id, 'error': str(error), 'status': error.details.get('status')})
            if 'budget' in str(error).lower():
                unscanned.extend(str(c['call_id']) for c in bounded[index+1:])
                break
            continue
        scanned.append(call_id)
        if excerpt['matching_context_lines']:
            matches.append({'call': call, **excerpt})
    unscanned.extend(str(c['call_id']) for c in calls[args.max_calls:])
    return {'scope': listing['scope'], 'matches': matches, 'scanned_call_ids': scanned,
            'unavailable': unavailable, 'unscanned_call_ids': unscanned,
            'calls_listing_complete': listing['complete'], 'next_cursor': listing['next_cursor'],
            'complete': listing['complete'] and not unavailable and not unscanned and all(m['complete'] for m in matches),
            'scope_verified': True,
            'coverage_note': 'Bounded literal substring scan of fetched transcripts. This is not native indexed MCP search; missing, unavailable, unscanned, or truncated records limit coverage.'}


def execute(args, client):
    if args.command == 'doctor':
        result = {'connection': args.connection, 'credential_available': True, 'network_checked': bool(args.live),
                  'credential_source': 'selected env file' if args.env_file else 'process environment',
                  'catalog_operations': len(CATALOG['operations']), 'base_url': BASE,
                  'limits': {'budget_seconds': args.budget, 'response_limit_mib': 16, 'automatic_write_retries': 0}}
        if args.live:
            data = client.request('GET', '/api/v2/offices/primary')
            if not isinstance(data, dict):
                raise Failure('Unexpected primary-office response.')
            result['authentication_verified'] = True
            result['primary_office'] = {k: data[k] for k in ('id', 'name') if k in data}
            result['verification_note'] = 'This proves one authorized read, not access to every operation, scope, or client.'
        return result
    if args.command == 'search-transcripts':
        return search_transcripts(args, client)
    if args.command == 'centers':
        query = {'name_search': args.name} if args.name else {}
        return client.pages('/api/v2/callcenters', query, args.max_pages, args.cursor,
            lambda c: {k: c[k] for k in ('id', 'name', 'state', 'office_id', 'phone_numbers') if k in c})
    if args.command == 'calls':
        start, end = timestamp(args.after), timestamp(args.before)
        if not start < end <= int(time.time()*1000):
            raise Failure('Call window must be ordered and cannot end in the future.')
        phone = normalize_phone(args.phone) if args.phone else None
        def select(call):
            if not call_in_scope(call, args.target_id, args.target_type):
                raise Failure('Provider returned a call outside the requested target; stopped.')
            contact = call.get('contact') or {}
            if args.name and args.name.casefold() not in str(contact.get('name', '')).casefold():
                return None
            if phone and phone not in [re.sub(r'\D', '', str(v)) for v in (call.get('external_number'), contact.get('phone')) if v]:
                return None
            return compact_call(call)
        result = client.pages('/api/v2/call', {'target_id': args.target_id, 'target_type': args.target_type,
            'started_after': start, 'started_before': end}, args.max_pages, args.cursor, select)
        result['scope'] = {'target_id': args.target_id, 'target_type': args.target_type, 'after': args.after, 'before': args.before}
        result['started_from_cursor'] = bool(args.cursor)
        result['count_unit'] = 'call legs; linked IDs are preserved, not assumed to be distinct conversations'
        return result
    if args.command in ('call', 'transcript', 'recap'):
        call = read_call(client, args.call_id, args.target_id, args.target_type)
        result = {'call': compact_call(call), 'scope_verified': True}
        if args.command == 'call':
            result['details'] = call
        elif args.command == 'recap':
            result['recap'] = client.request('GET', '/api/v2/call/' + args.call_id + '/ai_recap')
        else:
            data = client.request('GET', '/api/v2/transcripts/' + args.call_id)
            result.update(transcript_excerpt(data, args.call_id, args.contains, args.context, args.offset, args.max_lines))
        return result
    if args.command == 'api':
        operation, path, query, body = operation_request(args)
        if operation['method'] != 'GET' and not args.execute:
            return {'dry_run': True, 'operation': args.operation, 'method': operation['method'], 'path': path,
                    'query': query, 'body': body, 'note': 'No API request made. Execute only within the user-authorized action.'}
        if args.max_pages > 1:
            if operation['method'] != 'GET' or 'cursor' not in {p['name'] for p in operation['parameters']}:
                raise Failure('Automatic pagination is only available for documented cursor GET operations.')
            cursor = query.pop('cursor', None)
            return client.pages(path, query, args.max_pages, cursor, parameters=operation['parameters'])
        data = client.request(operation['method'], path, query, body, parameters=operation['parameters'])
        return {'operation': args.operation, 'method': operation['method'], 'path': path, 'data': data,
                'next_cursor': data.get('cursor') if isinstance(data,dict) else None,
                'started_from_cursor': bool(query.get('cursor')),
                'remaining_complete': not bool(data.get('cursor')) if isinstance(data, dict) else True,
                'complete': not bool(query.get('cursor')) and (not bool(data.get('cursor')) if isinstance(data, dict) else True)}
    raise Failure('Unsupported command.')


def selected_catalog(args):
    operations = dict(CATALOG['operations'])
    if args.include_auth:
        operations.update(CATALOG.get('auth_operations', {}))
    selected = [{'operation': key, **(operation if args.details else {
                    field: operation[field] for field in ('method', 'path', 'summary')})}
                for key, operation in operations.items()
                if args.search.casefold() in (key + ' ' + operation['summary']).casefold()]
    result = {'checked_at': CATALOG['checked_at'], 'operations': selected}
    if args.details:
        schemas = {}
        def visit(node):
            if isinstance(node, dict):
                if '$ref' in node:
                    reference = node['$ref']
                    name = reference.rsplit('/', 1)[-1].replace('~1', '/').replace('~0', '~')
                    if name not in schemas:
                        schemas[name] = resolve_ref(reference)
                        visit(schemas[name])
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)
        visit(selected)
        result['components'] = {'schemas': schemas}
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', help='Connection JSON file; defaults to DIALPAD_CONFIG_FILE, then HERMES_HOME/.config, XDG_CONFIG_HOME, or ~/.config under dialpad-skills/connection.json.')
    p.add_argument('--via', choices=['auto', 'local', 'ssh'])
    p.add_argument('--env-file', help='Local file containing DIALPAD_API_KEY; parsed without sourcing.')
    p.add_argument('--ssh-host', help='Explicit SSH host or alias. No implicit remote host.')
    p.add_argument('--remote-env-file', help='Credential file on the selected SSH host.')
    p.add_argument('--budget', type=int, default=120, help='Total request time budget, seconds.')
    subs = p.add_subparsers(dest='command', required=True)
    c = subs.add_parser('catalog'); c.add_argument('--search', default=''); c.add_argument('--details', action='store_true')
    c.add_argument('--include-auth', action='store_true', help='Show OAuth setup metadata; not executable through api.')
    c = subs.add_parser('doctor'); c.add_argument('--live', action='store_true', help='Also make a read-only primary-office request.')
    c = subs.add_parser('centers'); c.add_argument('--name'); c.add_argument('--cursor'); c.add_argument('--max-pages', type=int, default=5)
    for command in ('calls', 'search-transcripts', 'call', 'transcript', 'recap'):
        c = subs.add_parser(command)
        c.add_argument('--target-id', required=True, type=checked_id)
        c.add_argument('--target-type', default='callcenter', choices=['callcenter', 'department', 'office', 'user'])
        if command in ('calls', 'search-transcripts'):
            c.add_argument('--after', required=True); c.add_argument('--before', required=True)
            c.add_argument('--name'); c.add_argument('--phone'); c.add_argument('--cursor'); c.add_argument('--max-pages', type=int, default=20)
        else:
            c.add_argument('--call-id', required=True, type=checked_id)
        if command in ('transcript', 'search-transcripts'):
            c.add_argument('--contains', required=command == 'search-transcripts'); c.add_argument('--context', type=int, default=2)
            c.add_argument('--max-lines', type=int, default=150); c.add_argument('--offset', type=int, default=0)
        if command == 'search-transcripts':
            c.add_argument('--max-calls', type=int, default=20)
    c = subs.add_parser('api'); c.add_argument('operation'); c.add_argument('--path-params', default='{}')
    c.add_argument('--query', default='{}'); c.add_argument('--body-file'); c.add_argument('--execute', action='store_true')
    c.add_argument('--max-pages', type=int, default=1)
    c = subs.add_parser('filter-csv'); c.add_argument('--input', required=True); c.add_argument('--phone'); c.add_argument('--contains')
    return p


def resolve_connection(args):
    cli_via, cli_local, cli_remote = args.via, args.env_file, args.remote_env_file
    if cli_local and cli_remote:
        raise Failure('Choose either --env-file for local execution or --remote-env-file for SSH execution.')
    explicit_path = args.config or os.environ.get('DIALPAD_CONFIG_FILE')
    default_root = (Path(os.environ['HERMES_HOME']) / '.config' if os.environ.get('HERMES_HOME') else
                    Path(os.environ.get('XDG_CONFIG_HOME', '~/.config')))
    path = Path(explicit_path).expanduser() if explicit_path else (default_root / 'dialpad-skills/connection.json').expanduser()
    config = {}
    if explicit_path or path.exists():
        if path.stat().st_size > 65536:
            raise Failure('Connection config exceeds the 64 KiB limit.')
        config = obj(path.read_text())
        allowed = {'via', 'env_file', 'ssh_host', 'remote_env_file'}
        if set(config) - allowed:
            raise Failure('Connection config permits connection locations only, never credentials or endpoint overrides.', unknown=sorted(set(config) - allowed))
        if any(not isinstance(value, str) or not value for value in config.values()):
            raise Failure('Connection config values must be nonempty strings.')
    for field in ('via', 'env_file', 'ssh_host', 'remote_env_file'):
        value = getattr(args, field) or os.environ.get('DIALPAD_' + field.upper()) or config.get(field)
        setattr(args, field, value)
    if cli_local and cli_via is None:
        args.via = 'local'
    elif cli_remote and cli_via is None:
        args.via = 'ssh'
    elif cli_via is None and not os.environ.get('DIALPAD_VIA'):
        env_local, env_remote = os.environ.get('DIALPAD_ENV_FILE'), os.environ.get('DIALPAD_REMOTE_ENV_FILE')
        if env_local and env_remote:
            raise Failure('Conflicting local and remote environment files; select --via explicitly.')
        if env_local:
            args.via = 'local'
        elif env_remote:
            args.via = 'ssh'
    args.via = args.via or 'auto'
    if args.via not in ('auto', 'local', 'ssh'):
        raise Failure('Connection via must be auto, local, or ssh.')
    use_ssh = args.via == 'ssh' or (args.via == 'auto' and not args.env_file
                                    and not os.environ.get('DIALPAD_API_KEY') and bool(args.ssh_host))
    if use_ssh and cli_local:
        raise Failure('--env-file selects a local credential file; use --remote-env-file with SSH.')
    if use_ssh and (not args.ssh_host or not re.fullmatch(r'[A-Za-z0-9_.@-]+', args.ssh_host) or args.ssh_host.startswith('-')):
        raise Failure('An explicit valid SSH host is required; set --ssh-host or DIALPAD_SSH_HOST.')
    if not use_ssh and cli_remote:
        raise Failure('--remote-env-file selects an SSH credential file; use --env-file for local execution.')
    args.connection = 'ssh' if use_ssh else 'local'
    return use_ssh


def local_run(args):
    client = Client(load_key(args.env_file), args.budget)
    result = execute(args, client)
    result['metrics'] = {'requests': client.requests, 'response_bytes': client.bytes,
                         'elapsed_seconds': round(time.monotonic() - client.started, 3)}
    return result


def ssh_run(args):
    copied = vars(args).copy()
    copied.update(via='local', env_file=args.remote_env_file)
    if args.command == 'api' and args.body_file:
        copied['_body_data'] = operation_request(args)[3]
    # Source/catalog/request values cross encrypted SSH stdin, not a shell
    # command. The API credential is read only on the configured remote host.
    package = {'source': Path(__file__).read_text(), 'catalog': CATALOG, 'args': copied}
    encoded = base64.b64encode(json.dumps(package, allow_nan=False).encode()).decode()
    bootstrap = "import base64,json,argparse\np=json.loads(base64.b64decode(" + repr(encoded) + "))\n"
    bootstrap += "g={'__name__':'dialpad_remote','BUNDLED_CATALOG':p['catalog']}\nexec(p['source'],g)\na=argparse.Namespace(**p['args'])\n"
    bootstrap += "try:\n g['emit'](g['local_run'](a))\n"
    bootstrap += "except g['Failure'] as e:\n g['emit']({'error':str(e),**e.details});raise SystemExit(1)\n"
    bootstrap += "except (OSError,ValueError):\n g['emit']({'error':'Remote input or transport failed; no write was retried.'});raise SystemExit(1)\n"
    result = subprocess.run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', args.ssh_host, 'python3 -'],
                            input=bootstrap, text=True, capture_output=True, timeout=args.budget + 40)
    if result.stdout:
        try:
            receipt = obj(result.stdout)
        except Failure:
            raise Failure('SSH returned an unexpected response; raw remote output was suppressed. Executed write outcomes may be unknown.') from None
        emit(receipt)
    else:
        emit({'error': 'SSH transport failed; no credentials were copied. Executed write outcomes may be unknown.', 'exit_code': result.returncode})
    return result.returncode if result.stdout else (result.returncode or 1)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if args.budget < 1 or args.budget > 600 or not 1 <= getattr(args, 'max_pages', 1) <= 200:
            raise Failure('Budget must be 1..600 seconds and max-pages must be 1..200.')
        if getattr(args, 'offset', 0) < 0 or not 1 <= getattr(args, 'max_lines', 1) <= 10000 or not 0 <= getattr(args, 'context', 0) <= 100:
            raise Failure('Transcript offset must be nonnegative; max-lines must be 1..10000 and context 0..100.')
        if not 1 <= getattr(args, 'max_calls', 1) <= 100:
            raise Failure('Max-calls must be 1..100.')
        if args.command == 'search-transcripts' and args.offset:
            raise Failure('Use transcript --offset for a single-call excerpt; search-transcripts uses call and page bounds.')
        if args.command == 'filter-csv':
            emit(csv_filter(args)); return 0
        require_catalog()  # Public source requires local bootstrap before credentials or transport.
        if args.command == 'catalog':
            emit(selected_catalog(args)); return 0
        if args.command == 'api':
            operation_request(args)  # Validate before credential access or transport.
            if CATALOG['operations'][args.operation]['method'] != 'GET' and not args.execute:
                emit(execute(args, None)); return 0
        use_ssh = resolve_connection(args)
        if use_ssh:
            return ssh_run(args)
        emit(local_run(args)); return 0
    except Failure as error:
        emit({'error': str(error), **error.details}); return 1
    except (OSError, ValueError, subprocess.TimeoutExpired):
        emit({'error': 'Local input or transport failed; inspect paths and JSON. A timed-out write has unknown outcome; do not retry blindly.'}); return 1


if __name__ == '__main__':
    sys.exit(main())
