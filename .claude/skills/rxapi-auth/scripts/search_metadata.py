#!/usr/bin/env python3
"""
Поиск сущностей и полей в метаданных OData (metadata.xml).

Использование:
  python scripts/search_metadata.py IEmployee         # поля сущности
  python scripts/search_metadata.py vacation          # все типы с 'vacation' в имени
  python scripts/search_metadata.py --field Deadline  # все сущности с полем Deadline
  python scripts/search_metadata.py --ns HR           # все типы в модуле HR
"""

import sys, os, argparse, xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding='utf-8')

# Корень проекта: 4 уровня вверх от scripts/ → rxapi-auth/ → skills/ → .claude/ → проект
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(_HERE, '..', '..', '..', '..'))
METADATA_FILE = os.path.join(ROOT, 'metadata.xml')
NS = {'edm': 'http://docs.oasis-open.org/odata/ns/edm'}
MODEL_PREFIX = 'Sungero.IntegrationService.Models.Generated.'


def short_ns(namespace):
    return namespace.replace(MODEL_PREFIX, '')


def load_tree():
    if not os.path.exists(METADATA_FILE):
        print('metadata.xml не найден. Загрузите метаданные:', file=sys.stderr)
        print('  curl -H "Authorization: $(cat .env)" https://aura.npo-comp.ru/Integration/odata/$metadata -o metadata.xml', file=sys.stderr)
        sys.exit(1)
    return ET.parse(METADATA_FILE).getroot()


def search_types(root, query):
    """Найти EntityType по части имени."""
    query_lower = query.lower()
    results = []
    for schema in root.findall('.//edm:Schema', NS):
        ns = schema.get('Namespace', '')
        for et in schema.findall('edm:EntityType', NS):
            name = et.get('Name', '')
            if query_lower in name.lower():
                results.append((ns, name, et))
    return results


def print_type_details(ns, name, et):
    base = et.get('BaseType', '')
    base_short = base.split('.')[-1] if base else ''
    print(f'\n[{short_ns(ns)}] {name}' + (f'  (extends {base_short})' if base_short else ''))

    props = et.findall('edm:Property', NS)
    if props:
        print('  Свойства:')
        for p in props:
            req = ' *' if p.get('Nullable') == 'false' else ''
            print(f'    {p.get("Name"):<35} {p.get("Type","").replace("Edm.","")+req}')

    navs = et.findall('edm:NavigationProperty', NS)
    if navs:
        print('  Связи:')
        for np in navs:
            t = np.get('Type', '')
            coll = t.startswith('Collection(')
            short = t.replace('Collection(', '').replace(')', '').split('.')[-1].replace('Dto', '')
            arrow = f'→[{short}]' if coll else f'→ {short}'
            print(f'    {np.get("Name"):<35} {arrow}')


def search_by_field(root, field_name):
    """Найти все сущности с данным полем."""
    field_lower = field_name.lower()
    results = []
    for schema in root.findall('.//edm:Schema', NS):
        ns = schema.get('Namespace', '')
        for et in schema.findall('edm:EntityType', NS):
            for p in et.findall('edm:Property', NS):
                if field_lower in p.get('Name', '').lower():
                    results.append((ns, et.get('Name'), p.get('Name'), p.get('Type', '')))
            for np in et.findall('edm:NavigationProperty', NS):
                if field_lower in np.get('Name', '').lower():
                    results.append((ns, et.get('Name'), np.get('Name'), np.get('Type', '')))
    return results


def search_by_namespace(root, ns_filter):
    """Все типы в модуле."""
    ns_lower = ns_filter.lower()
    results = []
    for schema in root.findall('.//edm:Schema', NS):
        ns = schema.get('Namespace', '')
        if ns_lower in ns.lower():
            for et in schema.findall('edm:EntityType', NS):
                results.append((ns, et.get('Name')))
    return results


def main():
    parser = argparse.ArgumentParser(description='Поиск в метаданных Directum RX OData', epilog=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('query', nargs='?', help='Часть имени EntityType для поиска')
    parser.add_argument('--field', '-f', metavar='FIELD', help='Найти все сущности с данным полем')
    parser.add_argument('--ns', metavar='MODULE', help='Показать все типы в модуле')
    parser.add_argument('--short', action='store_true', help='Только имена, без деталей')
    args = parser.parse_args()

    root = load_tree()

    if args.field:
        results = search_by_field(root, args.field)
        if not results:
            print(f'Поле "{args.field}" не найдено')
            return
        print(f'Сущности с полем содержащим "{args.field}":')
        for ns, etype, fname, ftype in results:
            print(f'  [{short_ns(ns)}] {etype}.{fname}  ({ftype.split(".")[-1]})')
        print(f'\nВсего: {len(results)}')
        return

    if args.ns:
        results = search_by_namespace(root, args.ns)
        if not results:
            print(f'Модуль "{args.ns}" не найден')
            return
        for ns, name in results:
            print(f'  [{short_ns(ns)}] {name}')
        print(f'\nВсего: {len(results)}')
        return

    if not args.query:
        parser.print_help()
        return

    results = search_types(root, args.query)
    if not results:
        print(f'Тип "{args.query}" не найден')
        return

    if args.short or len(results) > 5:
        for ns, name, _ in results:
            print(f'  [{short_ns(ns)}] {name}')
        if len(results) > 5:
            print(f'\nВсего: {len(results)}. Уточните запрос или используйте --short')
        return

    for ns, name, et in results:
        print_type_details(ns, name, et)

    print(f'\nВсего типов: {len(results)}')


if __name__ == '__main__':
    main()
