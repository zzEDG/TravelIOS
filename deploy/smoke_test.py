"""Smoke test through Nginx: health, SPA, registration, session, CSRF, trips and logout."""
import http.cookiejar,json,secrets,sys,urllib.request,urllib.error
base=sys.argv[1].rstrip('/')
origin=sys.argv[2] if len(sys.argv)>2 else base
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(path,body=None,headers=None):
    req=urllib.request.Request(base+path, data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','Origin':origin,**(headers or {})})
    with client.open(req,timeout=20) as response:
        return response.status,json.loads(response.read())
assert call('/health')[1]['status']=='healthy'
with client.open(base+'/login',timeout=20) as response:
    assert response.status==200 and b'<html' in response.read().lower()
credentials={'username':'smoke_'+secrets.token_hex(6),'password':secrets.token_urlsafe(24)}
status,result=call('/api/v1/auth/register',credentials)
assert status==201
user_id=result['data']['id']
login=call('/api/v1/auth/login',credentials)[1]['data']
assert call('/api/v1/auth/me')[1]['data']['id']==user_id
assert call('/api/v1/trips')[0]==200
csrf=call('/api/v1/auth/csrf')[1]['data']['csrf_token']
assert csrf==login['csrf_token']
try:
    call('/api/v1/auth/logout',{})
    raise AssertionError('CSRF enforcement missing')
except urllib.error.HTTPError as error:
    assert error.code==403
assert call('/api/v1/auth/logout',{}, {'X-CSRF-Token':csrf})[0]==200
try:
    call('/api/v1/auth/me')
    raise AssertionError('Session still active')
except urllib.error.HTTPError as error:
    assert error.code==401
print('PASS: health, SPA, registration, login, session, trips, CSRF rejection, logout')
print('Smoke test account ID: '+user_id)