"""Offline authorization, media binding and legacy dedup regression checks."""
import hashlib,io,json,time,unittest
from email.message import Message
from unittest.mock import patch
from test_server import BridgeTests,bridge
from test_chat_publish import ChatPublishTests,worker
URL='https://raw.githubusercontent.com/theanonymousproduction-ai/creative/'+'a'*40+'/assets/threads/C025-v5-excerpt-v3.png'
IMAGE={'image_url':URL,'image_sha256':'b'*64}
class ImageBridgeTests(unittest.TestCase):
 def setUp(self):self.f=BridgeTests();self.f.setUp();self.addCleanup(self.f.tearDown)
 def test_ticket_binds_image_and_creates_actual_image_once(self):
  preview=bridge.preview({'text':'已批准主帖',**IMAGE});payload=bridge.read_ticket(preview['ticket']);self.assertEqual(payload['image_url'],URL);self.assertEqual(payload['image_sha256'],IMAGE['image_sha256']);a=self.f.submit(preview['ticket']);self.assertEqual(a['state'],'published');forms=[b for a,b in self.f.calls if a=='/123/threads'];self.assertEqual(forms,[{'media_type':'IMAGE','text':'已批准主帖','image_url':URL}]);self.assertTrue(self.f.submit(preview['ticket'])['already_processed']);self.assertEqual(len([a for a,b in self.f.calls if a=='/123/threads']),1)
 def test_text_and_image_have_separate_but_stable_ledger_keys(self):
  a=self.f.submit(bridge.preview({'text':'same'})['ticket']);b=self.f.submit(bridge.preview({'text':'same',**IMAGE})['ticket']);self.assertNotEqual(a['digest'],b['digest'])
 def test_mutable_untrusted_urls_missing_hash_and_mixed_modes_rejected(self):
  bad=[{**IMAGE,'image_url':URL.replace('a'*40,'main')},{**IMAGE,'image_url':URL+'?x=1'},{**IMAGE,'image_url':URL.replace('/assets/threads/','/assets/threads/../')},{**IMAGE,'image_url':'https://evil.test/a.png'},{'image_url':URL},{**IMAGE,'attachment':'long text'},{**IMAGE,'reply_to_id':'123'}]
  for image in bad:
   with self.assertRaises(bridge.Problem):bridge.preview({'text':'approved',**image})
  self.assertEqual(self.f.calls,[])
class ImageRunnerTests(unittest.TestCase):
 def setUp(self):self.f=ChatPublishTests();self.f.setUp();self.addCleanup(self.f.tearDown)
 def job(self):
  j={**self.f.job(),**IMAGE};j['sha256']=worker.fingerprint(j['account'],j['text'],image_url=URL,image_sha256=IMAGE['image_sha256']);return j
 def test_image_hash_and_url_are_in_approval_fingerprint(self):
  j=self.job();self.assertEqual(worker.validate(j),j['sha256']);j['image_sha256']='c'*64
  with self.assertRaises(worker.Stop):worker.validate(j)
  legacy=worker.fingerprint('luen.dayori','hello');expected=hashlib.sha256(b'{"account":"luen.dayori","attachment":"","text":"hello"}').hexdigest();self.assertEqual(legacy,expected)
 def test_old_preview_that_ignores_image_cannot_claim_or_publish(self):
  with patch.object(worker,'verify_image_asset'):
   with self.assertRaises(worker.Stop):worker.process(self.job())
  self.assertEqual(self.f.claims,[]);self.assertFalse(any(a=='/publish' for a,b in self.f.calls))
 def test_verified_image_goes_through_durable_ledger(self):
  old=self.f.bridge
  def fake(path,secret,body=None):
   v=old(path,secret,body)
   if path=='/preview':v.update({k:body[k] for k in IMAGE})
   return v
  with patch.object(worker,'verify_image_asset') as checked,patch.object(worker,'bridge',side_effect=fake):
   r=worker.process(self.job());self.assertEqual(r['state'],'published');checked.assert_called_once_with(IMAGE)
   with self.assertRaises(worker.Stop):worker.process(self.job())
  self.assertEqual(sum(a=='/publish' for a,b in self.f.calls),1)
 def test_asset_failure_stops_before_any_bridge_or_claim(self):
  with patch.object(worker,'verify_image_asset',side_effect=worker.Stop('bad_image')):
   with self.assertRaises(worker.Stop):worker.process(self.job())
  self.assertEqual(self.f.calls,[]);self.assertEqual(self.f.claims,[])
 def test_public_image_bytes_are_verified(self):
  raw=b'\x89PNG\r\n\x1a\nfixture';h=Message();h['Content-Type']='image/png'
  def opener():
   response=io.BytesIO(raw);response.headers=h
   from types import SimpleNamespace
   return SimpleNamespace(open=lambda *a,**k:response)
  good={'image_url':URL,'image_sha256':hashlib.sha256(raw).hexdigest()}
  with patch.object(worker,'build_opener',side_effect=lambda *a:opener()):worker.verify_image_asset(good)
  with patch.object(worker,'build_opener',side_effect=lambda *a:opener()):
   with self.assertRaises(worker.Stop):worker.verify_image_asset(IMAGE)
if __name__=='__main__':unittest.main()
