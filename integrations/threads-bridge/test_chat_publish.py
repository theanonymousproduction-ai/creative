"""Offline checks for Chat approval handoff; no GitHub or Threads calls."""
import importlib.util
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('chat_publish', root / 'scripts/publish_chat_approved.py')
worker = importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


class ChatPublishTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'THREADS_BRIDGE_API_KEY': 'fake-bridge-key',
             'GH_TOKEN': 'fake-runner-token', 'GITHUB_REPOSITORY': 'example/creative',
             'GITHUB_SHA': 'a' * 40})
        self.env.start()
        self.claims, self.calls = [], []
        self.bp = patch.object(worker, 'bridge', side_effect=self.bridge)
        self.gp = patch.object(worker, 'github', side_effect=self.github)
        self.bp.start();self.gp.start()

    def tearDown(self):
        self.bp.stop();self.gp.stop();self.env.stop()

    def job(self, attachment=''):
        job = {'schema': 1, 'approved': True, 'account': worker.EXPECTED_ACCOUNT,
               'approval_ref': 'C006-v1', 'approved_at': int(time.time()),
               'text': '已批准主帖', 'attachment': attachment}
        job['sha256'] = worker.fingerprint(job['account'], job['text'], attachment)
        return job

    def bridge(self, path, secret, body=None):
        self.calls.append((path, body))
        if path == '/account': return {'id': '123', 'username': worker.EXPECTED_ACCOUNT, 'publishing_enabled': True}
        if path == '/preview': return {'text': body['text'], 'attachment': body['attachment'],
            'account': {'id': '123', 'username': worker.EXPECTED_ACCOUNT}, 'ticket': 'fake-preview-ticket'}
        if path == '/publish': return {'state': 'published', 'post_id': '789',
                                      'permalink': 'https://www.threads.com/@example/post/test'}
        raise AssertionError(path)

    def github(self, path, token, body=None):
        if path.startswith('/deployments?'): return self.claims
        if path == '/deployments' and body is not None:
            self.claims.append({'id': 1});return {'id': 1}
        if path == '/deployments/1/statuses': return {'id': 2}
        raise AssertionError(path)

    def test_requires_fresh_exact_explicit_approval(self):
        for update in [{'approved': False}, {'sha256': 'bad'}, {'approved_at': 1},
                       {'account': 'other'}, {'approval_ref': '../bad'}]:
            job = self.job();job.update(update)
            with self.assertRaises(worker.Stop): worker.process(job)
        self.assertEqual(self.calls, [])

    def test_disabled_host_prevents_any_publish_or_claim(self):
        with patch.object(worker, 'bridge', return_value={'username':worker.EXPECTED_ACCOUNT, 'publishing_enabled':False}):
            with self.assertRaises(worker.Stop): worker.process(self.job())
        self.assertEqual(self.claims, [])

    def test_preview_cannot_change_approved_full_article(self):
        original = self.bridge
        def changed(path, secret, body=None):
            value = original(path, secret, body)
            if path == '/preview': value['attachment'] = 'changed'
            return value
        with patch.object(worker, 'bridge', side_effect=changed):
            with self.assertRaises(worker.Stop): worker.process(self.job('全文' * 300))
        self.assertEqual(self.claims, [])
        self.assertFalse(any(p == '/publish' for p, _ in self.calls))

    def test_success_persists_attempt_then_blocks_duplicate(self):
        result = worker.process(self.job('全文' * 300))
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['post_id'], '789')
        with self.assertRaises(worker.Stop): worker.process(self.job('全文' * 300))
        self.assertEqual(sum(p == '/publish' for p, _ in self.calls), 1)
        self.assertNotIn('fake-bridge-key', str(result))
        self.assertNotIn('fake-preview-ticket', str(result))

    def test_ambiguous_result_keeps_durable_claim_after_host_restart(self):
        original = self.bridge
        def timeout(path, secret, body=None):
            if path == '/publish':
                self.calls.append((path, body));raise worker.Stop('network_result_unknown_check_before_retry')
            return original(path, secret, body)
        with patch.object(worker, 'bridge', side_effect=timeout):
            result = worker.process(self.job())
        self.assertEqual(result['state'], 'uncertain')
        with self.assertRaises(worker.Stop): worker.process(self.job())
        self.assertEqual(sum(p == '/publish' for p, _ in self.calls), 1)

    def test_receipt_reporting_error_does_not_retry_publishing(self):
        original = self.github
        def failed_status(path, token, body=None):
            if path.endswith('/statuses'): raise worker.Stop('report_unavailable')
            return original(path, token, body)
        with patch.object(worker, 'github', side_effect=failed_status): result=worker.process(self.job())
        self.assertEqual(result['state'], 'published')
        self.assertIn('reporting_warning', result)
        self.assertEqual(sum(p == '/publish' for p, _ in self.calls), 1)

if __name__ == '__main__': unittest.main()
