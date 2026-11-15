import os,unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
import threads_oauth as o
class OAuthTests(unittest.TestCase):
 def setUp(self):self.env=patch.dict(os.environ,{'THREADS_APP_SECRET':'test-only-secret'});self.env.start();self.addCleanup(self.env.stop)
 def test_scope_and_exact_redirect(self):
  url,cookie=o.start(now=1000);q=parse_qs(urlsplit(url).query);self.assertIn('threads_keyword_search',q['scope'][0]);self.assertEqual(q['redirect_uri'],[o.CALLBACK]);o.validate_state(q['state'][0],cookie,now=1100)
  for state,c,now in [(q['state'][0]+'x',cookie,1100),(q['state'][0],'',1100),(q['state'][0],cookie,1601),(q['state'][0],cookie,999)]:
   with self.assertRaises(o.OAuthError):o.validate_state(state,c,now)
 def callback(self):
  url,cookie=o.start();return {'state':parse_qs(urlsplit(url).query)['state'],'code':['private-code']},cookie
 def test_validates_account_and_search_then_displays_only_final_token(self):
  params,cookie=self.callback();user={'id':o.EXPECTED_ID,'username':'luen.dayori'}
  responses=[{'user_id':o.EXPECTED_ID,'access_token':'short-private'},user,{'data':[]},{'access_token':'long-private'},user,{'data':[]}]
  with patch.object(o,'request_json',side_effect=responses) as request:
   result=o.complete(params,cookie)
  self.assertIn('long-private',result);self.assertNotIn('short-private',result);self.assertNotIn('private-code',result);self.assertNotIn('test-only-secret',result);self.assertEqual(request.call_count,6);self.assertNotIn('THREADS_ACCESS_TOKEN',os.environ)
 def test_wrong_account_never_gets_token_page(self):
  params,cookie=self.callback()
  with patch.object(o,'request_json',return_value={'user_id':'other','access_token':'private'}) as request:
   with self.assertRaises(o.OAuthError):o.complete(params,cookie)
   self.assertEqual(request.call_count,1)
 def test_search_denial_stops_before_long_token(self):
  params,cookie=self.callback()
  with patch.object(o,'request_json',side_effect=[{'user_id':o.EXPECTED_ID,'access_token':'private'},{'id':o.EXPECTED_ID,'username':'luen.dayori'},o.OAuthError('meta_request_failed')]) as request:
   with self.assertRaises(o.OAuthError):o.complete(params,cookie)
   self.assertEqual(request.call_count,3)
 def test_missing_configuration(self):
  with patch.dict(os.environ,{'THREADS_APP_SECRET':''}):
   with self.assertRaises(o.OAuthError):o.start()
if __name__=='__main__':unittest.main()
