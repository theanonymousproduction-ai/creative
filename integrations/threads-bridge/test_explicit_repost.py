"""Explicit deletion/repost intent stays separate from automatic retry; offline only."""
import hashlib
import json
from unittest.mock import patch
from test_chat_publish import ChatPublishTests, worker
from test_server import BridgeTests, bridge

REPOST = {'original_post_id': '789', 'original_deployment_id': 10,
          'authorization_ref': 'C016-v4-repost-1', 'user_confirmed_deleted': True}

class RunnerRepostTests(ChatPublishTests):
    def repost_job(self):
        j=self.job();j['repost']=dict(REPOST)
        j['sha256']=worker.fingerprint(j['account'], j['text'], '', repost=j['repost'])
        return j

    def bridge(self, path, secret, body=None):
        r=super().bridge(path,secret,body)
        if path=='/preview':r['repost']=body.get('repost')
        return r

    def github(self,path,token,body=None):
        if path=='/deployments/10':
            j=self.job();return {'environment':'threads-'+j['sha256'],'payload':{'sha256':j['sha256'],'account':worker.EXPECTED_ACCOUNT}}
        if path=='/deployments/10/statuses?per_page=1':return [{'state':'success','description':'Threads post 789'}]
        return super().github(path,token,body)

    def test_explicit_repost_is_distinct_but_same_intent_blocks_repeat(self):
        self.assertNotEqual(self.job()['sha256'], self.repost_job()['sha256'])
        self.assertEqual(worker.process(self.repost_job())['state'],'published')
        with self.assertRaises(worker.Stop):worker.process(self.repost_job())
        self.assertEqual(sum(p=='/publish' for p,_ in self.calls),1)

    def test_wrong_prior_content_blocks_before_preview(self):
        original=self.github
        def bad(path,token,body=None):
            if path=='/deployments/10':return {'payload':{}}
            return original(path,token,body)
        with patch.object(worker,'github',side_effect=bad):
            with self.assertRaises(worker.Stop):worker.process(self.repost_job())
        self.assertEqual(self.claims,[])
        self.assertFalse(any(p=='/preview' for p,_ in self.calls))

    def test_old_host_cannot_silently_reuse_old_id(self):
        original=self.bridge
        def old(path,secret,body=None):
            r=original(path,secret,body)
            r.pop('repost',None)
            return r
        with patch.object(worker,'bridge',side_effect=old):
            with self.assertRaises(worker.Stop):worker.process(self.repost_job())
        self.assertEqual(self.claims,[])

    def test_false_deletion_or_reply_repost_rejected(self):
        j=self.repost_job();j['repost']['user_confirmed_deleted']=False
        with self.assertRaises(worker.Stop):worker.validate(j)
        j=self.repost_job();j['reply_to_id']='1'
        with self.assertRaises(worker.Stop):worker.validate(j)

class HostRepostTests(BridgeTests):
    def test_new_intent_creates_once_and_leaves_original_record(self):
        self.submit(self.make_ticket())
        text='測試文字，這不是實際貼文。'
        preview=bridge.preview({'text':text,'repost':dict(REPOST)})
        self.assertEqual(preview['repost'],REPOST)
        result=self.submit(preview['ticket'])
        self.assertEqual(result['state'],'published')
        self.submit(preview['ticket'])
        self.assertEqual(sum(p=='/123/threads' for p,_ in self.calls),2)
        conn=bridge.database();self.assertEqual(conn.execute('SELECT count(*) FROM publications').fetchone()[0],2);conn.close()

    def test_repost_ticket_is_signed(self):
        ticket=bridge.preview({'text':'重發','repost':dict(REPOST)})['ticket']
        body,sig=ticket.split('.')
        import base64
        p=json.loads(base64.urlsafe_b64decode(body));p['repost']['original_post_id']='999'
        body=base64.urlsafe_b64encode(json.dumps(p).encode()).decode()
        self.assert_problem(400,lambda:self.submit(body+'.'+sig))
