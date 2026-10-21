"""Single-owner Google Drive bridge. No file writes or public token endpoints."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, jsonify, make_response, render_template_string, request

SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
FOLDER_MIME = 'application/vnd.google-apps.folder'


def create_app(config=None, http=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=8192)
    cfg = dict(os.environ) if config is None else dict(config)
    http = http or requests.Session()
    origin = cfg.get('PUBLIC_ORIGIN', '').rstrip('/')
    bridge_key = cfg.get('DRIVE_BRIDGE_API_KEY', '')
    client_id = cfg.get('GOOGLE_CLIENT_ID', '')
    client_secret = cfg.get('GOOGLE_CLIENT_SECRET', '')
    folder_id = cfg.get('GOOGLE_FOLDER_ID', '1_ho3hHWrG8txOcoUi6R34xnKfVVivEIE')
    cipher_key = cfg.get('GOOGLE_TOKEN_KEY', '')
    cipher = Fernet(cipher_key.encode()) if cipher_key else None
    parsed = urlsplit(origin)
    if origin and (parsed.scheme != 'https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username):
        raise ValueError('PUBLIC_ORIGIN must be an HTTPS origin')
    lock = threading.Lock()
    pending = {}
    refresh = [None]
    if cfg.get('GOOGLE_TOKEN_BUNDLE'):
        if cipher is None:
            raise ValueError('GOOGLE_TOKEN_KEY required')
        try:
            bundle = json.loads(cipher.decrypt(cfg['GOOGLE_TOKEN_BUNDLE'].encode()))
            if bundle['client_id'] != client_id or bundle['folder_id'] != folder_id:
                raise ValueError('Token bundle configuration mismatch')
            refresh[0] = bundle['refresh_token']
        except (InvalidToken, KeyError, json.JSONDecodeError) as exc:
            raise ValueError('Invalid encrypted token bundle') from exc

    def configured():
        return bool(origin and len(bridge_key) >= 32 and client_id and client_secret and cipher)

    def authorized():
        return len(bridge_key) >= 32 and hmac.compare_digest(
            request.headers.get('Authorization', ''), 'Bearer ' + bridge_key)

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                                 'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    @app.get('/health')
    def health():
        return jsonify(status='ok', version='drive-readonly-v1')

    @app.get('/')
    def setup():
        return '''<!doctype html><meta charset="utf-8"><title>Google Drive 連接</title>
        <h1>連接 Google Drive</h1><p>首次連接需登入 Google。此版本只讀取資料，不修改文件。</p>
        <p>Google 授權為 Drive 唯讀；服務僅提供指定資料夾第一層清單。</p>
        <label>DRIVE_BRIDGE_API_KEY <input id="key" type="password" autocomplete="off"></label>
        <button id="connect">登入 Google</button><p id="message"></p><script src="/setup.js"></script>'''

    @app.get('/setup.js')
    def javascript():
        return app.response_class('''document.getElementById('connect').onclick = async () => {
          const field = document.getElementById('key'); const key = field.value; field.value = '';
          try { const r = await fetch('/connect', {method:'POST', headers:{Authorization:'Bearer '+key}});
            const data = await r.json(); if (!r.ok) throw new Error(data.error);
            location.assign(data.url);
          } catch(e) { document.getElementById('message').textContent = '未能開始授權，請檢查設定與服務密鑰。'; }
        };''', mimetype='application/javascript')

    @app.post('/connect')
    def connect():
        if not authorized():
            return jsonify(error='unauthorized'), 401
        if request.headers.get('Origin') != origin:
            return jsonify(error='invalid_origin'), 403
        if not configured():
            return jsonify(error='setup_incomplete'), 503
        state, cookie, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with lock:
            now = time.monotonic()
            for old in list(pending):
                if pending[old][0] < now:
                    del pending[old]
            if len(pending) >= 20:
                return jsonify(error='too_many_pending_logins'), 429
            pending[state] = (now + 600, cookie, verifier)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': client_id, 'redirect_uri': origin + '/oauth/callback',
            'response_type': 'code', 'scope': SCOPE, 'access_type': 'offline',
            'prompt': 'consent', 'state': state, 'code_challenge': challenge,
            'code_challenge_method': 'S256'})
        response = jsonify(url=url)
        response.set_cookie('__Host-drive-oauth', cookie, secure=True, httponly=True, samesite='Lax', max_age=600, path='/')
        return response

    @app.get('/oauth/callback')
    def callback():
        state = request.args.get('state', '')
        with lock:
            item = pending.get(state)
            if not item or item[0] < time.monotonic() or not hmac.compare_digest(item[1], request.cookies.get('__Host-drive-oauth', '')):
                return jsonify(error='invalid_or_expired_login'), 400
            del pending[state]  # Single use, including denied/failed exchanges.
        if request.args.get('error') or not request.args.get('code'):
            return jsonify(error='authorization_not_completed'), 400
        try:
            response = http.post('https://oauth2.googleapis.com/token', data={
                'client_id': client_id, 'client_secret': client_secret,
                'redirect_uri': origin + '/oauth/callback', 'grant_type': 'authorization_code',
                'code': request.args['code'], 'code_verifier': item[2]}, timeout=30)
            response.raise_for_status()
            token = response.json()
            granted = set(token.get('scope', '').split())
            if SCOPE not in granted or not token.get('refresh_token'):
                return jsonify(error='required_permission_or_refresh_token_missing'), 400
            encrypted = cipher.encrypt(json.dumps({'client_id': client_id, 'folder_id': folder_id,
                                                   'refresh_token': token['refresh_token']}).encode()).decode()
            refresh[0] = token['refresh_token']
        except (requests.RequestException, ValueError, KeyError):
            return jsonify(error='google_authorization_failed'), 502
        page = render_template_string('''<!doctype html><meta charset="utf-8"><h1>已收到 Google 授權</h1>
        <p>尚未核對資料夾。請把下方加密值儲存到此 Render 服務的 GOOGLE_TOKEN_BUNDLE 環境變數。
        保留 GOOGLE_TOKEN_KEY 不變。儲存後服務重啟仍可連接。不要貼到聊天或 GitHub。</p>
        <textarea readonly rows="8" cols="80">{{ bundle }}</textarea>''', bundle=encrypted)
        result = make_response(page)
        result.delete_cookie('__Host-drive-oauth', secure=True, httponly=True, samesite='Lax', path='/')
        return result

    @app.get('/folder')
    def folder():
        if not authorized():
            return jsonify(error='unauthorized'), 401
        if not refresh[0]:
            return jsonify(error='google_login_required'), 409
        try:
            r = http.post('https://oauth2.googleapis.com/token', data={
                'client_id': client_id, 'client_secret': client_secret,
                'refresh_token': refresh[0], 'grant_type': 'refresh_token'}, timeout=30)
            r.raise_for_status()
            auth = {'Authorization': 'Bearer ' + r.json()['access_token']}
            meta = http.get('https://www.googleapis.com/drive/v3/files/' + requests.utils.quote(folder_id, safe=''),
                            params={'fields': 'id,name,mimeType,trashed'}, headers=auth, timeout=30)
            meta.raise_for_status()
            info = meta.json()
            if info.get('mimeType') != FOLDER_MIME or info.get('trashed'):
                return jsonify(error='configured_folder_unavailable'), 409
            # Never accepts a caller-supplied folder/file ID or arbitrary Google URL.
            params = {'q': "'" + folder_id.replace('\\', '\\\\').replace("'", "\\'") + "' in parents and trashed = false",
                      'fields': 'nextPageToken,files(id,name,mimeType)', 'pageSize': 100}
            files = []
            for _ in range(20):
                listing = http.get('https://www.googleapis.com/drive/v3/files', params=params, headers=auth, timeout=30)
                listing.raise_for_status()
                data = listing.json()
                files.extend(data.get('files', []))
                if not data.get('nextPageToken'):
                    return jsonify(folder=info, files=files, complete=True)
                params['pageToken'] = data['nextPageToken']
            return jsonify(folder=info, files=files, complete=False)
        except (requests.RequestException, ValueError, KeyError):
            return jsonify(error='google_request_failed_check_authorization'), 502

    return app


app = create_app()
