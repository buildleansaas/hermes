import json

from agent.skill_catalog import ensure_skill_catalog, search_skill_catalog


def _write_skill(root, category, directory, *, name=None, description="", body=""):
    skill_dir = root / "skills" / category / directory
    skill_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = ["---"]
    if name is not None:
        frontmatter.append(f"name: {name}")
    if description:
        frontmatter.append(f"description: {description}")
    frontmatter.extend(["---", ""])
    (skill_dir / "SKILL.md").write_text("\n".join(frontmatter) + body)
    return skill_dir / "SKILL.md"


def test_search_indexes_full_metadata_and_headings(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_skill(
        tmp_path,
        "devops",
        "release-ops",
        name="release-ops",
        description="Ship web applications safely",
        body="# Release Operations\n\n## Vercel verification\nCheck the alias.\n",
    )
    _write_skill(
        tmp_path,
        "writing",
        "copy-editing",
        name="copy-editing",
        description="Improve prose",
        body="# Copy editing\n",
    )

    payload = search_skill_catalog("Vercel verification", limit=5)

    assert payload["results"][0]["name"] == "release-ops"
    assert payload["results"][0]["matched_headings"][0]["title"] == "Vercel verification"
    assert payload["results"][0]["full_content_bytes"] > 0
    assert payload["catalog"]["indexed_skills"] == 2


def test_catalog_rebuilds_only_when_manifest_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    skill = _write_skill(
        tmp_path,
        "tools",
        "search",
        name="search",
        description="Find things",
        body="# Search\n",
    )

    first = ensure_skill_catalog()
    second = ensure_skill_catalog()
    skill.write_text(skill.read_text() + "\n## Advanced query syntax\n")
    third = ensure_skill_catalog()

    assert first["rebuilt"] is True
    assert second["rebuilt"] is False
    assert third["rebuilt"] is True
    assert search_skill_catalog("Advanced query syntax")["results"][0]["name"] == "search"


def test_malformed_skills_and_duplicate_names_remain_discoverable(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_skill(
        tmp_path,
        "alpha",
        "shared-a",
        name="shared",
        description="First duplicate",
        body="# Alpha procedure\n",
    )
    _write_skill(
        tmp_path,
        "beta",
        "shared-b",
        name="shared",
        description="Second duplicate",
        body="# Beta procedure\n",
    )
    malformed = tmp_path / "skills" / "broken" / "odd-skill"
    malformed.mkdir(parents=True)
    (malformed / "SKILL.md").write_text("not yaml\n# Recovery procedure\n")

    duplicates = search_skill_catalog("duplicate", limit=10)["results"]
    recovery = search_skill_catalog("Recovery procedure", limit=10)["results"]

    assert {result["description"] for result in duplicates} == {
        "First duplicate",
        "Second duplicate",
    }
    assert all(result["ambiguous_name"] for result in duplicates)
    assert {result["load_name"] for result in duplicates} == {
        "alpha/shared-a",
        "beta/shared-b",
    }
    assert recovery[0]["name"] == "odd-skill"
