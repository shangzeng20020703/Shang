"""Read-only deployment checks; supply BASE_URL, ADMIN_PHONE and ADMIN_PASSWORD."""
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

base = os.environ['BASE_URL'].rstrip('/')

def request(path, data=None, token=None, expected=200):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = Request(base + path, data=json.dumps(data).encode() if data else None, headers=headers)
    try:
        response = urlopen(req, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        assert response.status == expected, (path, response.status, body[:200])
        return json.loads(body) if 'application/json' in response.headers.get('Content-Type', '') else body

assert request('/health')['status'] == 'healthy'
for path in ['/', '/mobile/', '/mobile/app/tabs/workbench']:
    assert b'<html' in request(path).lower(), path
request('/api/v1/field/people', expected=401)
credentials = {'phone': os.environ['ADMIN_PHONE'], 'password': os.environ['ADMIN_PASSWORD']}
token = request('/api/v1/auth/management-login', credentials)['access_token']
for path in ['/api/v1/auth/management-session', '/api/v1/field/people',
             '/api/v1/field/dashboard', '/api/v1/field/positions', '/api/v1/attendance/rules']:
    request(path, token=token)
people = request('/api/v1/field/people', token=token)
if os.environ.get('EXPECT_EMPTY_INSTALL') == '1':
    assert len(people) == 1 and people[0]['phone'] == credentials['phone'], 'Unexpected seeded people'
mobile_token = request('/api/v1/auth/login', credentials)['access_token']
request('/api/v1/auth/me', token=mobile_token)
assert request('/api/v1/field/roster/template', token=token).startswith(b'PK')
request('/uploads/nonexistent.txt', expected=401)
print('PASS: HTTP/HTML routes, MySQL health, admin/mobile login, protected APIs and roster template')
