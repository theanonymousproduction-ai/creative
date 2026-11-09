"""Discovery is bounded, account-locked and strictly read-only."""
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


bridge = module('discovery_bridge', Path(__file__).with_name('server.py'))
runner = module('discovery_runner', ROOT / 'scripts/read_threads.py')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'THREADS_EXPECTED_USERNAME': 'luen.dayori',
            'THREADS_ACCESS_TOKEN': 'offline-not-a-real-token', 'THREADS_BRIDGE_API_KEY': 'offline-not-a-real-key'})
        self.env.start()
        self.calls = []

    def tearDown(self):
        self.env.stop()

    def meta(self, path, form=None):
        self.assertIsNone(form, 'Discovery must never write to Meta')
        self.calls.append(path)
        if path.startswith('/me?'):
            return {'id': '123', 'username': 'luen.dayori'}
        return {'data': [{'id': '789', 'username': 'other', 'text': '公開貼文',
            'permalink': 'https://www.threads.com/@other/post/example', 'access_token': 'MUST_NOT_LEAK'}],
            'paging': {'next': 'https://example.invalid/?access_token=MUST_NOT_LEAK'}}

    def test_search_encodes_query_only_uses_get_and_removes_paging_secrets(self):
        with patch.object(bridge, 'meta', side_effect=self.meta):
            data = bridge.read_threads({'operation': 'search', 'q': '戀愛腦&limit=99', 'limit': '5'})
        params = parse_qs(urlsplit(self.calls[-1]).query)
        self.assertEqual(params['q'], ['戀愛腦&limit=99'])
        self.assertEqual(params['limit'], ['5'])
        self.assertTrue(data['public_discovery_verified'])
        self.assertNotIn('MUST_NOT_LEAK', json.dumps(data))

    def test_invalid_operations_and_targets_do_not_call_meta(self):
        for params in [{'operation': 'publish'}, {'operation': 'search', 'q': 'x', 'url': 'https://evil.invalid'},
                       {'operation': 'post', 'post_id': '../me'}, {'operation': 'search', 'q': ''},
                       {'operation': 'search', 'q': 'x', 'limit': '21'}, {'operation': 'search', 'q': 'x', 'search_type': 'BAD'}]:
            with self.subTest(params=params), patch.object(bridge, 'meta') as meta:
                with self.assertRaises(bridge.Problem): bridge.read_threads(params)
                meta.assert_not_called()

    def test_account_mismatch_blocks_discovery(self):
        with patch.dict(os.environ, {'THREADS_EXPECTED_USERNAME': 'different'}), patch.object(bridge, 'meta', side_effect=self.meta):
            with self.assertRaises(bridge.Problem): bridge.read_threads({'operation': 'search', 'q': '戀愛腦'})
        self.assertEqual(len(self.calls), 1)

    def test_empty_or_own_only_results_do_not_prove_public_search(self):
        for rows in [[], [{'id': '789', 'username': 'luen.dayori'}]]:
            with patch.object(bridge, 'check_account', return_value={'id': '123', 'username': 'luen.dayori'}), patch.object(bridge, 'meta', return_value={'data': rows}):
                result = bridge.read_threads({'operation': 'search', 'q': '戀愛腦'})
                self.assertFalse(result['public_discovery_verified'])

    def test_post_replies_and_own_posts_are_allowlisted(self):
        with patch.object(bridge, 'meta', side_effect=self.meta):
            bridge.read_threads({'operation': 'replies', 'post_id': '789'})
            bridge.read_threads({'operation': 'own_posts'})
        self.assertTrue(any(p.startswith('/789/replies?') for p in self.calls))
        self.assertTrue(any(p.startswith('/me/threads?') for p in self.calls))
        with patch.object(bridge, 'check_account', return_value={'username': 'luen.dayori'}), patch.object(bridge, 'meta', return_value={'id': '789', 'text': '正文'}):
            self.assertEqual(bridge.read_threads({'operation': 'post', 'post_id': '789'})['data'][0]['text'], '正文')

    def test_upstream_error_exposes_only_numeric_diagnostics(self):
        raw = {'error': {'message': 'MUST_NOT_LEAK', 'code': 10, 'error_subcode': 33}}
        error = HTTPError('https://example.invalid', 403, 'MUST_NOT_LEAK', {}, io.BytesIO(json.dumps(raw).encode()))
        with patch.object(bridge, 'urlopen', side_effect=error):
            with self.assertRaises(bridge.Problem) as ctx: bridge.meta('/keyword_search?q=x')
        self.assertEqual(ctx.exception.body['upstream_code'], 10)
        self.assertNotIn('MUST_NOT_LEAK', json.dumps(ctx.exception.body))

    def test_runner_cannot_select_write_or_external_host(self):
        for job in [{'schema': 1, 'account': 'luen.dayori', 'operation': 'publish'},
                    {'schema': 1, 'account': 'other', 'operation': 'own_posts'},
                    {'schema': 1, 'account': 'luen.dayori', 'operation': 'search', 'q': 'x', 'url': 'https://evil.invalid'}]:
            with patch.object(runner, 'urlopen') as http:
                with self.assertRaises(runner.ReadError): runner.process(job)
                http.assert_not_called()

    def test_runner_builds_get_only_and_checks_response_account(self):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()
        def get(req, timeout):
            self.assertEqual(req.get_method(), 'GET')
            self.assertEqual(urlsplit(req.full_url).hostname, 'creative-hl3h.onrender.com')
            if urlsplit(req.full_url).path == '/health':
                return Response(json.dumps({'version': 'discovery-read-v4'}).encode())
            return Response(json.dumps({'account': {'username': 'wrong'}, 'data': []}).encode())
        with patch.object(runner, 'urlopen', side_effect=get):
            with self.assertRaises(runner.ReadError): runner.process({'schema': 1, 'account': 'luen.dayori', 'operation': 'own_posts'})

    def test_runner_reports_old_deployment_before_search(self):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()
        with patch.object(runner, 'urlopen', return_value=Response(b'{"version":"chat-replies-v3"}')) as http:
            result = runner.process({'schema': 1, 'account': 'luen.dayori', 'operation': 'own_posts'})
        self.assertEqual(result['error'], 'bridge_upgrade_required')
        self.assertEqual(http.call_count, 1)


if __name__ == '__main__':
    unittest.main()
