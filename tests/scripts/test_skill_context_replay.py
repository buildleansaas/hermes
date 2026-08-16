from scripts.skill_context_replay import evaluate


def _write_skill(root, category, name, description, body):
    skill_dir = root / "skills" / category / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n",
        encoding="utf-8",
    )


def test_evaluate_reports_prompt_and_section_budgets(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    verbose = "A detailed release procedure. " * 20
    _write_skill(
        tmp_path,
        "devops",
        "release-ops",
        verbose,
        "## Vercel verification\nCheck the alias.\n\n## Rollback\nRestore it.",
    )
    _write_skill(
        tmp_path,
        "writing",
        "copy-editing",
        "A detailed copy editing procedure. " * 20,
        "## Improve prose\nEdit the draft.",
    )

    report = evaluate(
        [
            {
                "id": "release",
                "query": "Vercel verification",
                "expected_skill": "release-ops",
                "expected_section": "Vercel verification",
            }
        ],
        top_k=3,
    )

    assert report["catalog"]["indexed_skills"] == 2
    assert report["prompt_budget"]["names_only"]["bytes"] < report["prompt_budget"]["full"]["bytes"]
    assert report["prompt_budget"]["byte_reduction_percent"] > 0
    assert report["replay"]["top_k_hit_rate"] == 1.0
    section = report["replay"]["tasks"][0]["section_load"]
    assert section["success"] is True
    assert section["selected_section_bytes"] < section["full_skill_bytes"]
