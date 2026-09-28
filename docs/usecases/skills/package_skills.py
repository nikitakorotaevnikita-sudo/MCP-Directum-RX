"""Упаковка навыков для загрузки на платформу: .zip и .skill с SKILL.md в корне архива.

Проверяет требования платформы:
- SKILL.md начинается с YAML-заголовка (между строками «---») с полями name и description;
- архив содержит SKILL.md;
- размер архива не превышает 50 МБ.

Запуск:  python package_skills.py [ИМЯ_НАВЫКА ...] [--out КАТАЛОГ]
Без имён упаковываются все каталоги рядом со скриптом, в которых есть SKILL.md.
Код выхода: 0 — всё собрано, 2 — навык не прошёл проверку (архив не создан), 1 — непредвиденная ошибка.
"""

import argparse
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

SKILLS_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SKILLS_DIR / "dist"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1024
NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
EXTENSIONS = (".zip", ".skill")
# Служебное, что не должно попасть в архив.
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git", "dist", ".venv"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db"}
# Фиксированная дата записей: одинаковые исходники дают одинаковый архив.
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


class SkillError(ValueError):
    """Навык не соответствует требованиям платформы."""


def read_frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError(f"{skill_md.name}: файл должен начинаться с YAML-заголовка — строки «---»")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        raise SkillError(f"{skill_md.name}: YAML-заголовок не закрыт второй строкой «---»") from None
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        hint = " Если в значении есть «: » или «#», заключите значение в кавычки." if "mapping values" in str(exc) else ""
        raise SkillError(f"{skill_md.name}: YAML-заголовок не разбирается: {exc}.{hint}") from None
    if not isinstance(data, dict):
        raise SkillError(f"{skill_md.name}: YAML-заголовок должен быть набором полей «ключ: значение»")
    return data


def validate_skill(skill_dir: Path) -> dict:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillError(f"{skill_dir.name}: нет файла SKILL.md")
    meta = read_frontmatter(skill_md)
    errors = []
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not name.strip():
        errors.append("поле name обязательно и должно быть строкой")
    elif len(name) > MAX_NAME_CHARS or not NAME_PATTERN.match(name):
        errors.append(f"name «{name}»: только строчные латинские буквы, цифры и дефисы, до {MAX_NAME_CHARS} символов")
    elif name != skill_dir.name:
        errors.append(f"name «{name}» должно совпадать с именем каталога «{skill_dir.name}»")
    if not isinstance(description, str) or not description.strip():
        errors.append("поле description обязательно и должно быть строкой")
    elif len(description) > MAX_DESCRIPTION_CHARS:
        errors.append(f"description длиннее {MAX_DESCRIPTION_CHARS} символов ({len(description)})")
    if errors:
        raise SkillError(f"{skill_dir.name}/SKILL.md: " + "; ".join(errors))
    return meta


def skill_files(skill_dir: Path) -> list[Path]:
    files = []
    for path in sorted(skill_dir.rglob("*")):
        relative = path.relative_to(skill_dir)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_file() and path.suffix not in EXCLUDED_SUFFIXES and path.name not in EXCLUDED_NAMES:
            files.append(path)
    return files


def _write_zip(skill_dir: Path, files: list[Path], target: Path) -> None:
    """Атомарно: пишем во временный файл рядом и переименовываем — недописанного архива не остаётся."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(handle)
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(skill_dir).as_posix(), date_time=ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
        check_archive(Path(temp_name))
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def check_archive(archive_path: Path) -> None:
    size = archive_path.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise SkillError(f"{archive_path.name}: {size / 1024 / 1024:.1f} МБ — больше предела 50 МБ")
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if "SKILL.md" not in names:
            raise SkillError(f"{archive_path.name}: в корне архива нет SKILL.md")
        broken = archive.testzip()
        if broken is not None:
            raise SkillError(f"{archive_path.name}: повреждена запись {broken}")
        read_frontmatter_from_bytes(archive.read("SKILL.md"), archive_path.name)


def read_frontmatter_from_bytes(data: bytes, label: str) -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "SKILL.md"
        path.write_bytes(data)
        try:
            read_frontmatter(path)
        except SkillError as exc:
            raise SkillError(f"{label}: {exc}") from None


def package(skill_dir: Path, out_dir: Path = DEFAULT_OUT) -> list[Path]:
    validate_skill(skill_dir)
    files = skill_files(skill_dir)
    built = []
    for extension in EXTENSIONS:
        target = out_dir / f"{skill_dir.name}{extension}"
        _write_zip(skill_dir, files, target)
        built.append(target)
    return built


def discover(root: Path = SKILLS_DIR) -> list[Path]:
    return [path for path in sorted(root.iterdir()) if path.is_dir() and (path / "SKILL.md").is_file()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Упаковка навыков в .zip и .skill")
    parser.add_argument("skills", nargs="*", help="имена каталогов навыков (по умолчанию — все)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="куда положить архивы")
    args = parser.parse_args(argv)
    skill_dirs = [SKILLS_DIR / name for name in args.skills] if args.skills else discover()
    if not skill_dirs:
        print("Не найдено ни одного каталога с SKILL.md", file=sys.stderr)
        return 2
    failed = False
    for skill_dir in skill_dirs:
        try:
            for path in package(skill_dir, Path(args.out)):
                print(f"{path}  ({path.stat().st_size / 1024:.0f} КБ)")
        except SkillError as exc:
            failed = True
            print(f"Ошибка: {exc}", file=sys.stderr)
    return 2 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Непредвиденная ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
