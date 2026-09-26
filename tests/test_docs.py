"""Documentation integrity: every relative link resolves, the replay is self-contained, and the
truthfulness statements the docs rely on are present. Runs offline (no network)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MD_FILES = sorted(p for p in REPO.rglob("*.md") if ".venv" not in p.parts and ".pytest_cache" not in p.parts)
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)")
ALLOWED_EXTERNAL = ("https://github.com/herui03/merchant-onboarding-change-lab",)


def _links(path: Path):
    for m in LINK_RE.finditer(path.read_text(encoding="utf-8")):
        yield m.group(1) or m.group(2)


@pytest.mark.req("REQ-NFR-06")
@pytest.mark.parametrize("md", MD_FILES, ids=[str(p.relative_to(REPO)) for p in MD_FILES])
def test_markdown_links_resolve(md):
    for target in _links(md):
        if target.startswith(("http://", "https://")):
            assert target.startswith(ALLOWED_EXTERNAL), f"{md.name}: unexpected external link {target}"
            continue
        if target.startswith(("#", "mailto:")):
            continue
        resolved = (md.parent / target.split("#")[0]).resolve()
        assert resolved.exists(), f"{md.relative_to(REPO)} links to missing {target}"
        assert REPO in resolved.parents or resolved == REPO, f"{md.name}: link leaves the repository: {target}"


def test_readme_clone_command_is_branch_aware():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "git clone --branch claude/dazzling-mendel-1zti2z https://github.com/herui03/merchant-onboarding-change-lab.git" in readme


@pytest.mark.req("REQ-NFR-04")
def test_replay_is_self_contained_and_labelled():
    replay = REPO / "replay" / "case_study_replay.html"
    if not replay.exists():
        pytest.skip("replay not built yet (python -m moblab build-replay)")
    html = replay.read_text(encoding="utf-8")
    assert "RECORDED REPLAY" in html and "Not the live workflow" in html
    assert not re.search(r'(src|href)\s*=\s*"(https?:)?//', html), "replay must not load external resources"
    assert "<script" not in html.lower()
    assert "External UAT" in html and "Not performed" in html
    assert replay.stat().st_size < 16 * 1024 * 1024


def test_truthfulness_statements_present():
    scope = (REPO / "docs" / "00_scope_and_truthfulness.md").read_text(encoding="utf-8")
    for phrase in ("None come from interviews", "developer acceptance checks", "No external business tester",
                   "not** legal or regulatory thresholds", "Business-date authority"):
        assert phrase.lower() in scope.lower(), phrase
    for path in (REPO / "README.md", REPO / "docs" / "07_release_recommendation.md"):
        if path.exists():
            text = path.read_text(encoding="utf-8").lower()
            assert "external uat" in text and ("not performed" in text or "has not" in text), path.name


def test_no_runtime_state_or_secrets_tracked():
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout.split()
    assert not [f for f in tracked if f.startswith("instance/") or f.endswith((".db", ".db-wal", ".db-shm", "secret_key"))]
    assert not [f for f in tracked if f.startswith(".venv/")]
