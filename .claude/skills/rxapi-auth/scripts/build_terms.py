#!/usr/bin/env python3
"""
Собирает entity_terms.json из resx-ресурсов Directum RX / Aura.

Источники:
  aura_resx/*System.ru.resx  — названия сущностей и полей на русском
  metadata.xml               — маппинг типов в EntitySet API

Результат:
  .claude/skills/rxapi-auth/entity_terms.json

Использование:
  python .claude/skills/rxapi-auth/scripts/build_terms.py
  python .claude/skills/rxapi-auth/scripts/build_terms.py --resx путь/к/resx
"""

import sys, os, re, json, argparse, xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding='utf-8')

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT  = os.path.normpath(os.path.join(_HERE, '..', '..', '..', '..'))

DEFAULT_RESX  = os.path.join(ROOT, 'aura_resx')
METADATA_FILE = os.path.join(ROOT, 'metadata.xml')
OUTPUT_FILE   = os.path.join(_HERE, '..', 'entity_terms.json')

NS_EDM = {'edm': 'http://docs.oasis-open.org/odata/ns/edm'}


def load_entity_sets(metadata_path):
    """Строит маппинг base_type_name -> EntitySet из metadata.xml."""
    tree = ET.parse(metadata_path)
    mroot = tree.getroot()
    entity_sets = {}
    for schema in mroot.findall('.//edm:Schema', NS_EDM):
        if schema.get('Namespace') == 'Sungero.IntegrationService':
            container = schema.find('edm:EntityContainer', NS_EDM)
            if container is not None:
                for es in container.findall('edm:EntitySet', NS_EDM):
                    eset_name = es.get('Name')
                    etype = es.get('EntityType', '').split('.')[-1]
                    base = etype.lstrip('I').replace('Dto', '')
                    # Предпочитаем более короткое имя EntitySet при коллизии
                    if base not in entity_sets or len(eset_name) < len(entity_sets[base]):
                        entity_sets[base] = eset_name
    return entity_sets


def parse_system_resx(path):
    """Читает *System.ru.resx и возвращает display_name, collection_name, properties."""
    try:
        root = ET.parse(path).getroot()
    except Exception as e:
        print(f'  Ошибка парсинга {os.path.basename(path)}: {e}', file=sys.stderr)
        return None

    entry = {'properties': {}}
    for data in root.findall('data'):
        name = data.get('name', '')
        val  = data.find('value')
        if val is None or not val.text:
            continue
        text = val.text.strip()

        if name == 'DisplayName':
            entry['display_name'] = text
        elif name == 'CollectionDisplayName':
            entry['collection_name'] = text
        elif name.startswith('Property_'):
            field_code = name[9:]
            # Пропускаем GUID-подобные имена полей
            if re.match(r'^[A-Za-z]\w{0,49}$', field_code) and not re.search(r'[0-9a-f]{8}', field_code):
                entry['properties'][field_code] = text

    return entry


def build(resx_dir, metadata_path, output_path):
    print(f'Метаданные:  {metadata_path}')
    print(f'Ресурсы:     {resx_dir}')
    print(f'Результат:   {output_path}')
    print()

    entity_sets = load_entity_sets(metadata_path)
    print(f'EntitySets в metadata.xml: {len(entity_sets)}')

    system_files = sorted(
        f for f in os.listdir(resx_dir) if f.endswith('System.ru.resx')
    )
    print(f'System.ru.resx файлов:     {len(system_files)}')

    result = {}
    for fname in system_files:
        entity_code = fname.replace('System.ru.resx', '')
        entry = parse_system_resx(os.path.join(resx_dir, fname))
        if not entry:
            continue

        eset = entity_sets.get(entity_code)
        if eset:
            entry['entity_set'] = eset

        if entry.get('display_name') or entry.get('entity_set') or entry['properties']:
            result[entity_code] = entry

    with_eset  = sum(1 for e in result.values() if 'entity_set' in e)
    total_props = sum(len(e['properties']) for e in result.values())

    print(f'Сущностей распознано:      {len(result)}')
    print(f'  из них с EntitySet:      {with_eset}')
    print(f'  всего свойств:           {total_props}')

    output_path = os.path.normpath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f'\nСохранено: {output_path}')


def main():
    parser = argparse.ArgumentParser(description='Сборка словаря терминов Directum RX', epilog=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--resx', default=DEFAULT_RESX, metavar='DIR',
                        help=f'Папка с resx-файлами (по умолчанию: {DEFAULT_RESX})')
    parser.add_argument('--metadata', default=METADATA_FILE, metavar='FILE',
                        help='Путь к metadata.xml')
    parser.add_argument('--output', default=OUTPUT_FILE, metavar='FILE',
                        help='Куда сохранить entity_terms.json')
    args = parser.parse_args()

    if not os.path.isdir(args.resx):
        print(f'Ошибка: папка resx не найдена: {args.resx}', file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.metadata):
        print(f'Ошибка: metadata.xml не найден: {args.metadata}', file=sys.stderr)
        sys.exit(1)

    build(args.resx, args.metadata, args.output)


if __name__ == '__main__':
    main()
