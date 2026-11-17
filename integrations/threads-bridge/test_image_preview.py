import importlib.util,sys,unittest
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'scripts'))
import verify_threads_image_preview as preview
from test_image_publish import IMAGE
class PreviewTests(unittest.TestCase):
 def job(self):return {'schema':1,'account':'luen.dayori','text':'approved caption',**IMAGE}
 def test_only_account_and_preview_called_and_no_ticket_leak(self):
  calls=[]
  def fake(path,secret,body=None):
   calls.append(path)
   if path=='/account':return {'id':'123','username':'luen.dayori','publishing_enabled':True}
   if path=='/preview':return {**body,'account':{'id':'123','username':'luen.dayori'},'ticket':'DO_NOT_PRINT'}
   self.fail('Unexpected endpoint: '+path)
  with patch.dict(preview.worker.os.environ,{'THREADS_BRIDGE_API_KEY':'fake'}),patch.object(preview.worker,'verify_image_asset'),patch.object(preview.worker,'bridge',side_effect=fake):r=preview.verify(self.job())
  self.assertEqual(calls,['/account','/preview']);self.assertEqual(r['state'],'image_preview_verified');self.assertNotIn('DO_NOT_PRINT',str(r));self.assertFalse(r['publication_sent'])
 def test_old_bridge_cannot_verify_image(self):
  def fake(path,secret,body=None):
   if path=='/account':return {'id':'123','username':'luen.dayori','publishing_enabled':True}
   return {'text':body['text'],'attachment':'','account':{'id':'123','username':'luen.dayori'},'ticket':'private'}
  with patch.dict(preview.worker.os.environ,{'THREADS_BRIDGE_API_KEY':'fake'}),patch.object(preview.worker,'verify_image_asset'),patch.object(preview.worker,'bridge',side_effect=fake):
   with self.assertRaises(preview.worker.Stop):preview.verify(self.job())
if __name__=='__main__':unittest.main()
