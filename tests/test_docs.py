from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def documentation_files() -> list[Path]:
    return [
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / ".github").rglob("*.md"),
    ]


def test_local_markdown_links_resolve():
    broken: list[str] = []
    for document in documentation_files():
        for target in LINK.findall(document.read_text(encoding="utf-8")):
            value = target.strip().strip("<>")
            if not value or value.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path_text = value.split("#", 1)[0]
            if path_text and not (document.parent / path_text).resolve().exists():
                broken.append(f"{document.relative_to(ROOT)} -> {target}")
    assert broken == []


def test_open_source_community_files_exist():
    required = (
        "README.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "CHANGELOG.md",
        ".github/CODEOWNERS",
        ".github/pull_request_template.md",
        ".github/workflows/ci.yml",
    )
    assert all((ROOT / value).is_file() for value in required)
