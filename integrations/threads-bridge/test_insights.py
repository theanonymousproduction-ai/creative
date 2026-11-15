"""Bounded analytics and encrypted delivery; no real Meta credentials."""
import base64
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from test_discovery import bridge, runner

class InsightsTests(unittest.TestCase):
    def user(self):return {'id':'123','username':'luen.dayori'}
    def test_post_insights_check_ownership_and_strip_every_non_numeric_field(self):
        calls=[]
        def meta(path,form=None):
            self.assertIsNone(form);calls.append(path)
            if path=='/789?fields=id,username':return {'id':'789','username':'luen.dayori'}
            return {'data':[{'name':'views','period':'lifetime','values':[{'value':12,'secret':'DO_NOT_LEAK'}], 'secret':'DO_NOT_LEAK'}], 'paging':{'next':'DO_NOT_LEAK'}}
        with patch.object(bridge,'check_account',return_value=self.user()),patch.object(bridge,'meta',side_effect=meta):
            result=bridge.read_threads({'operation':'post_insights','post_id':'789'})
        self.assertEqual(result['data'][0]['values'][0]['value'],12)
        self.assertEqual(result['missing_metrics'],['likes','replies','reposts','quotes'])
        self.assertNotIn('DO_NOT_LEAK',json.dumps(result))
        self.assertTrue(calls[-1].startswith('/789/insights?metric='))

    def test_wrong_owner_never_calls_insights(self):
        with patch.object(bridge,'check_account',return_value=self.user()),patch.object(bridge,'meta',return_value={'id':'789','username':'other'}) as meta:
            with self.assertRaises(bridge.Problem):bridge.read_threads({'operation':'post_insights','post_id':'789'})
        self.assertEqual(meta.call_count,1)

    def test_account_insights_only_requests_follower_snapshot(self):
        with patch.object(bridge,'check_account',return_value=self.user()),patch.object(bridge,'meta',return_value={'data':[{'name':'followers_count','total_value':{'value':7}}]}) as meta:
            result=bridge.read_threads({'operation':'account_insights'})
        meta.assert_called_once_with('/me/threads_insights?metric=followers_count')
        self.assertEqual(result['data'][0]['total_value'],7)

    def test_arbitrary_metric_url_and_bad_ids_cannot_reach_meta(self):
        for params in [{'operation':'account_insights','metric':'private'}, {'operation':'post_insights','post_id':'../me'}, {'operation':'post_insights','post_id':'1','url':'https://evil.invalid'}]:
            with patch.object(bridge,'check_account',return_value=self.user()),patch.object(bridge,'meta') as meta:
                with self.assertRaises(bridge.Problem):bridge.read_threads(params)
                meta.assert_not_called()

    def test_runner_requires_encrypted_recipient_before_using_credentials(self):
        with patch.object(runner,'urlopen') as http:
            with self.assertRaises(runner.ReadError):runner.process({'schema':1,'account':'luen.dayori','operation':'post_insights','post_id':'789'})
            http.assert_not_called()

    def test_v5_health_can_read_discovery_without_false_upgrade_error(self):
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*_):self.close()
        def get(req,timeout):
            self.assertEqual(req.get_method(),'GET')
            if req.full_url.endswith('/health'):return Response(b'{"version":"explicit-repost-v5"}')
            return Response(b'{"account":{"username":"luen.dayori"},"operation":"own_posts","data":[]}')
        with patch.dict('os.environ',{'THREADS_BRIDGE_API_KEY':'offline'}),patch.object(runner,'urlopen',side_effect=get):
            self.assertEqual(runner.process({'schema':1,'account':'luen.dayori','operation':'own_posts'})['state'],'read_complete')

    def test_encryption_hides_numbers_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            key=Path(tmp)/'key.pem';cert=Path(tmp)/'cert.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-keyout',str(key),'-out',str(cert),'-days','1','-nodes','-subj','/CN=offline'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
            original={'state':'read_complete','operation':'post_insights','data':[{'name':'views','value':1234567}]}
            sealed=runner.seal_result(original,cert.read_text())
            self.assertNotIn('1234567',json.dumps(sealed));self.assertNotIn('data',sealed)
            plain=subprocess.run(['openssl','cms','-decrypt','-binary','-inform','DER','-inkey',str(key),'-recip',str(cert)],input=base64.b64decode(sealed['encrypted_result']),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
            self.assertEqual(json.loads(plain),original)

if __name__=='__main__':unittest.main()
