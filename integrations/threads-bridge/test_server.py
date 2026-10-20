"""Offline tests: Meta is mocked; no real account or post is touched."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).with_name('server.py'))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'BRIDGE_API_KEY': 'offline-test-key-not-a-real-secret',
            'THREADS_ACCESS_TOKEN': 'offline-test-token-never-transmitted',
            'THREADS_EXPECTED_USERNAME': 'example_tester',
            'PUBLIC_ORIGIN': 'https://example.invalid',
            'PUBLISH_ENABLED': 'true',
            'PUBLISH_STATE_PATH': str(Path(self.temp.name) / 'state.sqlite3')})
        self.env.start()
        self.calls = []
        self.real_meta = bridge.meta
        self.meta_patch = patch.object(bridge, 'meta', side_effect=self.fake_meta)
        self.meta_patch.start()

    def tearDown(self):
        self.meta_patch.stop()
        self.env.stop()
        self.temp.cleanup()

    def fake_meta(self, path, form=None):
        self.calls.append((path, form))
        if path.startswith('/me?'): return {'id': '123', 'username': 'example_tester'}
        if path == '/123/threads': return {'id': '456'}
        if path == '/456?fields=status': return {'status': 'FINISHED'}
        if path == '/123/threads_publish': return {'id': '789'}
        if path == '/789?fields=permalink': return {'permalink': 'https://www.threads.com/@example_tester/post/example'}
        raise AssertionError(path)

    def make_ticket(self, text='測試文字，這不是實際貼文。'):
        return bridge.preview({'text': text})['ticket']

    def submit(self, ticket):
        return bridge.publish({'ticket': ticket, 'approved': True})

    def assert_problem(self, status, callback):
        with self.assertRaises(bridge.Problem) as ctx: callback()
        self.assertEqual(ctx.exception.status, status)
        return ctx.exception.body

    def test_preview_has_exact_text_and_never_creates_container(self):
        result = bridge.preview({'text': '  第一行\n第二行  '})
        self.assertEqual(result['text'], '第一行\n第二行')
        self.assertEqual(bridge.read_ticket(result['ticket'])['text'], result['text'])
        self.assertTrue(all(form is None for _, form in self.calls))

    def test_text_limit_and_empty(self):
        self.assert_problem(400, lambda: self.make_ticket('字' * 501))
        self.assert_problem(400, lambda: self.make_ticket(' '))
        self.assert_problem(400, lambda: self.make_ticket('😀' * 251))
        self.assertEqual(self.calls, [])

    def test_disabled_is_default_and_blocks_everything(self):
        os.environ.pop('PUBLISH_ENABLED')
        self.assertFalse(bridge.publishing_enabled())
        self.assert_problem(403, lambda: bridge.publish({'approved': True}))
        self.assertEqual(self.calls, [])

    def test_missing_explicit_approval(self):
        self.assert_problem(400, lambda: bridge.publish({'ticket': self.make_ticket()}))
        self.assertFalse(any(form for _, form in self.calls))

    def test_modified_and_expired_tickets(self):
        ticket = self.make_ticket()
        bad = ticket[:-1] + ('0' if ticket[-1] != '0' else '1')
        self.assert_problem(400, lambda: self.submit(bad))
        payload = bridge.read_ticket(ticket);payload['expires'] = int(time.time()) - 1
        self.assert_problem(409, lambda: self.submit(bridge.signed_ticket(payload)))
        self.assertFalse(any(form for _, form in self.calls))

    def test_account_lock_and_change_after_preview(self):
        os.environ['THREADS_EXPECTED_USERNAME'] = 'different_account'
        self.assert_problem(409, self.make_ticket)
        os.environ['THREADS_EXPECTED_USERNAME'] = 'example_tester'
        ticket = self.make_ticket()
        with patch.object(bridge, 'check_account', return_value={'id': '999', 'username': 'example_tester'}):
            self.assert_problem(409, lambda: self.submit(ticket))
        self.assertFalse(any(form for _, form in self.calls))

    def test_success_and_same_text_duplicate_after_new_preview(self):
        first = self.submit(self.make_ticket())
        self.assertEqual(first['state'], 'published')
        second = self.submit(self.make_ticket())
        self.assertTrue(second['already_processed'])
        self.assertEqual(sum(path == '/123/threads_publish' for path, _ in self.calls), 1)
        self.assertEqual(sum(path == '/123/threads' for path, _ in self.calls), 1)

    def test_concurrent_double_submit_only_one_can_publish(self):
        ticket = self.make_ticket()
        def worker(_):
            try: return self.submit(ticket)
            except bridge.Problem as exc: return exc.body
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(worker, range(8)))
        self.assertTrue(any(r.get('state') == 'published' for r in results))
        self.assertEqual(sum(path == '/123/threads_publish' for path, _ in self.calls), 1)

    def test_uncertain_publish_never_automatically_retried(self):
        original = self.fake_meta
        def failing(path, form=None):
            if path == '/123/threads_publish':
                self.calls.append((path, form))
                raise bridge.Problem(502, 'meta_connection_failed')
            return original(path, form)
        with patch.object(bridge, 'meta', side_effect=failing):
            self.assert_problem(502, lambda: self.submit(self.make_ticket()))
            body = self.assert_problem(409, lambda: self.submit(self.make_ticket()))
        self.assertEqual(body['state'], 'uncertain')
        self.assertEqual(sum(path == '/123/threads_publish' for path, _ in self.calls), 1)

    def test_container_error_never_publishes(self):
        original = self.fake_meta
        def failed_container(path, form=None):
            if path == '/456?fields=status': return {'status': 'ERROR'}
            return original(path, form)
        with patch.object(bridge, 'meta', side_effect=failed_container):
            self.assert_problem(409, lambda: self.submit(self.make_ticket()))
        self.assertFalse(any(p == '/123/threads_publish' for p, _ in self.calls))

    def test_permalink_failure_does_not_publish_again(self):
        original = self.fake_meta
        def missing_link(path, form=None):
            if path == '/789?fields=permalink': raise bridge.Problem(502, 'meta_connection_failed')
            return original(path, form)
        with patch.object(bridge, 'meta', side_effect=missing_link):
            first = self.submit(self.make_ticket())
            second = self.submit(self.make_ticket())
        self.assertEqual(first['state'], 'published')
        self.assertIsNone(first['permalink'])
        self.assertTrue(second['already_processed'])
        self.assertEqual(sum(p == '/123/threads_publish' for p, _ in self.calls), 1)

    def test_long_attachment_is_bound_to_preview_and_meta_form(self):
        article = '完整故事' * 150
        data = bridge.preview({'text': '故事標題', 'attachment': article})
        self.assertEqual(data['attachment'], article)
        self.assertEqual(bridge.read_ticket(data['ticket'])['attachment'], article)
        result = self.submit(data['ticket'])
        self.assertEqual(result['state'], 'published')
        form = next(form for path, form in self.calls if path == '/123/threads')
        self.assertEqual(json.loads(form['text_attachment'])['plaintext'], article)
        self.assertEqual(form['text'], '故事標題')
        self.assert_problem(400, lambda: bridge.preview({'text':'標題', 'attachment':'字' * 10001}))

    def test_http_auth_origin_ui_and_preview(self):
        server = bridge.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        def request(path, body=None, authorized=False, origin=None):
            headers = {}
            if authorized: headers['Authorization'] = 'Bearer ' + bridge.bridge_key()
            if origin: headers['Origin'] = origin
            if body is not None: headers['Content-Type'] = 'application/json'
            req = Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
            try:
                with urlopen(req, timeout=3) as response: return response.status, response.headers, response.read().decode()
            except HTTPError as exc: return exc.code, exc.headers, exc.read().decode()
        try:
            self.assertEqual(request('/health')[0], 200)
            code, headers, page = request('/')
            self.assertEqual(code, 200)
            self.assertIn('Content-Security-Policy', headers)
            self.assertNotIn('__NONCE__', page)
            self.assertNotIn(os.environ['THREADS_ACCESS_TOKEN'], page)
            self.assertNotIn(bridge.bridge_key(), page)
            self.assertEqual(request('/account')[0], 401)
            self.assertEqual(request('/preview', {'text': '測試'})[0], 401)
            self.assertEqual(request('/preview', {'text': '測試'}, True, 'https://wrong.invalid')[0], 403)
            self.assertEqual(request('/preview', {'text': '測試'}, True, 'https://example.invalid')[0], 200)
            self.assertFalse(any(form for _, form in self.calls))
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)

    def test_upstream_http_error_does_not_echo_token_or_raw_body(self):
        real_meta = self.real_meta
        with patch.object(bridge, 'urlopen', side_effect=HTTPError('https://example.invalid', 401, 'secret raw error', {}, None)):
            with self.assertRaises(bridge.Problem) as ctx: real_meta('/me?fields=id,username')
        body = json.dumps(ctx.exception.body)
        self.assertNotIn(os.environ['THREADS_ACCESS_TOKEN'], body)
        self.assertNotIn('secret raw error', body)


if __name__ == '__main__': unittest.main()
