"""Authenticated dry-run only. This file never calls /publish or the deployment ledger."""
import hashlib,json,sys
from pathlib import Path
import publish_chat_approved as worker
ROOT=Path(__file__).resolve().parents[1]
def verify(job):
 if not isinstance(job,dict) or set(job)!={'schema','account','text','image_url','image_sha256'} or job.get('schema')!=1 or job.get('account')!=worker.EXPECTED_ACCOUNT:raise worker.Stop('invalid_image_preview_request')
 text=job.get('text')
 if not isinstance(text,str) or text!=text.strip() or not text or len(text.encode('utf-16-le'))//2>500:raise worker.Stop('invalid_image_preview_text')
 try:image=worker.media_validation.validate_image(job)
 except ValueError as exc:raise worker.Stop(str(exc)) from None
 worker.verify_image_asset(image)
 secret=worker.os.environ.get('THREADS_BRIDGE_API_KEY','').strip()
 if not secret:raise worker.Stop('bridge_secret_missing')
 user=worker.bridge('/account',secret)
 if user.get('username')!=worker.EXPECTED_ACCOUNT or user.get('publishing_enabled') is not True:raise worker.Stop('account_or_switch_mismatch')
 preview=worker.bridge('/preview',secret,{'text':text,'attachment':'',**image})
 if preview.get('text')!=text or preview.get('attachment')!='' or preview.get('account',{}).get('id')!=user.get('id') or preview.get('account',{}).get('username')!=worker.EXPECTED_ACCOUNT or any(preview.get(k)!=v for k,v in image.items()) or not isinstance(preview.get('ticket'),str):raise worker.Stop('image_preview_mismatch')
 return {'state':'image_preview_verified','account':worker.EXPECTED_ACCOUNT,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),**image,'publication_sent':False}
def main(paths):
 result={'state':'blocked'}
 try:
  if len(paths)!=1:raise worker.Stop('require_one_preview_request')
  p=Path(paths[0])
  if p.is_symlink() or not p.resolve().is_relative_to((ROOT/'threads-preview-requests').resolve()) or p.suffix!='.json' or p.stat().st_size>16384:raise worker.Stop('invalid_preview_file')
  result=verify(json.loads(p.read_text()))
 except (worker.Stop,OSError,ValueError) as exc:result={'state':'blocked','error':str(exc) if isinstance(exc,worker.Stop) else 'invalid_preview_file','publication_sent':False}
 (ROOT/'image-preview-receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False));return 0 if result['state']=='image_preview_verified' else 1
if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
