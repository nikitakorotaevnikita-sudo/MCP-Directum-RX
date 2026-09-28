import importlib.util
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "usecases" / "skills" / "package_skills.py"
spec = importlib.util.spec_from_file_location("package_skills", SCRIPT)
package_skills = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_skills)


def make_skill(root: Path, name="demo-skill", frontmatter=None, extra=None) -> Path:
    skill = root / name
    (skill / "scripts").mkdir(parents=True)
    header = frontmatter if frontmatter is not None else f"---\nname: {name}\ndescription: Делает отчёт в PDF и таблицы.\n---\n"
    (skill / "SKILL.md").write_text(header + "\n# Навык\n", encoding="utf-8")
    (skill / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (skill / "scripts" / "__pycache__").mkdir()
    (skill / "scripts" / "__pycache__" / "run.cpython-313.pyc").write_bytes(b"junk")
    for relative, content in (extra or {}).items():
        (skill / relative).write_bytes(content)
    return skill


def test_real_skills_pass_platform_requirements(tmp_path):
    skills = package_skills.discover()

    assert {skill.name for skill in skills} >= {"pdf-report", "excel-report"}
    for skill in skills:
        for archive_path in package_skills.package(skill, tmp_path):
            assert archive_path.stat().st_size <= 50 * 1024 * 1024
            with zipfile.ZipFile(archive_path) as archive:
                assert "SKILL.md" in archive.namelist()
                assert not [n for n in archive.namelist() if "__pycache__" in n or n.endswith(".pyc")]


def test_builds_zip_and_skill_with_skill_md_at_root(tmp_path):
    skill = make_skill(tmp_path / "src")

    built = package_skills.package(skill, tmp_path / "out")

    assert [path.name for path in built] == ["demo-skill.zip", "demo-skill.skill"]
    with zipfile.ZipFile(built[1]) as archive:
        assert sorted(archive.namelist()) == ["SKILL.md", "scripts/run.py"]


def test_archives_are_reproducible(tmp_path):
    skill = make_skill(tmp_path / "src")

    first = package_skills.package(skill, tmp_path / "a")[0].read_bytes()
    second = package_skills.package(skill, tmp_path / "b")[0].read_bytes()

    assert first == second


def test_unquoted_colon_in_description_is_caught_with_hint(tmp_path):
    skill = make_skill(tmp_path, frontmatter="---\nname: demo-skill\ndescription: Делает отчёт: PDF и таблицы.\n---\n")

    with pytest.raises(package_skills.SkillError, match="кавычки"):
        package_skills.validate_skill(skill)


def test_quoted_colon_in_description_is_valid(tmp_path):
    skill = make_skill(tmp_path, frontmatter='---\nname: demo-skill\ndescription: "Делает отчёт: PDF и таблицы."\n---\n')

    assert package_skills.validate_skill(skill)["description"] == "Делает отчёт: PDF и таблицы."


@pytest.mark.parametrize(
    ("frontmatter", "message"),
    [
        ("# Нет заголовка\n", "YAML-заголовка"),
        ("---\nname: demo-skill\ndescription: x\n", "не закрыт"),
        ("---\nname: demo-skill\n---\n", "description"),
        ("---\ndescription: x\n---\n", "name"),
        ("---\nname: Demo Skill\ndescription: x\n---\n", "строчные латинские"),
        ("---\nname: other-name\ndescription: x\n---\n", "совпадать с именем каталога"),
        ("---\nname: demo-skill\ndescription: [x\n---\n", "не разбирается"),
        ("---\n- name\n---\n", "ключ: значение"),
        (f"---\nname: demo-skill\ndescription: {'а' * 1100}\n---\n", "длиннее 1024"),
    ],
)
def test_invalid_frontmatter_rejected_and_no_archive(tmp_path, frontmatter, message):
    skill = make_skill(tmp_path / "src", frontmatter=frontmatter)

    with pytest.raises(package_skills.SkillError, match=message):
        package_skills.package(skill, tmp_path / "out")
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


def test_missing_skill_md_rejected(tmp_path):
    folder = tmp_path / "empty-skill"
    folder.mkdir()

    with pytest.raises(package_skills.SkillError, match="нет файла SKILL.md"):
        package_skills.validate_skill(folder)


def test_oversized_archive_rejected_without_leftovers(tmp_path, monkeypatch):
    monkeypatch.setattr(package_skills, "MAX_ARCHIVE_BYTES", 100)
    skill = make_skill(tmp_path / "src", extra={"big.bin": bytes(range(256)) * 50})
    out = tmp_path / "out"

    with pytest.raises(package_skills.SkillError, match="больше предела 50 МБ"):
        package_skills.package(skill, out)
    assert list(out.iterdir()) == []


def test_cli_exit_codes(tmp_path, capsys):
    assert package_skills.main(["pdf-report", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "pdf-report.skill").is_file()
    assert package_skills.main(["no-such-skill", "--out", str(tmp_path)]) == 2
    assert "нет файла SKILL.md" in capsys.readouterr().err
