import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('dialpad',ROOT/'skills/dialpad-api/scripts/dialpad.py')
d=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(d)


class Reply:
    def __init__(self,data):self.raw=json.dumps(data).encode()
    def read(self,n):return self.raw[:n]
    def __enter__(self):return self
    def __exit__(self,*args):pass


class FakeOpener:
    def __init__(self,events):self.events=iter(events);self.seen=[]
    def open(self,request,timeout):
        self.seen.append(request)
        event=next(self.events)
        if isinstance(event,Exception):raise event
        return Reply(event)


def client(events):
    c=d.Client('TEST_ONLY_SECRET');c.opener=FakeOpener(events);c.wait=lambda seconds:None
    return c


class RunnerTests(unittest.TestCase):
    def test_pagination_dedupes_ids_keeps_legs_and_stops(self):
        c=client([{'items':[{'call_id':'1'},{'call_id':'2','entry_point_call_id':'1'}],'cursor':'p2'},
                  {'items':[{'call_id':'2'},{'call_id':'3'}],'cursor':'p3'}])
        r=c.pages('/api/v2/call',{'target_id':123},2)
        self.assertEqual([v['call_id'] for v in r['items']],['1','2','3'])
        self.assertFalse(r['complete']);self.assertEqual(r['next_cursor'],'p3')
        self.assertIn('cursor=p2',c.opener.seen[1].full_url)

    def test_repeated_cursor_fails(self):
        c=client([{'items':[],'cursor':'repeat'},{'items':[],'cursor':'repeat'}])
        with self.assertRaises(d.Failure):c.pages('/api/v2/call',{},3)

    def test_partial_failure_preserves_evidence_and_resume(self):
        c=client([{'items':[{'id':'1'}],'cursor':'next'},HTTPError('u',403,'',{},None)])
        with self.assertRaises(d.Failure) as e:c.pages('/api/v2/call',{},3)
        self.assertEqual(e.exception.details['partial_result']['next_cursor'],'next')
        self.assertEqual(e.exception.details['partial_result']['items'],[{'id':'1'}])

    def test_permission_denied_is_not_empty(self):
        c=client([HTTPError('u',403,'private provider body',{},None)])
        with self.assertRaises(d.Failure) as e:c.request('GET','/api/v2/call')
        self.assertEqual(c.requests,1);self.assertEqual(e.exception.details['status'],403)
        self.assertNotIn('private',str(e.exception));self.assertNotIn('TEST_ONLY_SECRET',str(e.exception))

    def test_get_retries_transient_failure(self):
        c=client([HTTPError('u',429,'',{'Retry-After':'0'},None),{'ok':True}])
        self.assertEqual(c.request('GET','/api/v2/call'),{'ok':True})
        self.assertEqual(c.requests,2)

    def test_long_retry_after_is_returned_without_sleep(self):
        c=client([HTTPError('u',429,'',{'Retry-After':'90'},None)])
        with self.assertRaises(d.Failure) as e:c.request('GET','/api/v2/call')
        self.assertEqual(c.requests,1);self.assertEqual(e.exception.details['retry_after'],'90')

    def test_write_never_retries_and_reports_unknown(self):
        for error in [URLError('network'),HTTPError('u',503,'',{},None)]:
            c=client([error])
            with self.assertRaises(d.Failure) as e:c.request('POST','/api/v2/sms',body={'text':'x'})
            self.assertEqual(c.requests,1);self.assertEqual(e.exception.details['outcome'],'unknown')

    def test_preview_needs_no_credentials_or_transport(self):
        with tempfile.TemporaryDirectory() as t:
            body=Path(t)/'body.json';body.write_text('{"text":"Draft only","to_numbers":["+15555550101"]}')
            out=io.StringIO()
            with patch.object(d,'load_key',side_effect=AssertionError('loaded credential')),patch.object(d.subprocess,'run',side_effect=AssertionError('ran ssh')),redirect_stdout(out):
                self.assertEqual(d.main(['api','sms.send','--body-file',str(body)]),0)
            self.assertTrue(json.loads(out.getvalue())['dry_run'])

    def test_redirect_and_path_escape_refused(self):
        with self.assertRaises(d.Failure):d.NoRedirect().redirect_request(None,None,302,'',{},'https://other.example/')
        c=client([])
        for path in ['/api/v2/../secret','https://other.example','/api/v2/%2e%2e/secret']:
            with self.assertRaises(d.Failure):c.request('GET',path)
        self.assertEqual(c.requests,0)

    def test_scope_mismatch_prevents_transcript_read(self):
        c=client([{'call_id':'456','target':{'type':'callcenter','id':'999'}}])
        a=d.parser().parse_args(['transcript','--target-id','123','--call-id','456'])
        with self.assertRaises(d.Failure):d.execute(a,c)
        self.assertEqual(c.requests,1)

    def test_proxy_scope_and_speaker_lines_preserved(self):
        call={'call_id':'456','target':{'type':'user','id':'5'},'proxy_target':{'type':'call_center','id':'123'}}
        lines=[{'name':'Speaker','content':'before','type':'transcript','time':'2026-09-14T20:00:00'},
               {'name':'Customer','content':'roof question','type':'transcript','time':'2026-09-14T20:00:01'},
               {'name':'Agent','content':'transfer','type':'transcript','time':'2026-09-14T20:00:02'}]
        c=client([call,{'call_id':'456','lines':lines}])
        a=d.parser().parse_args(['transcript','--target-id','123','--call-id','456','--contains','roof','--context','1','--max-lines','2'])
        r=d.execute(a,c)
        self.assertTrue(r['excerpt']);self.assertFalse(r['complete']);self.assertEqual(r['next_offset'],2)
        self.assertEqual(r['lines'][1]['name'],'Customer');self.assertEqual(r['lines'][1]['line_number'],2)

    def test_explicit_env_file_overrides_ambient_without_evaluation(self):
        with tempfile.TemporaryDirectory() as t,patch.dict(os.environ,{'DIALPAD_API_KEY':'wrong'}):
            p=Path(t)/'.env';p.write_text('UNRELATED=hidden\nexport DIALPAD_API_KEY="literal$(no_execution)" # ignored\n')
            self.assertEqual(d.load_key(str(p)),'literal$(no_execution)')

    def test_time_window_requires_timezone_and_no_future(self):
        with self.assertRaises(d.Failure):d.timestamp('2026-09-14')
        self.assertEqual(d.timestamp('2026-09-14T00:00:00-05:00'),d.timestamp('2026-09-14T05:00:00Z'))
        a=d.parser().parse_args(['calls','--target-id','123','--after','2026-09-14T00:00:00Z','--before','2099-01-01T00:00:00Z'])
        c=client([])
        with self.assertRaises(d.Failure):d.execute(a,c)
        self.assertEqual(c.requests,0)

    def test_full_phone_exact_match_and_multiple_filters(self):
        c=client([{'items':[{'call_id':'1','target':{'id':'123','type':'callcenter'},'contact':{'name':'Same Name','phone':'+15555550101'}},
                            {'call_id':'2','target':{'id':'123','type':'callcenter'},'contact':{'name':'Same Name','phone':'+15555550102'}}]}])
        a=d.parser().parse_args(['calls','--target-id','123','--after','2026-09-14T00:00:00Z','--before','2026-09-15T00:00:00Z','--phone','+15555550101','--name','Same'])
        r=d.execute(a,c);self.assertEqual(len(r['items']),1)
        self.assertNotIn('+15555550101',json.dumps(r))
        with self.assertRaises(d.Failure):d.normalize_phone('0101')

    def test_catalog_rejects_unknown_fields_and_wrong_types(self):
        for text in ['{"name":"wrong-query-field"}','{"name_search":123}']:
            a=d.parser().parse_args(['api','departments.listall','--query',text])
            with self.assertRaises(d.Failure):d.operation_request(a)

    def test_csv_handles_group_participants_multiline_and_dedup(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'export.csv';p.write_text('message_id,from_phone,to_phone,text\n1,+15555550200,"+15555550101,+15555550102","first\nsecond"\n1,+15555550200,+15555550101,duplicate\n2,+15555550300,+15555550400,unrelated\n')
            a=d.parser().parse_args(['filter-csv','--input',str(p),'--phone','+15555550101'])
            r=d.csv_filter(a);self.assertEqual(r['matched'],1);self.assertIn('\n',r['items'][0]['text'])
            self.assertIn('+15555550102',r['items'][0]['to_phone'])


if __name__=='__main__':unittest.main()
