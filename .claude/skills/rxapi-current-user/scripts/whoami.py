#!/usr/bin/env python3
"""
Возвращает JSON с данными текущего пользователя.

Использование:
  python scripts/whoami.py
  → {"id": 1165, "name": "Беляк Игорь Сергеевич", "login": "nt_work\\belyak_is"}
"""

import sys, os, json, base64, urllib.request, urllib.parse

sys.stdout.reconfigure(encoding='utf-8')

# Корень проекта: 3 уровня вверх от scripts/ → rxapi-current-user/ → skills/ → .claude/ → проект
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(_HERE, '..', '..', '..', '..'))
ENV_FILE = os.path.join(ROOT, '.env')
BASE_URL = 'https://aura.npo-comp.ru/Integration/odata'


def load_token():
    with open(ENV_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                return line.split('=', 1)[-1].strip().strip('"').strip("'") if '=' in line else line
    raise ValueError('.env не содержит токена')


def get_login(token):
    encoded = token.replace('Basic ', '').strip()
    decoded = base64.b64decode(encoded).decode('utf-8')
    return decoded.split(':')[0]


def odata_get(path, params, token):
    url = f'{BASE_URL}/{path}?' + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    req = urllib.request.Request(url)
    req.add_header('Authorization', token)
    req.add_header('Accept', 'application/json')
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))


def main():
    token = load_token()
    login = get_login(token)

    data = odata_get('IUsers', {
        '$filter': f"Login/LoginName eq '{login}'",
        '$select': 'Id,Name',
        '$top': '1'
    }, token)

    items = data.get('value', [])
    if not items:
        print(json.dumps({'error': f'Пользователь с логином {login!r} не найден'}, ensure_ascii=False))
        sys.exit(1)

    user = items[0]
    result = {
        'id': user['Id'],
        'name': user.get('Name', ''),
        'login': login
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
