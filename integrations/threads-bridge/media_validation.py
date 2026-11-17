"""Bounded single-image metadata shared by the approval runner and bridge."""
import re
PREFIX='https://raw.githubusercontent.com/theanonymousproduction-ai/creative/'
def validate_image(data):
 if 'image_url' not in data and 'image_sha256' not in data:return {}
 url,digest=data.get('image_url'),data.get('image_sha256')
 if not isinstance(url,str) or len(url)>600 or not re.fullmatch(re.escape(PREFIX)+r'[0-9a-f]{40}/assets/threads/[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg)',url):raise ValueError('invalid_approved_image_url')
 if any(part in ('.','..') for part in url[len(PREFIX):].split('/')):raise ValueError('invalid_approved_image_url')
 if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):raise ValueError('invalid_approved_image_sha256')
 if data.get('attachment','') or data.get('reply_to_id') is not None or data.get('repost') is not None:raise ValueError('image_main_only_without_text_attachment')
 return {'image_url':url,'image_sha256':digest}
