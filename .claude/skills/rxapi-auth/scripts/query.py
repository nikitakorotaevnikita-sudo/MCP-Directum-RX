#!/usr/bin/env python3
"""
Универсальный CLI-инструмент для OData-запросов к Directum RX / Aura.

Использование:
  python query.py IEmployees --filter "contains(Name,'Иванов')" --select "Id,Name" --top 5
  python query.py IAssignments --filter "Performer/Id eq 1165" --count --top 20
  python query.py IEmployees(1165)                           # одна запись по Id
  python query.py --list                                     # все доступные EntitySets
  python query.py --list employee                            # EntitySets содержащие 'employee'
"""

import sys
import os
import json
import argparse
import urllib.request
import urllib.parse

# Гарантируем UTF-8 вывод на Windows
sys.stdout.reconfigure(encoding='utf-8')

# Корень проекта: 4 уровня вверх от scripts/ → rxapi-auth/ → skills/ → .claude/ → проект
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(_HERE, '..', '..', '..', '..'))
ENV_FILE = os.path.join(ROOT, '.env')
METADATA_FILE = os.path.join(ROOT, 'metadata.xml')
BASE_URL = 'https://aura.npo-comp.ru/Integration/odata'


def load_token():
    if not os.path.exists(ENV_FILE):
        raise FileNotFoundError(f'.env not found at {ENV_FILE}')
    with open(ENV_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                # Поддержка форматов: TOKEN=..., Basic ..., просто строка
                if '=' in line:
                    _, value = line.split('=', 1)
                    return value.strip().strip('"').strip("'")
                return line
    raise ValueError('.env не содержит токена')


def odata_request(path, params=None):
    token = load_token()
    url = f'{BASE_URL}/{path}'
    if params:
        url += '?' + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)

    req = urllib.request.Request(url)
    req.add_header('Authorization', token)
    req.add_header('Accept', 'application/json')
    req.add_header('OData-MaxVersion', '4.0')

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        print(f'HTTP {e.code}: {e.reason}', file=sys.stderr)
        print(body[:500], file=sys.stderr)
        sys.exit(1)


def list_entity_sets(name_filter=None):
    import xml.etree.ElementTree as ET
    if not os.path.exists(METADATA_FILE):
        print('metadata.xml не найден. Запустите: python generate_knowledge.py', file=sys.stderr)
        sys.exit(1)
    tree = ET.parse(METADATA_FILE)
    root = tree.getroot()
    ns = {'edm': 'http://docs.oasis-open.org/odata/ns/edm'}
    results = []
    for schema in root.findall('.//edm:Schema', ns):
        if schema.get('Namespace') == 'Sungero.IntegrationService':
            container = schema.find('edm:EntityContainer', ns)
            if container is not None:
                for es in container.findall('edm:EntitySet', ns):
                    name = es.get('Name', '')
                    entity_type = es.get('EntityType', '').split('.')[-1].replace('Dto', '')
                    if not name_filter or name_filter.lower() in name.lower():
                        results.append((name, entity_type))
    results.sort(key=lambda x: x[0])
    for name, etype in results:
        print(f'{name:<55} → {etype}')
    print(f'\nВсего: {len(results)}')


def main():
    parser = argparse.ArgumentParser(
        description='OData-запросы к Directum RX / Aura',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('endpoint', nargs='?',
                        help='EntitySet или EntitySet(Id). Пример: IEmployees или IEmployees(1165)')
    parser.add_argument('--filter', '-f', metavar='EXPR',
                        help='$filter выражение')
    parser.add_argument('--select', '-s', metavar='FIELDS',
                        help='$select поля через запятую')
    parser.add_argument('--expand', '-e', metavar='PROPS',
                        help='$expand навигационные свойства')
    parser.add_argument('--orderby', '-o', metavar='FIELD',
                        help='$orderby')
    parser.add_argument('--top', '-t', type=int, default=None,
                        help='$top — максимум записей (по умолчанию без ограничения)')
    parser.add_argument('--skip', type=int, default=None,
                        help='$skip — пропустить N записей')
    parser.add_argument('--count', '-c', action='store_true',
                        help='Добавить $count=true')
    parser.add_argument('--raw', action='store_true',
                        help='Вывести сырой JSON без форматирования')
    parser.add_argument('--list', '-l', nargs='?', const='',
                        help='Показать доступные EntitySets (опционально — фильтр по имени)')

    args = parser.parse_args()

    # Режим списка EntitySets
    if args.list is not None:
        list_entity_sets(args.list if args.list else None)
        return

    if not args.endpoint:
        parser.print_help()
        sys.exit(0)

    # Одна запись по Id: IEmployees(1165)
    if '(' in args.endpoint and args.endpoint.endswith(')'):
        path = args.endpoint
        params = {}
        if args.select:
            params['$select'] = args.select
        if args.expand:
            params['$expand'] = args.expand
        result = odata_request(path, params if params else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # Коллекция
    params = {}
    if args.filter:
        params['$filter'] = args.filter
    if args.select:
        params['$select'] = args.select
    if args.expand:
        params['$expand'] = args.expand
    if args.orderby:
        params['$orderby'] = args.orderby
    if args.top is not None:
        params['$top'] = args.top
    if args.skip is not None:
        params['$skip'] = args.skip
    if args.count:
        params['$count'] = 'true'

    result = odata_request(args.endpoint, params if params else None)

    if args.raw:
        print(json.dumps(result, ensure_ascii=False))
        return

    # Красивый вывод
    count = result.get('@odata.count')
    if count is not None:
        print(f'Всего записей: {count}')
        print()

    items = result.get('value', [result] if 'Id' in result else [])
    for item in items:
        # Убираем служебные поля @odata.*
        clean = {k: v for k, v in item.items() if not k.startswith('@odata')}
        print(json.dumps(clean, ensure_ascii=False, indent=2))
        print()

    if not items:
        print('(пустой результат)')


if __name__ == '__main__':
    main()
