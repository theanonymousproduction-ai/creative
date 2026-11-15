"""Read-only Threads discovery runner. Credentials stay in Actions/Render."""
import json
import base64
import hashlib
import subprocess
import tempfile
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BRIDGE = 'https://creative-hl3h.onrender.com'
ACCOUNT = 'luen.dayori'
ROOT = Path(__file__).resolve().parents[1]


class ReadError(Exception):
    pass


def parameters(job):
    allowed = {'schema', 'account', 'operation', 'q', 'post_id', 'post_ids', 'limit', 'search_type', 'result_recipient_certificate'}
    if not isinstance(job, dict) or set(job) - allowed or job.get('schema') != 1 or job.get('account') != ACCOUNT:
        raise ReadError('invalid_read_job')
    operation = job.get('operation')
    cert = job.get('result_recipient_certificate')
    if cert is not None and (not isinstance(cert, str) or len(cert) > 4096 or not cert.startswith('-----BEGIN CERTIFICATE-----') or not cert.strip().endswith('-----END CERTIFICATE-----') or 'PRIVATE KEY' in cert):
        raise ReadError('invalid_result_recipient')
    if operation in ('post_insights', 'account_insights', 'insights_snapshot') and cert is None:
        raise ReadError('encrypted_result_required_for_insights')
    extras = {'search': {'q', 'limit', 'search_type'}, 'post': {'post_id'},
              'replies': {'post_id', 'limit'}, 'own_posts': {'limit'},
              'post_insights': {'post_id'}, 'account_insights': set(), 'insights_snapshot': {'post_ids'}}
    if operation not in extras or set(job) - {'schema', 'account', 'operation', 'result_recipient_certificate'} - extras[operation]:
        raise ReadError('invalid_read_operation')
    limit = job.get('limit', 5)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ReadError('invalid_read_limit')
    result = {'operation': operation}
    if operation not in ('post', 'post_insights', 'account_insights', 'insights_snapshot'):
        result['limit'] = str(limit)
    if operation == 'insights_snapshot':
        ids = job.get('post_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 8 or any(not isinstance(x, str) or not x.isascii() or not x.isdigit() or len(x) > 40 for x in ids) or len(set(ids)) != len(ids):
            raise ReadError('invalid_snapshot_post_ids')
        result['post_ids'] = ids
    elif operation == 'search':
        q = job.get('q')
        ordering = job.get('search_type', 'RECENT')
        if not isinstance(q, str) or not q.strip() or len(q) > 80 or any(ord(c) < 32 for c in q):
            raise ReadError('invalid_search_query')
        if ordering not in ('TOP', 'RECENT'):
            raise ReadError('invalid_search_type')
        result.update(q=q, search_type=ordering)
    elif operation in ('post', 'replies', 'post_insights'):
        target = job.get('post_id')
        if not isinstance(target, str) or not target.isascii() or not target.isdigit() or len(target) > 40:
            raise ReadError('invalid_read_post_id')
        result['post_id'] = target
    return result


def process(job):
    params = parameters(job)
    if params['operation'] == 'insights_snapshot':
        # One Actions job; reuse the bounded existing own-post read operations.
        # Every result is encrypted together by main, including partial failures.
        posts = []
        common = {'schema': 1, 'account': ACCOUNT,
                  'result_recipient_certificate': job['result_recipient_certificate']}
        for post_id in params['post_ids']:
            result = process({**common, 'operation': 'post_insights', 'post_id': post_id})
            posts.append({'post_id': post_id, 'result': result})
            if result.get('state') != 'read_complete':
                return {'state': 'blocked', 'operation': 'insights_snapshot',
                        'post_results': posts, 'error': 'partial_snapshot_stopped'}
        account = process({**common, 'operation': 'account_insights'})
        return {'state': account.get('state', 'blocked'), 'operation': 'insights_snapshot',
                'post_results': posts, 'account_result': account}
    secret = os.environ.get('THREADS_BRIDGE_API_KEY', '').strip()
    if not secret:
        raise ReadError('bridge_secret_missing')
    health = Request(BRIDGE + '/health', headers={'Accept': 'application/json', 'Cache-Control': 'no-cache'})
    try:
        with urlopen(health, timeout=100) as response:
            version = json.load(response).get('version')
        acceptable = ('insights-read-v6',) if params['operation'] in ('post_insights', 'account_insights') else ('discovery-read-v4', 'explicit-repost-v5', 'insights-read-v6')
        if version not in acceptable:
            return {'state': 'blocked', 'error': 'bridge_upgrade_required',
                    'expected_version': acceptable[0],
                    'observed_version': version if version in ('chat-replies-v3', 'preview-publish-v1', 'discovery-read-v4', 'explicit-repost-v5', 'insights-read-v6') else 'unknown'}
    except (HTTPError, URLError, TimeoutError, ValueError, AttributeError):
        return {'state': 'blocked', 'error': 'bridge_health_check_failed'}
    request = Request(BRIDGE + '/read?' + urlencode(params), headers={
        'Authorization': 'Bearer ' + secret, 'Accept': 'application/json',
        'User-Agent': 'luen-dayori-read-only'})
    try:
        with urlopen(request, timeout=100) as response:
            data = json.load(response)
    except HTTPError as exc:
        safe = {'error': 'bridge_request_rejected', 'http_status': exc.code}
        try:
            body = json.loads(exc.read(32768))
            if body.get('error') in ('not_found', 'unauthorized', 'account_mismatch', 'meta_request_failed', 'meta_connection_failed', 'threads_token_not_configured', 'insights_own_posts_only', 'unexpected_meta_insights_response'):
                safe['bridge_error'] = body['error']
            # Only numeric upstream diagnostics; never relay raw messages or headers.
            for key in ('upstream_status', 'upstream_code', 'upstream_subcode'):
                if type(body.get(key)) is int:
                    safe[key] = body[key]
        except (ValueError, AttributeError, OSError):
            pass
        return {'state': 'blocked', **safe}
    except (URLError, TimeoutError, ValueError):
        return {'state': 'blocked', 'error': 'read_connection_failed'}
    if not isinstance(data, dict) or not isinstance(data.get('account'), dict) or data['account'].get('username') != ACCOUNT or not isinstance(data.get('data'), list):
        raise ReadError('unexpected_account_or_read_response')
    return {'state': 'read_complete', **data}


def seal_result(result, certificate):
    # Public recipient certificate only; the private key stays in the local workspace.
    with tempfile.TemporaryDirectory() as tmp:
        cert = Path(tmp) / 'recipient.pem'
        cert.write_text(certificate)
        completed = subprocess.run(['openssl', 'cms', '-encrypt', '-aes-256-gcm', '-binary',
            '-outform', 'DER', str(cert)], input=json.dumps(result, ensure_ascii=False).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if completed.returncode:
            return {'state': 'blocked', 'error': 'result_encryption_failed'}
        return {'state': result.get('state', 'blocked'), 'operation': result.get('operation'),
                'recipient_sha256': hashlib.sha256(certificate.encode()).hexdigest(),
                'encrypted_result': base64.b64encode(completed.stdout).decode()}


def main(paths):
    if len(paths) != 1:
        raise SystemExit('Require exactly one read-only request')
    path = Path(paths[0])
    job = {}
    try:
        if path.is_symlink() or not path.resolve().is_relative_to((ROOT / 'threads-read-requests').resolve()) or path.suffix != '.json' or path.stat().st_size > 4096:
            raise ReadError('invalid_read_file')
        job = json.loads(path.read_text())
        result = process(job)
    except (ReadError, OSError, ValueError) as exc:
        result = {'state': 'blocked', 'error': str(exc) if isinstance(exc, ReadError) else 'invalid_read_file'}
    certificate = job.get('result_recipient_certificate') if isinstance(job, dict) else None
    if certificate and isinstance(certificate, str):
        result = seal_result(result, certificate)
    (ROOT / 'threads-read-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('state') == 'read_complete' else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
