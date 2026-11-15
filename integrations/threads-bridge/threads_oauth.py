"""Browser-only reauthorization helper. Never changes the active Threads token."""
import hashlib,hmac,html,json,os,secrets,time
from http.cookies import SimpleCookie
from urllib.parse import urlencode
from urllib.request import Request,urlopen

APP_ID='848853311611370'
ORIGIN='https://creative-hl3h.onrender.com'
CALLBACK=ORIGIN+'/oauth/threads/callback'
COOKIE='__Host-threads_reauth'
SCOPES='threads_basic,threads_content_publish,threads_manage_replies,threads_manage_insights,threads_read_replies,threads_keyword_search'
EXPECTED_ID='27025518633755187'
class OAuthError(Exception):pass

def secret():return os.environ.get('THREADS_APP_SECRET','').strip()
def ready():return bool(secret())
def page(text):return '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Threads 搜尋授權</title><style>body{font:17px/1.8 sans-serif;max-width:700px;margin:60px auto;padding:20px;color:#283b38}textarea{width:100%;height:160px;overflow-wrap:anywhere}a{color:#34594b}</style>'+text+'</html>'
def start(now=None):
 if not ready():raise OAuthError('setup_required')
 stamp=int(time.time() if now is None else now);payload=str(stamp)+'.'+secrets.token_urlsafe(32);signature=hmac.new(secret().encode(),payload.encode(),hashlib.sha256).hexdigest();state=payload+'.'+signature
 return 'https://www.threads.net/oauth/authorize?'+urlencode({'client_id':APP_ID,'redirect_uri':CALLBACK,'scope':SCOPES,'response_type':'code','state':state}),COOKIE+'='+state+'; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600'
def validate_state(state,cookie,now=None):
 if not ready() or not isinstance(state,str) or len(state)>256:raise OAuthError('invalid_state')
 try:
  jar=SimpleCookie();jar.load(cookie);actual=jar[COOKIE].value;stamp,nonce,sig=state.split('.');age=int(time.time() if now is None else now)-int(stamp)
 except (KeyError,ValueError,TypeError):raise OAuthError('invalid_state') from None
 expected=hmac.new(secret().encode(),(stamp+'.'+nonce).encode(),hashlib.sha256).hexdigest()
 if not hmac.compare_digest(actual,state) or not hmac.compare_digest(expected,sig) or not 0<=age<=600:raise OAuthError('invalid_state')
def request_json(url,form=None,token=None):
 headers={'Accept':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 data=None
 if form is not None:data=urlencode(form).encode();headers['Content-Type']='application/x-www-form-urlencoded'
 try:
  with urlopen(Request(url,data=data,headers=headers),timeout=25) as r:d=json.load(r)
  if not isinstance(d,dict) or 'error' in d:raise OAuthError('meta_request_failed')
  return d
 except Exception:raise OAuthError('meta_request_failed') from None

def complete(params,cookie):
 if any(len(v)!=1 for v in params.values()):raise OAuthError('invalid_callback')
 validate_state(params.get('state',[''])[0],cookie)
 if 'error'in params:raise OAuthError('authorization_declined')
 code=params.get('code',[''])[0]
 if not code or len(code)>4096:raise OAuthError('invalid_code')
 short=request_json('https://graph.threads.net/oauth/access_token',{'client_id':APP_ID,'client_secret':secret(),'grant_type':'authorization_code','redirect_uri':CALLBACK,'code':code})
 token=short.get('access_token')
 if not isinstance(token,str) or not token or str(short.get('user_id'))!=EXPECTED_ID:raise OAuthError('account_mismatch')
 user=request_json('https://graph.threads.net/v1.0/me?fields=id,username',token=token)
 if str(user.get('id'))!=EXPECTED_ID or user.get('username')!='luen.dayori':raise OAuthError('account_mismatch')
 # A successful actual read is the acceptance gate; no new token is activated here.
 result=request_json('https://graph.threads.net/v1.0/keyword_search?'+urlencode({'q':'戀愛','search_type':'RECENT','limit':1,'fields':'id,text,username'}),token=token)
 if not isinstance(result.get('data'),list):raise OAuthError('search_not_verified')
 long=request_json('https://graph.threads.net/access_token?'+urlencode({'grant_type':'th_exchange_token','client_secret':secret(),'access_token':token}))
 token=long.get('access_token')
 if not isinstance(token,str) or not token:raise OAuthError('long_token_missing')
 # Verify the final token too, before letting the owner copy it into Render.
 user=request_json('https://graph.threads.net/v1.0/me?fields=id,username',token=token)
 if str(user.get('id'))!=EXPECTED_ID or user.get('username')!='luen.dayori':raise OAuthError('account_mismatch')
 result=request_json('https://graph.threads.net/v1.0/keyword_search?'+urlencode({'q':'戀愛','search_type':'RECENT','limit':1,'fields':'id,text,username'}),token=token)
 if not isinstance(result.get('data'),list):raise OAuthError('search_not_verified')
 return page('<h1>搜尋測試成功</h1><p>已核對 @luen.dayori，新長效 Token 的搜尋讀取成功。現用 Token 尚未更改。</p><p>只在 Render 的環境變數 THREADS_ACCESS_TOKEN 貼上以下值並儲存，然後關閉本頁。不要貼入 Chat、截圖或其他地方。</p><textarea readonly autocomplete="off" spellcheck="false">'+html.escape(token)+'</textarea><p>儲存後請通知統籌再驗證現用接駁。實際到期日請用 Meta Token Debugger 核對。</p>')
