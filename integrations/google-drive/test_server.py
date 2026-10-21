import json
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock
from cryptography.fernet import Fernet
from server import create_app, SCOPE, FOLDER_MIME


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(PUBLIC_ORIGIN='https://bridge.example', DRIVE_BRIDGE_API_KEY='k'*40,
                        GOOGLE_CLIENT_ID='test-client', GOOGLE_CLIENT_SECRET='test-secret',
                        GOOGLE_FOLDER_ID='allowed-folder', GOOGLE_TOKEN_KEY=Fernet.generate_key().decode())
        self.http = Mock()
        self.app = create_app(self.cfg, self.http)
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer ' + 'k'*40, 'Origin': 'https://bridge.example'}

    def begin(self):
        r = self.client.post('/connect', headers=self.headers, base_url=self.cfg['PUBLIC_ORIGIN'])
        self.assertEqual(r.status_code, 200)
        params = parse_qs(urlsplit(r.json['url']).query)
        self.assertIn('code_challenge', params)
        self.assertEqual(params['redirect_uri'], ['https://bridge.example/oauth/callback'])
        return params['state'][0]

    def test_no_key_cannot_start_or_list(self):
        self.assertEqual(self.client.post('/connect').status_code, 401)
        self.assertEqual(self.client.get('/folder').status_code, 401)
        self.http.post.assert_not_called()

    def test_cross_origin_rejected(self):
        self.headers['Origin'] = 'https://attacker.example'
        self.assertEqual(self.client.post('/connect', headers=self.headers).status_code, 403)

    def test_callback_needs_browser_cookie(self):
        state = self.begin()
        stranger = self.app.test_client()
        self.assertEqual(stranger.get('/oauth/callback?state='+state+'&code=fake').status_code, 400)
        self.http.post.assert_not_called()

    def test_login_single_use_and_encrypted_export(self):
        state = self.begin()
        self.http.post.return_value.json.return_value = {'scope': SCOPE, 'refresh_token':'SECRET-REFRESH'}
        path = '/oauth/callback?state='+state+'&code=test'
        r = self.client.get(path, base_url=self.cfg['PUBLIC_ORIGIN'])
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b'SECRET-REFRESH', r.data)
        encrypted = r.data.decode().split('cols="80">')[1].split('</textarea>')[0]
        restored = json.loads(Fernet(self.cfg['GOOGLE_TOKEN_KEY'].encode()).decrypt(encrypted.encode()))
        self.assertEqual(restored['refresh_token'], 'SECRET-REFRESH')
        self.assertEqual(self.client.get(path, base_url=self.cfg['PUBLIC_ORIGIN']).status_code, 400)
        self.assertEqual(self.http.post.call_count, 1)

    def test_bundle_wrong_folder_rejected(self):
        bundle = {'client_id':'test-client','folder_id':'different','refresh_token':'secret'}
        self.cfg['GOOGLE_TOKEN_BUNDLE'] = Fernet(self.cfg['GOOGLE_TOKEN_KEY'].encode()).encrypt(json.dumps(bundle).encode()).decode()
        with self.assertRaises(ValueError):
            create_app(self.cfg, self.http)

    def test_restore_and_only_configured_folder_paginated(self):
        bundle = {'client_id':'test-client','folder_id':'allowed-folder','refresh_token':'secret'}
        self.cfg['GOOGLE_TOKEN_BUNDLE'] = Fernet(self.cfg['GOOGLE_TOKEN_KEY'].encode()).encrypt(json.dumps(bundle).encode()).decode()
        client = create_app(self.cfg, self.http).test_client()
        self.http.post.return_value.json.return_value = {'access_token':'SECRET-ACCESS'}
        self.http.get.side_effect = [Mock(json=lambda:{'id':'allowed-folder','name':'luen.dayori','mimeType':FOLDER_MIME}),
                                    Mock(json=lambda:{'files':[{'name':'A'}],'nextPageToken':'next'}),
                                    Mock(json=lambda:{'files':[{'name':'B'}]})]
        r = client.get('/folder?folder_id=forbidden', headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([f['name'] for f in r.json['files']], ['A','B'])
        self.assertTrue(r.json['complete'])
        self.assertIn('allowed-folder', self.http.get.call_args_list[0].args[0])
        self.assertNotIn(b'SECRET-ACCESS', r.data)

    def test_missing_scope_rejected(self):
        state = self.begin()
        self.http.post.return_value.json.return_value = {'scope':'email','refresh_token':'secret'}
        self.assertEqual(self.client.get('/oauth/callback?state='+state+'&code=x', base_url=self.cfg['PUBLIC_ORIGIN']).status_code, 400)


if __name__ == '__main__':
    unittest.main()
