import importlib.util,unittest
from unittest.mock import patch
from pathlib import Path
s=importlib.util.spec_from_file_location('read_threads',Path(__file__).resolve().parents[1]/'scripts/read_threads.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
cert='-----BEGIN CERTIFICATE-----\nPUBLIC-TEST-CERT\n-----END CERTIFICATE-----';job={'schema':1,'account':'luen.dayori','operation':'insights_snapshot','post_ids':['123','456'],'result_recipient_certificate':cert}
class Tests(unittest.TestCase):
 def test_parameters(self):
  self.assertEqual(m.parameters(job)['post_ids'],['123','456'])
  for change in [{'post_ids':['123','123']},{'post_ids':['abc']},{'post_ids':[]},{'post_ids':['123']*9},{'result_recipient_certificate':None},{'operation':'insights_snapshot','post_id':'123'}]:
   with self.assertRaises(m.ReadError):m.parameters({**job,**change})
 def test_existing_own_operations_only(self):
  original=m.process;calls=[]
  def fake(j):
   calls.append(j);return {'state':'read_complete','operation':j['operation'],'data':[]}
  with patch.object(m,'process',side_effect=fake):r=original(job)
  self.assertEqual([j['operation'] for j in calls],['post_insights','post_insights','account_insights']);self.assertEqual(r['state'],'read_complete')
 def test_stops_partial(self):
  original=m.process
  with patch.object(m,'process',return_value={'state':'blocked'}) as f:r=original(job)
  self.assertEqual(f.call_count,1);self.assertEqual(r['state'],'blocked');self.assertEqual(len(r['post_results']),1)
unittest.main()
