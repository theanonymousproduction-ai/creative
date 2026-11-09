"""Read-only Threads discovery runner. Credentials stay in Actions/Render."""
import json
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
    allowed = {'schema', 'account', 'operation', 'q', 'post_id', 'limit', 'search_type'}
    if not isinstance(job, dict) or set(job) - allowed or job.get('schema') != 1 or job.get('account') != ACCOUNT:
        raise ReadError('invalid_read_job')
    operation = job.get('operation')
    extras = {'search': {'q', 'limit', 'search_type'}, 'post': {'post_id'},
              'replies': {'post_id', 'limit'}, 'own_posts': {'limit'}}
    if operation not in extras or set(job) - {'schema', 'account', 'operation'} - extras[operation]:
        raise ReadError('invalid_read_operation')
    limit = job.get('limit', 5)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ReadError('invalid_read_limit')
    result = {'operation': operation}
    if operation != 'post':
        result['limit'] = str(limit)
    if operation == 'search':
        q = job.get('q')
        ordering = job.get('search_type', 'RECENT')
        if not isinstance(q, str) or not q.strip() or len(q) > 80 or any(ord(c) < 32 for c in q):
            raise ReadError('invalid_search_query')
        if ordering not in ('TOP', 'RECENT'):
            raise ReadError('invalid_search_type')
        result.update(q=q, search_type=ordering)
    elif operation in ('post', 'replies'):
        target = job.get('post_id')
        if not isinstance(target, str) or not target.isascii() or not target.isdigit() or len(target) > 40:
            raise ReadError('invalid_read_post_id')
        result['post_id'] = target
    return result


def process(job):
    params = parameters(job)
    secret = os.environ.get('THREADS_BRIDGE_API_KEY', '').strip()
    if not secret:
        raise ReadError('bridge_secret_missing')
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


def main(paths):
    if len(paths) != 1:
        raise SystemExit('Require exactly one read-only request')
    path = Path(paths[0])
    try:
        if path.is_symlink() or not path.resolve().is_relative_to((ROOT / 'threads-read-requests').resolve()) or path.suffix != '.json' or path.stat().st_size > 4096:
            raise ReadError('invalid_read_file')
        result = process(json.loads(path.read_text()))
    except (ReadError, OSError, ValueError) as exc:
        result = {'state': 'blocked', 'error': str(exc) if isinstance(exc, ReadError) else 'invalid_read_file'}
    (ROOT / 'threads-read-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('state') == 'read_complete' else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
