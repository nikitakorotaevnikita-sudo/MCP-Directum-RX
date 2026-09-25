import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

EDM_NS = "{http://docs.oasis-open.org/odata/ns/edm}"

# Наборы с учётками, правами, сертификатами, лицензиями, настройками и аудитом в универсальный слой не пускаем.
DENY_SUBSTRINGS = (
    "login", "user", "certificate", "accessright", "license", "audit",
    "setting", "password", "secret", "token", "session", "permission", "signature", "constant",
)

# Для навигационных свойств маркер "user" не применяем: Author/Performer у стандартных
# заданий и поручений ссылаются на IUserDto/IEmployeeDto — те же данные, что и в разрешённом
# наборе IEmployees, и нужны для типовых фильтров вида Performer/Id eq 63.
NAV_DENY_SUBSTRINGS = tuple(marker for marker in DENY_SUBSTRINGS if marker != "user")

# Свойства с секретами (пароли ящиков, ключи API, токены) закрыты даже в разрешённых наборах.
SENSITIVE_PROPERTY_MARKERS = ("password", "secret", "apikey", "token", "privatekey", "pincode", "thumbprint")

# Легитимные слова, содержащие маркер: вырезаются из имени перед любой проверкой маркеров.
ALLOWED_WORDS = ("secretaries", "secretary")


def normalize_name(name: str) -> str:
    """Имя в нижнем регистре без легитимных слов (Secretary и т.п.) — для сравнения с маркерами."""
    lowered = (name or "").lower()
    for word in ALLOWED_WORDS:
        lowered = lowered.replace(word, "")
    return lowered


def has_marker(name: str, markers: tuple[str, ...]) -> bool:
    normalized = normalize_name(name)
    return any(marker in normalized for marker in markers)


def is_sensitive_property(name: str) -> bool:
    return has_marker(name, SENSITIVE_PROPERTY_MARKERS)


def strip_sensitive(value: Any) -> Any:
    """Рекурсивно убирает ключи-секреты из ответа Directum (в т.ч. во вложенных expand-объектах)."""
    if isinstance(value, dict):
        return {key: strip_sensitive(item) for key, item in value.items() if not is_sensitive_property(str(key))}
    if isinstance(value, list):
        return [strip_sensitive(item) for item in value]
    return value


@dataclass(frozen=True)
class EntityInfo:
    entity_set: str
    entity_type: str
    properties: dict[str, str]
    navigation: dict[str, str]


def parse_metadata(xml_text: str) -> dict[str, EntityInfo]:
    root = ET.fromstring(xml_text)
    types: dict[str, tuple[dict[str, str], dict[str, str], str | None]] = {}
    for schema in root.iter(f"{EDM_NS}Schema"):
        namespace = schema.get("Namespace", "")
        for entity_type in schema.findall(f"{EDM_NS}EntityType"):
            properties = {
                p.get("Name"): p.get("Type")
                for p in entity_type.findall(f"{EDM_NS}Property")
                if not is_sensitive_property(p.get("Name") or "")
            }
            navigation = {n.get("Name"): n.get("Type") for n in entity_type.findall(f"{EDM_NS}NavigationProperty")}
            types[f"{namespace}.{entity_type.get('Name')}"] = (properties, navigation, entity_type.get("BaseType"))

    def collect(type_name: str | None, seen: frozenset[str]) -> tuple[dict[str, str], dict[str, str]]:
        if not type_name or type_name not in types or type_name in seen:
            return {}, {}
        properties, navigation, base = types[type_name]
        base_properties, base_navigation = collect(base, seen | {type_name})
        return {**base_properties, **properties}, {**base_navigation, **navigation}

    entities: dict[str, EntityInfo] = {}
    for entity_set in root.iter(f"{EDM_NS}EntitySet"):
        name, type_name = entity_set.get("Name"), entity_set.get("EntityType")
        properties, navigation = collect(type_name, frozenset())
        entities[name] = EntityInfo(name, type_name, properties, navigation)
    return entities


def is_denied(entity_set: str) -> bool:
    return has_marker(entity_set, DENY_SUBSTRINGS)


def type_leaf(type_name: str) -> str:
    """«Collection(Demo.ILoginDto)» -> «ILoginDto»; «Demo.ILoginDto» -> «ILoginDto»."""
    text = type_name or ""
    if text.startswith("Collection(") and text.endswith(")"):
        text = text[len("Collection(") : -1]
    return text.rsplit(".", 1)[-1]


def is_denied_type(type_name: str) -> bool:
    return has_marker(type_leaf(type_name), DENY_SUBSTRINGS)


def is_denied_navigation_type(type_name: str) -> bool:
    """Как is_denied_type, но без маркера "user" — см. NAV_DENY_SUBSTRINGS."""
    return has_marker(type_leaf(type_name), NAV_DENY_SUBSTRINGS)


class MetadataCache:
    """$metadata одинаков для всех пользователей — скачиваем один раз кредами первого вызвавшего."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entities: dict[str, EntityInfo] | None = None

    def get(self, client: Any) -> dict[str, EntityInfo]:
        with self._lock:
            if self._entities is None:
                self._entities = parse_metadata(client.get_metadata_xml())
            return self._entities
