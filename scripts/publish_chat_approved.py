"""GitHub runner bridge for a version approved in Chat. Never run without approval.
Secrets stay in the runner/Render; results contain no credentials or preview tickets.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

BRIDGE = 'https://creative-hl3h.onrender.com'
EXPECTED_ACCOUNT = 'wannasaybutdidnt'
ROOT = Path(__file__).resolve().parents[1]


class Stop(Exception):
    pass


def fingerprint(account, text, attachment=''):
    data = {'account': account, 'text': text, 'attachment': attachment}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def validate(job):
    if not isinstance(job, dict) or job.get('schema') != 1 or job.get('approved') is not True:
        raise Stop('explicit_chat_approval_required')
    if job.get('account') != EXPECTED_ACCOUNT:
        raise Stop('unexpected_account')
    ref = job.get('approval_ref', '')
    if not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', ref):
        raise Stop('invalid_approval_reference')
    approved_at = job.get('approved_at')
    if type(approved_at) is not int or not -120 <= int(time.time()) - approved_at <= 86400:
        raise Stop('approval_not_fresh')
    text, attachment = job.get('text'), job.get('attachment', '')
    for value, limit in [(text, 500), (attachment, 10000)]:
        if not isinstance(value, str) or value != value.strip():
            raise Stop('approval_text_must_be_exact')
        try:
            if len(value.encode('utf-16-le')) // 2 > limit:
                raise Stop('approved_text_too_long')
        except UnicodeEncodeError:
            raise Stop('invalid_approval_text') from None
    if not text:
        raise Stop('empty_post')
    digest = fingerprint(EXPECTED_ACCOUNT, text, attachment)
    if job.get('sha256') != digest:
        raise Stop('approval_digest_mismatch')
    return digest


def json_http(url, token, body=None, origin=None):
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
               'User-Agent': 'creative-chat-approved-publisher'}
    if origin:
        headers['Origin'] = origin
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body, ensure_ascii=False).encode()
    req = Request(url, data=data, headers=headers)
    try:
        with urlopen(req, timeout=100) as response:
            return json.load(response)
    except HTTPError as exc:
        raise Stop('request_rejected_http_' + str(exc.code)) from None
    except (URLError, TimeoutError, ValueError):
        raise Stop('network_result_unknown_check_before_retry') from None


def bridge(path, secret, body=None):
    return json_http(BRIDGE + path, secret, body, BRIDGE if body is not None else None)


def github(path, token, body=None):
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise Stop('invalid_repository')
    return json_http('https://api.github.com/repos/' + repo + path, token, body)


def process(job):
    digest = validate(job)
    secret = os.environ.get('THREADS_BRIDGE_API_KEY', '').strip()
    gh_token = os.environ.get('GH_TOKEN', '').strip()
    if not secret or not gh_token:
        raise Stop('one_time_secret_setup_missing')
    user = bridge('/account', secret)
    if user.get('username') != EXPECTED_ACCOUNT or user.get('publishing_enabled') is not True:
        raise Stop('wrong_account_or_publishing_switch_off')
    snapshot = bridge('/preview', secret, {'text': job['text'], 'attachment': job.get('attachment', '')})
    if (snapshot.get('text') != job['text'] or
        snapshot.get('attachment', '') != job.get('attachment', '') or
        snapshot.get('account', {}).get('id') != user.get('id') or
        snapshot.get('account', {}).get('username') != EXPECTED_ACCOUNT or
        not isinstance(snapshot.get('ticket'), str)):
        raise Stop('preview_does_not_match_chat_approval')
    environment = 'threads-' + digest
    existing = github('/deployments?' + urlencode({'environment': environment, 'per_page': 1}), gh_token)
    if not isinstance(existing, list):
        raise Stop('unexpected_approval_ledger')
    if existing:
        raise Stop('already_attempted_durable_ledger_blocks_repeat')
    sha = os.environ.get('GITHUB_SHA', '')
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise Stop('invalid_source_commit')
    # Workflow concurrency serializes claims. Retain every attempt across Render restarts.
    claim = github('/deployments', gh_token, {
        'ref': sha, 'auto_merge': False, 'required_contexts': [],
        'environment': environment, 'transient_environment': False,
        'production_environment': True,
        'description': 'Chat-approved Threads ' + job['approval_ref'],
        'payload': {'sha256': digest, 'approval_ref': job['approval_ref'], 'account': EXPECTED_ACCOUNT}})
    if not isinstance(claim.get('id'), int):
        raise Stop('durable_ledger_claim_failed')
    result = {'approval_ref': job['approval_ref'], 'sha256': digest,
              'account': EXPECTED_ACCOUNT, 'deployment_id': claim['id'],
              'state': 'uncertain', 'post_id': None, 'permalink': None}
    try:
        published = bridge('/publish', secret, {'ticket': snapshot['ticket'], 'approved': True})
        if published.get('state') != 'published' or not str(published.get('post_id', '')).isdigit():
            raise Stop('publication_result_not_confirmed')
        link = published.get('permalink')
        parsed = urlsplit(link or '')
        if parsed.scheme != 'https' or parsed.hostname not in ('www.threads.net', 'threads.net', 'www.threads.com', 'threads.com'):
            link = None
        result.update(state='published', post_id=str(published['post_id']), permalink=link,
                      completed_at=int(time.time()))
    except Stop as exc:
        result['error'] = str(exc)
    # Even if reporting fails, the permanent attempt claim blocks automatic resubmission.
    try:
        status = {'state': 'success' if result['state'] == 'published' else 'failure',
                  'auto_inactive': False,
                  'description': ('Threads post ' + str(result['post_id']) if result['state'] == 'published'
                                  else 'Uncertain: inspect Threads before retrying')}
        if result.get('permalink'):
            status['target_url'] = result['permalink']
        github('/deployments/' + str(claim['id']) + '/statuses', gh_token, status)
    except Stop:
        result['reporting_warning'] = 'ledger_status_update_failed_attempt_record_retained'
    return result


def main(paths):
    receipts, ok = [], True
    for name in paths:
        path = Path(name).resolve()
        allowed = (ROOT / 'approved-threads').resolve()
        try:
            if not path.is_relative_to(allowed) or path.is_symlink() or path.suffix != '.json' or path.stat().st_size > 65536:
                raise Stop('invalid_job_file')
            job = json.loads(path.read_text())
            result = process(job)
        except (Stop, OSError, ValueError) as exc:
            result = {'state': 'blocked', 'error': str(exc) if isinstance(exc, Stop) else 'invalid_job_file'}
        receipts.append(result)
        if result.get('state') != 'published':
            ok = False
            break  # Do not process further posts after any uncertain outcome.
    out = ROOT / 'publish-receipt.json'
    out.write_text(json.dumps({'results': receipts}, ensure_ascii=False, indent=2) + '\n')
    for result in receipts:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if ok and receipts else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
