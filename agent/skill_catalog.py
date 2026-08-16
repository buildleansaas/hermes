"""Lossless SQLite FTS5 catalog for installed Hermes skills.

The catalog stores metadata and Markdown headings only. Canonical SKILL.md files
remain the source of truth and are loaded on demand through ``skill_view``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from agent.skill_utils import get_all_skills_dirs, iter_skill_index_files, parse_frontmatter
from hermes_constants import get_hermes_home

_TOKEN_RE = re.compile(r"[\w+-]+", re.UNICODE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _catalog_path() -> Path:
    return get_hermes_home() / "cache" / "skill_catalog.sqlite3"


def _normalise_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _skill_record(skill_md: Path, root: Path, root_priority: int) -> dict[str, Any]:
    content = skill_md.read_text(encoding="utf-8")
    try:
        frontmatter, body = parse_frontmatter(content)
    except Exception:
        frontmatter, body = {}, content
    if not isinstance(frontmatter, dict):
        frontmatter = {}

    rel = skill_md.relative_to(root)
    skill_dir_parts = rel.parts[:-1]
    fallback_name = skill_md.parent.name if skill_md.name == "SKILL.md" else skill_md.stem
    name = str(frontmatter.get("name") or fallback_name).strip() or fallback_name
    category = "/".join(skill_dir_parts[:-1]) or "general"

    metadata = frontmatter.get("metadata")
    hermes_meta = metadata.get("hermes", {}) if isinstance(metadata, dict) else {}
    if not isinstance(hermes_meta, dict):
        hermes_meta = {}
    tags = _normalise_list(hermes_meta.get("tags") or frontmatter.get("tags"))

    headings: list[dict[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        match = _HEADING_RE.match(line)
        if match:
            headings.append(
                {
                    "level": len(match.group(1)),
                    "title": match.group(2).strip(),
                    "line": line_number,
                }
            )

    stat = skill_md.stat()
    source_id = hashlib.sha256(str(skill_md.resolve()).encode("utf-8")).hexdigest()[:20]
    return {
        "source_id": f"skill:{source_id}",
        "name": name,
        "qualified_name": "/".join(skill_dir_parts) or name,
        "category": category,
        "description": str(frontmatter.get("description") or "").strip(),
        "tags": tags,
        "headings": headings,
        "path": str(skill_md.resolve()),
        "root": str(root.resolve()),
        "root_priority": root_priority,
        "platforms": _normalise_list(frontmatter.get("platforms")),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "body_bytes": len(body.encode("utf-8")),
    }


def _discover_records() -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    fingerprint_parts: list[str] = []
    seen_paths: set[Path] = set()
    for root_priority, root in enumerate(get_all_skills_dirs()):
        if not root.exists():
            continue
        for skill_md in iter_skill_index_files(root, "SKILL.md"):
            try:
                resolved = skill_md.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                stat = skill_md.stat()
                fingerprint_parts.append(
                    f"{resolved}|{stat.st_mtime_ns}|{stat.st_size}"
                )
                records.append(_skill_record(skill_md, root, root_priority))
            except (OSError, UnicodeError):
                continue
    fingerprint = hashlib.sha256("\n".join(fingerprint_parts).encode("utf-8")).hexdigest()
    return records, fingerprint


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS catalog_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            qualified_name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            headings_json TEXT NOT NULL,
            headings_text TEXT NOT NULL,
            path TEXT NOT NULL,
            root TEXT NOT NULL,
            root_priority INTEGER NOT NULL,
            platforms_json TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            body_bytes INTEGER NOT NULL
        );
        """
    )
    existing = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'skills_fts'"
    ).fetchone()
    if existing and "content='skills'" in (existing["sql"] or ""):
        connection.execute("DROP TABLE skills_fts")
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(
            name,
            qualified_name,
            category,
            description,
            tags,
            headings,
            tokenize='porter unicode61'
        )
        """
    )


def ensure_skill_catalog(*, force: bool = False) -> dict[str, Any]:
    """Refresh the profile-scoped catalog when the skill-file manifest changes."""
    records, fingerprint = _discover_records()
    path = _catalog_path()
    with _connect(path) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT value FROM catalog_meta WHERE key = 'fingerprint'"
        ).fetchone()
        rebuilt = force or row is None or row["value"] != fingerprint
        if rebuilt:
            connection.execute("DELETE FROM skills_fts")
            connection.execute("DELETE FROM skills")
            for record in records:
                cursor = connection.execute(
                    """
                    INSERT INTO skills (
                        source_id, name, qualified_name, category, description,
                        tags_json, headings_json, headings_text, path, root,
                        root_priority, platforms_json, mtime_ns, size,
                        content_sha256, body_bytes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["source_id"],
                        record["name"],
                        record["qualified_name"],
                        record["category"],
                        record["description"],
                        json.dumps(record["tags"], ensure_ascii=False),
                        json.dumps(record["headings"], ensure_ascii=False),
                        "\n".join(item["title"] for item in record["headings"]),
                        record["path"],
                        record["root"],
                        record["root_priority"],
                        json.dumps(record["platforms"], ensure_ascii=False),
                        record["mtime_ns"],
                        record["size"],
                        record["content_sha256"],
                        record["body_bytes"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO skills_fts(
                        rowid, name, qualified_name, category, description, tags, headings
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cursor.lastrowid,
                        record["name"],
                        record["qualified_name"],
                        record["category"],
                        record["description"],
                        " ".join(record["tags"]),
                        "\n".join(item["title"] for item in record["headings"]),
                    ),
                )
            connection.execute(
                "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES('fingerprint', ?)",
                (fingerprint,),
            )
            connection.commit()
        count = connection.execute("SELECT COUNT(*) AS count FROM skills").fetchone()["count"]
    return {
        "path": str(path),
        "fingerprint": fingerprint,
        "indexed_skills": count,
        "rebuilt": rebuilt,
    }


def _fts_expression(query: str) -> str:
    terms = [term for term in _TOKEN_RE.findall(query.lower()) if term]
    return " OR ".join(f'"{term}"*' for term in terms[:12])


def search_skill_catalog(
    query: str,
    *,
    category: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Return ranked metadata/heading matches without loading skill bodies."""
    limit = max(1, min(int(limit), 50))
    metrics = ensure_skill_catalog()
    expression = _fts_expression(query)
    path = Path(metrics["path"])

    with _connect(path) as connection:
        _ensure_schema(connection)
        params: list[Any] = []
        where: list[str] = []
        if expression:
            where.append("skills_fts MATCH ?")
            params.append(expression)
        if category:
            where.append("s.category = ?")
            params.append(category)
        clause = " WHERE " + " AND ".join(where) if where else ""
        rank_sql = (
            "bm25(skills_fts, 8.0, 6.0, 4.0, 3.0, 2.0, 1.0) AS rank"
            if expression
            else "0.0 AS rank"
        )
        join_sql = "JOIN skills_fts ON skills_fts.rowid = s.id" if expression else ""
        rows = connection.execute(
            f"""
            SELECT s.*, {rank_sql}
            FROM skills AS s
            {join_sql}
            {clause}
            ORDER BY rank ASC, s.root_priority ASC, s.category ASC, s.name ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        duplicate_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM skills GROUP BY name HAVING COUNT(*) > 1"
            ).fetchall()
        }

    query_terms = {term.lower() for term in _TOKEN_RE.findall(query)}
    results: list[dict[str, Any]] = []
    for row in rows:
        headings = json.loads(row["headings_json"])
        matched_headings = [
            item
            for item in headings
            if any(term in item["title"].lower() for term in query_terms)
        ][:5]
        results.append(
            {
                "id": row["source_id"],
                "name": row["name"],
                "load_name": row["qualified_name"] if row["name"] in duplicate_names else row["name"],
                "category": row["category"],
                "description": row["description"],
                "tags": json.loads(row["tags_json"]),
                "matched_headings": matched_headings,
                "path": row["path"],
                "content_sha256": row["content_sha256"],
                "full_content_bytes": row["size"],
                "estimated_full_tokens": (row["size"] + 3) // 4,
                "ambiguous_name": row["name"] in duplicate_names,
                "rank": round(float(row["rank"]), 6),
                "platforms": json.loads(row["platforms_json"]),
            }
        )
    return {"results": results, "count": len(results), "catalog": metrics}
