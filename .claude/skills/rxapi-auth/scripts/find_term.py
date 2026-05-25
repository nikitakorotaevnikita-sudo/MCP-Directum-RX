#!/usr/bin/env python3
"""
Поиск русских терминов в модели данных Directum RX / Aura.
Переводит пользовательские слова в API-имена сущностей и полей.

Использование:
  python find_term.py команда            # найти всё, где встречается "команда"
  python find_term.py отпуск             # сущность + поля
  python find_term.py --entity команда   # только сущности
  python find_term.py --prop MainTeam    # что значит поле MainTeam
  python find_term.py --eset IEmployees  # все поля у IEmployees
"""

import sys, os, json, argparse

sys.stdout.reconfigure(encoding='utf-8')

_HERE = os.path.dirname(os.path.abspath(__file__))
TERMS_FILE = os.path.join(_HERE, '..', 'entity_terms.json')


def load_terms():
    if not os.path.exists(TERMS_FILE):
        print(f'entity_terms.json не найден: {TERMS_FILE}', file=sys.stderr)
        print('Запустите: python .claude/skills/rxapi-auth/scripts/build_terms.py', file=sys.stderr)
        sys.exit(1)
    with open(TERMS_FILE, encoding='utf-8') as f:
        return json.load(f)


def search_all(terms, query):
    """Ищет query (ru или en) в именах сущностей и полях."""
    q = query.lower()
    entity_hits = []
    prop_hits = []

    for code, entry in terms.items():
        dn = entry.get('display_name', '')
        cn = entry.get('collection_name', '')
        eset = entry.get('entity_set', '')

        # Совпадение по имени сущности (ru или code)
        if q in dn.lower() or q in cn.lower() or q in code.lower():
            entity_hits.append((code, eset, dn, cn))

        # Совпадение по имени поля (ru или code)
        for field_code, field_ru in entry.get('properties', {}).items():
            if q in field_ru.lower() or q in field_code.lower():
                prop_hits.append((code, eset, field_code, field_ru))

    return entity_hits, prop_hits


def lookup_entity_set(terms, eset_name):
    """Показать все свойства сущности по имени EntitySet."""
    eset_lower = eset_name.lower()
    results = []
    for code, entry in terms.items():
        if entry.get('entity_set', '').lower() == eset_lower:
            results.append((code, entry))
    return results


def lookup_property(terms, field_code):
    """Найти что значит поле field_code (по code имени)."""
    fc_lower = field_code.lower()
    results = []
    for code, entry in terms.items():
        for fc, fru in entry.get('properties', {}).items():
            if fc.lower() == fc_lower:
                results.append((code, entry.get('entity_set', '?'), fc, fru))
    return results


def main():
    parser = argparse.ArgumentParser(
        description='Поиск терминов в модели Directum RX / Aura',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('query', nargs='?', help='Слово для поиска (ru или en)')
    parser.add_argument('--entity', '-e', metavar='TERM', help='Только сущности')
    parser.add_argument('--prop', '-p', metavar='FIELD', help='Что значит поле (по API-имени)')
    parser.add_argument('--eset', metavar='ENTITYSET', help='Все поля у EntitySet')
    args = parser.parse_args()

    terms = load_terms()

    if args.prop:
        results = lookup_property(terms, args.prop)
        if not results:
            print(f'Поле "{args.prop}" не найдено')
        else:
            print(f'Поле {args.prop}:')
            for code, eset, fc, fru in results:
                print(f'  [{code}] {eset}  →  {fc} = "{fru}"')
        return

    if args.eset:
        results = lookup_entity_set(terms, args.eset)
        if not results:
            print(f'EntitySet "{args.eset}" не найден в словаре')
        else:
            for code, entry in results:
                dn = entry.get('display_name', '')
                cn = entry.get('collection_name', '')
                print(f'{code}  ({entry.get("entity_set","")})  {dn} / {cn}')
                for fc, fru in entry.get('properties', {}).items():
                    print(f'  {fc:<40} = {fru}')
        return

    query = args.entity or args.query
    if not query:
        parser.print_help()
        return

    entity_hits, prop_hits = search_all(terms, query)

    if args.entity:
        prop_hits = []

    if not entity_hits and not prop_hits:
        print(f'Ничего не найдено по запросу "{query}"')
        return

    if entity_hits:
        print(f'Сущности ({len(entity_hits)}):')
        for code, eset, dn, cn in entity_hits:
            label = dn or cn or code
            api = f'  →  EntitySet: {eset}' if eset else ''
            print(f'  {code:<40} "{label}"{api}')

    if prop_hits:
        print(f'\nПоля ({len(prop_hits)}):')
        for code, eset, fc, fru in prop_hits:
            api = eset or code
            print(f'  [{api}] {fc:<35} = "{fru}"')


if __name__ == '__main__':
    main()
