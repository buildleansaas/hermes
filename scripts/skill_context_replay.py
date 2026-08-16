#!/usr/bin/env python3
"""Measure fixed-prompt and retrieve-then-load skill context budgets.

This is intentionally bounded: it evaluates a frozen task file, emits JSON, and
never mutates canonical skills. Use it before/after selective-context changes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from agent.prompt_builder import build_skills_system_prompt, clear_skills_system_prompt_cache
from agent.skill_catalog import ensure_skill_catalog, search_skill_catalog
from tools.skills_tool import _slice_markdown_content


def _budget(text: str) -> dict[str, int]:
    encoded = text.encode("utf-8")
    return {
        "characters": len(text),
        "bytes": len(encoded),
        "estimated_tokens": (len(encoded) + 3) // 4,
    }


def evaluate(tasks: list[dict[str, Any]], *, top_k: int = 5) -> dict[str, Any]:
    clear_skills_system_prompt_cache(clear_snapshot=True)
    full_prompt = build_skills_system_prompt()
    names_only_prompt = build_skills_system_prompt(
        compact_categories=frozenset({"*"})
    )
    full_budget = _budget(full_prompt)
    names_budget = _budget(names_only_prompt)
    reduction = (
        0.0
        if full_budget["bytes"] == 0
        else 100.0 * (full_budget["bytes"] - names_budget["bytes"]) / full_budget["bytes"]
    )

    catalog = ensure_skill_catalog()
    task_results: list[dict[str, Any]] = []
    section_reductions: list[float] = []
    for task in tasks:
        search = search_skill_catalog(task["query"], limit=top_k)
        matches = search["results"]
        expected = task["expected_skill"]
        top_names = [result["name"] for result in matches]
        hit = expected in top_names
        expected_result = next(
            (result for result in matches if result["name"] == expected), None
        )
        row: dict[str, Any] = {
            "id": task.get("id"),
            "query": task["query"],
            "expected_skill": expected,
            "top_matches": top_names,
            "top_k_hit": hit,
        }

        section = task.get("expected_section")
        if expected_result and section:
            content = Path(expected_result["path"]).read_text(encoding="utf-8")
            selected, slice_info = _slice_markdown_content(content, section=section)
            if selected is None:
                row["section_load"] = {
                    "success": False,
                    "section": section,
                    "error": slice_info["error"],
                }
            else:
                full_bytes = len(content.encode("utf-8"))
                selected_bytes = len(selected.encode("utf-8"))
                section_reduction = (
                    0.0
                    if full_bytes == 0
                    else 100.0 * (full_bytes - selected_bytes) / full_bytes
                )
                section_reductions.append(section_reduction)
                row["section_load"] = {
                    "success": True,
                    "section": slice_info["section"],
                    "source_range": slice_info["source_range"],
                    "full_skill_bytes": full_bytes,
                    "selected_section_bytes": selected_bytes,
                    "byte_reduction_percent": round(section_reduction, 2),
                }
        task_results.append(row)

    hit_count = sum(1 for row in task_results if row["top_k_hit"])
    return {
        "prompt_budget": {
            "full": full_budget,
            "names_only": names_budget,
            "byte_reduction_percent": round(reduction, 2),
        },
        "catalog": catalog,
        "replay": {
            "tasks": task_results,
            "task_count": len(task_results),
            "top_k": top_k,
            "top_k_hits": hit_count,
            "top_k_hit_rate": round(hit_count / len(task_results), 4) if task_results else 0.0,
            "average_section_byte_reduction_percent": (
                round(mean(section_reductions), 2) if section_reductions else None
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tasks",
        type=Path,
        default=Path("benchmarks/selective_context_skill_replay.json"),
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
    report = evaluate(tasks, top_k=max(1, min(args.top_k, 20)))
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
