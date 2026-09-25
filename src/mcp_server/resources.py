import re
from dataclasses import dataclass
from pathlib import Path

from mcp.server.mcpserver import MCPServer

DESCRIPTION_LINE = re.compile(r"^description:\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class DomainGuide:
    name: str
    description: str
    body: str


def load_domain_guides(skills_dir: Path) -> dict[str, DomainGuide]:
    """Справочники доменов DRX из .claude/skills/rxapi-*/SKILL.md (только SKILL.md — без скриптов и словарей)."""
    guides: dict[str, DomainGuide] = {}
    for skill_file in sorted(Path(skills_dir).glob("rxapi-*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        name = skill_file.parent.name.removeprefix("rxapi-")
        match = DESCRIPTION_LINE.search(text)
        guides[name] = DomainGuide(name=name, description=match.group(1).strip() if match else name, body=text)
    return guides


def _register_guide(mcp: MCPServer, guide: DomainGuide) -> None:
    @mcp.resource(
        f"drx://domains/{guide.name}",
        name=f"domain-{guide.name}",
        description=guide.description,
        mime_type="text/markdown",
    )
    def read_guide() -> str:
        return guide.body


def register(mcp: MCPServer, guides: dict[str, DomainGuide]) -> None:
    for guide in guides.values():
        _register_guide(mcp, guide)
