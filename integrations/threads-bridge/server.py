"""Threads bridge: authenticated preview and explicit-confirmation text publishing.
No scheduling; no automatic posting; publishing is disabled unless configured.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

API = 'https://graph.threads.net/v1.0'
ROOT = Path(__file__).resolve().parent


class Problem(Exception):
    def __init__(self, status, code, **details):
        self.status, self.body = status, {'error': code, **details}
        super().__init__(code)


def bridge_key():
    return os.environ.get('BRIDGE_API_KEY', '').strip()


def publishing_enabled():
    return os.environ.get('PUBLISH_ENABLED', '').lower() == 'true'


def database():
    path = os.environ.get('PUBLISH_STATE_PATH', '/tmp/threads-publications.sqlite3')
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS publications (
        digest TEXT PRIMARY KEY, account_id TEXT NOT NULL, ticket_id TEXT NOT NULL,
        state TEXT NOT NULL, container_id TEXT, post_id TEXT, permalink TEXT,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)''')
    conn.commit()
    return conn


def meta(path, form=None):
    token = os.environ.get('THREADS_ACCESS_TOKEN', '').strip()
    if not token:
        raise Problem(503, 'threads_token_not_configured')
    data = urlencode(form).encode() if form is not None else None
    req = Request(API + path, data=data,
                  headers={'Authorization': 'Bearer ' + token,
                           'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urlopen(req, timeout=20) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise Problem(502, 'unexpected_meta_response')
        return result
    except HTTPError as exc:
        # Never echo raw upstream errors, request URLs or credentials.
        details = {'upstream_status': exc.code}
        try:
            error = json.loads(exc.read(32768)).get('error', {})
            for source, target in [('code', 'upstream_code'), ('error_subcode', 'upstream_subcode')]:
                if type(error.get(source)) is int:
                    details[target] = error[source]
        except (ValueError, AttributeError, OSError):
            pass
        raise Problem(502, 'meta_request_failed', **details) from None
    except (URLError, TimeoutError, ValueError):
        raise Problem(502, 'meta_connection_failed') from None


def account():
    data = meta('/me?fields=id,username')
    if not str(data.get('id', '')).isdigit() or not isinstance(data.get('username'), str):
        raise Problem(502, 'unexpected_meta_account')
    return {'id': str(data['id']), 'username': data['username'],
            'publishing_enabled': publishing_enabled()}


def check_account():
    data = account()
    expected = os.environ.get('THREADS_EXPECTED_USERNAME', '').strip().lstrip('@')
    if not expected:
        raise Problem(503, 'expected_username_not_configured')
    if data['username'].casefold() != expected.casefold():
        raise Problem(409, 'account_mismatch')
    return data


def validate_text(text, limit=500):
    if not isinstance(text, str) or not text.strip():
        raise Problem(400, 'text_required')
    text = text.strip()
    try:
        units = len(text.encode('utf-16-le')) // 2
    except UnicodeEncodeError:
        raise Problem(400, 'invalid_text') from None
    if units > limit:
        raise Problem(400, 'text_too_long', counted_units=units, limit=limit)
    return text, units


def signed_ticket(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False,
        separators=(',', ':')).encode()).decode()
    signature = hmac.new(bridge_key().encode(), body.encode(), hashlib.sha256).hexdigest()
    return body + '.' + signature


def read_ticket(ticket):
    if not isinstance(ticket, str) or len(ticket) > 200000:
        raise Problem(400, 'invalid_preview_ticket')
    try:
        body, signature = ticket.rsplit('.', 1)
        expected = hmac.new(bridge_key().encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(body))
        if not isinstance(payload, dict) or not isinstance(payload.get('expires'), int):
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise Problem(400, 'invalid_preview_ticket') from None
    if payload['expires'] < int(time.time()):
        raise Problem(409, 'preview_expired')
    return payload


def reply_target(value):
    if value is not None and (not isinstance(value, str) or not value.isdigit()):
        raise Problem(400, "invalid_reply_target")
    return value


READ_FIELDS = ('id', 'text', 'username', 'permalink', 'timestamp', 'media_type')


def read_threads(params):
    """Bounded GET-only discovery. Never follow upstream paging URLs containing tokens."""
    if not isinstance(params, dict) or any(not isinstance(v, str) for v in params.values()):
        raise Problem(400, 'invalid_read_parameters')
    operation = params.get('operation')
    allowed = {'search': {'operation', 'q', 'limit', 'search_type'},
               'post': {'operation', 'post_id'},
               'replies': {'operation', 'post_id', 'limit'},
               'own_posts': {'operation', 'limit'}}
    if operation not in allowed or set(params) - allowed[operation]:
        raise Problem(400, 'invalid_read_operation')
    count = params.get('limit', '5')
    if len(count) > 2 or not count.isascii() or not count.isdigit() or not 1 <= int(count) <= 20:
        raise Problem(400, 'invalid_read_limit')
    query = {'fields': ','.join(READ_FIELDS)}
    if operation != 'post':
        query['limit'] = int(count)
    if operation == 'search':
        term = params.get('q', '')
        ordering = params.get('search_type', 'RECENT')
        if not term.strip() or len(term) > 80 or any(ord(c) < 32 for c in term):
            raise Problem(400, 'invalid_search_query')
        if ordering not in ('TOP', 'RECENT'):
            raise Problem(400, 'invalid_search_type')
        query.update(q=term, search_type=ordering)
        endpoint = '/keyword_search'
    elif operation == 'own_posts':
        endpoint = '/me/threads'
    else:
        post_id = reply_target(params.get('post_id'))
        if post_id is None or not post_id.isascii() or len(post_id) > 40:
            raise Problem(400, 'invalid_read_post_id')
        endpoint = '/' + post_id + ('/replies' if operation == 'replies' else '')
    user = check_account()
    result = meta(endpoint + '?' + urlencode(query))
    rows = [result] if operation == 'post' else result.get('data')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise Problem(502, 'unexpected_meta_read_response')
    data = []
    for row in rows[:int(count)]:
        item = {key: row[key] for key in READ_FIELDS if isinstance(row.get(key), str)}
        if not item.get('id', '').isascii() or not item.get('id', '').isdigit():
            raise Problem(502, 'unexpected_meta_post_id')
        try:
            link = urlsplit(item.get('permalink', ''))
            valid_link = link.scheme == 'https' and link.hostname in ('threads.com', 'www.threads.com', 'threads.net', 'www.threads.net') and not link.username and not link.password
        except ValueError:
            valid_link = False
        if not valid_link:
            item.pop('permalink', None)
        data.append(item)
    return {'account': user, 'operation': operation, 'data': data,
            'has_more': bool(result.get('paging', {}).get('next')) if isinstance(result.get('paging'), dict) else False,
            'public_discovery_verified': operation == 'search' and any(
                item.get('username') and item['username'].casefold() != user['username'].casefold()
                for item in data)}


def explicit_repost(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'original_post_id', 'original_deployment_id', 'authorization_ref', 'user_confirmed_deleted'}
            or value['user_confirmed_deleted'] is not True
            or type(value['original_deployment_id']) is not int or value['original_deployment_id'] <= 0
            or not isinstance(value['original_post_id'], str) or not re.fullmatch(r'[0-9]{1,40}', value['original_post_id'])
            or not isinstance(value['authorization_ref'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value['authorization_ref'])):
        raise Problem(400, 'invalid_explicit_repost_authorization')
    return value


def preview(body):
    text, units = validate_text(body.get('text'))
    attachment = body.get('attachment', '')
    if not isinstance(attachment, str):
        raise Problem(400, 'invalid_attachment')
    attachment = validate_text(attachment, limit=10000)[0] if attachment.strip() else ''
    target = reply_target(body.get('reply_to_id'))
    repost = explicit_repost(body.get('repost'))
    if repost is not None and target is not None:
        raise Problem(400, 'repost_only_for_new_main_post')
    user = check_account()
    payload = {'account_id': user['id'], 'username': user['username'], 'text': text,
               'reply_to_id': target, 'attachment': attachment, 'expires': int(time.time()) + 600, 'ticket_id': secrets.token_hex(16)}
    if repost is not None:
        payload['repost'] = repost
    return {'account': user, 'text': text, 'attachment': attachment, 'reply_to_id': target, 'repost': repost, 'counted_units': units,
            'ticket': signed_ticket(payload), 'expires_in_seconds': 600}


def update_record(digest, **fields):
    allowed = {'state', 'container_id', 'post_id', 'permalink'}
    assert fields and set(fields).issubset(allowed)
    fields['updated_at'] = int(time.time())
    conn = database()
    try:
        conn.execute('UPDATE publications SET ' + ', '.join(k + '=?' for k in fields)
                     + ' WHERE digest=?', [*fields.values(), digest])
        conn.commit()
    finally:
        conn.close()


def receipt(row):
    return {k: row[k] for k in ('digest', 'account_id', 'state', 'container_id',
                                'post_id', 'permalink', 'created_at', 'updated_at')}


def publish(body):
    if not publishing_enabled():
        raise Problem(403, 'publishing_disabled')
    if body.get('approved') is not True:
        raise Problem(400, 'explicit_approval_required')
    payload = read_ticket(body.get('ticket'))
    text, _ = validate_text(payload.get('text'))
    attachment = payload.get('attachment', '')
    if not isinstance(attachment, str):
        raise Problem(400, 'invalid_attachment')
    attachment = validate_text(attachment, limit=10000)[0] if attachment.strip() else ''
    user = check_account()
    if payload.get('account_id') != user['id'] or payload.get('username') != user['username']:
        raise Problem(409, 'account_changed_since_preview')
    target = reply_target(payload.get('reply_to_id'))
    repost = explicit_repost(payload.get('repost'))
    if repost is not None and target is not None:
        raise Problem(400, 'repost_only_for_new_main_post')
    digest = hashlib.sha256((user['id'] + '\0' + text + ('\0attachment\0' + attachment if attachment else '') + ('\0reply\0' + target if target else '') + ('\0explicit-repost\0' + json.dumps(repost, sort_keys=True, separators=(',', ':')) if repost is not None else '')).encode()).hexdigest()
    conn = database()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM publications WHERE digest=?', (digest,)).fetchone()
        if row:
            if row['state'] == 'published':
                return {'already_processed': True, **receipt(row)}
            raise Problem(409, 'already_submitted_check_threads_first', **receipt(row))
        now = int(time.time())
        conn.execute('INSERT INTO publications (digest,account_id,ticket_id,state,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                     (digest, user['id'], payload['ticket_id'], 'creating', now, now))
        conn.commit()
    finally:
        conn.close()
    # From this point any error is retained as uncertain: never automatically retry a post.
    try:
        form = {'media_type': 'TEXT', 'text': text}
        if attachment:
            form['text_attachment'] = json.dumps({'plaintext': attachment}, ensure_ascii=False)
        if target:
            form['reply_to_id'] = target
        container = meta('/' + user['id'] + '/threads', form)
        cid = str(container.get('id', ''))
        if not cid.isdigit():
            raise Problem(502, 'unexpected_container_response')
        update_record(digest, state='processing', container_id=cid)
        for attempt in range(16):
            result = meta('/' + cid + '?fields=status')
            if result.get('status') == 'FINISHED':
                break
            if result.get('status') in ('ERROR', 'EXPIRED', 'PUBLISHED'):
                raise Problem(409, 'container_not_publishable')
            if attempt == 15:
                raise Problem(504, 'container_not_ready_check_before_retry')
            time.sleep(2)
        update_record(digest, state='publishing')
        result = meta('/' + user['id'] + '/threads_publish', {'creation_id': cid})
        post_id = str(result.get('id', ''))
        if not post_id.isdigit():
            raise Problem(502, 'publish_result_uncertain')
        update_record(digest, state='published', post_id=post_id)
    except Exception as exc:
        update_record(digest, state='uncertain')
        if isinstance(exc, Problem):
            exc.body.update(digest=digest, action='check_threads_before_any_retry')
            raise
        raise Problem(502, 'publish_result_uncertain', digest=digest,
                      action='check_threads_before_any_retry') from None
    link = None
    try:
        link = meta('/' + post_id + '?fields=permalink').get('permalink')
        parsed = urlsplit(link or '')
        if parsed.scheme != 'https' or parsed.hostname not in ('www.threads.net', 'threads.net', 'www.threads.com', 'threads.com'):
            link = None
        update_record(digest, permalink=link)
    except Problem:
        pass  # A permalink lookup failure must never trigger publishing again.
    return {'state': 'published', 'post_id': post_id, 'permalink': link,
            'digest': digest, 'account_id': user['id']}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, data, content_type='application/json; charset=utf-8', nonce=None):
        payload = (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        if nonce:
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'nonce-" + nonce + "'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self):
        key = bridge_key()
        if not key:
            raise Problem(503, 'bridge_key_not_configured')
        if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + key).encode()):
            raise Problem(401, 'unauthorized')

    def do_GET(self):
        try:
            if self.path == '/health':
                return self.respond(200, {'ok': True, 'version': 'explicit-repost-v5'})
            if self.path == '/':
                nonce = secrets.token_urlsafe(24)
                page = (ROOT / 'publisher.html').read_text().replace('__NONCE__', nonce)
                return self.respond(200, page, 'text/html; charset=utf-8', nonce)
            self.authorized()
            parsed = urlsplit(self.path)
            if parsed.path == '/read':
                params = parse_qs(parsed.query, keep_blank_values=True)
                if any(len(values) != 1 for values in params.values()):
                    raise Problem(400, 'duplicate_read_parameter')
                return self.respond(200, read_threads({k: v[0] for k, v in params.items()}))
            if self.path == '/account':
                return self.respond(200, account())
            if self.path == '/history':
                conn = database()
                try:
                    rows = conn.execute('SELECT * FROM publications ORDER BY created_at DESC LIMIT 30').fetchall()
                    return self.respond(200, {'publications': [receipt(row) for row in rows],
                        'warning': 'Local ledger may reset on free-host restart or redeploy; verify Threads before resubmitting.'})
                finally:
                    conn.close()
            self.respond(404, {'error': 'not_found'})
        except Problem as exc:
            self.respond(exc.status, exc.body)

    def do_POST(self):
        try:
            self.authorized()
            origin = os.environ.get('PUBLIC_ORIGIN', '').strip().rstrip('/')
            if not origin or self.headers.get('Origin') != origin:
                raise Problem(403, 'origin_not_allowed')
            if self.path not in ('/preview', '/publish'):
                raise Problem(404, 'not_found')
            if self.headers.get('Transfer-Encoding'):
                raise Problem(400, 'unsupported_body')
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise Problem(400, 'invalid_body_length') from None
            if length < 1 or length > 262144:
                raise Problem(413, 'invalid_body_size')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise Problem(415, 'json_required')
            try:
                body = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                raise Problem(400, 'invalid_json') from None
            if not isinstance(body, dict):
                raise Problem(400, 'json_object_required')
            self.respond(200, preview(body) if self.path == '/preview' else publish(body))
        except Problem as exc:
            self.respond(exc.status, exc.body)
        except (OSError, sqlite3.Error):
            self.respond(503, {'error': 'local_storage_error_check_threads_before_retry'})


if __name__ == '__main__':
    if not bridge_key():
        raise SystemExit('BRIDGE_API_KEY must be configured')
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8000'))), Handler).serve_forever()
