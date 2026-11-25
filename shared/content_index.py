"""Read side of My Content: small, cacheable answers instead of whole-library polls.

A remote screen needs three things and each is cheap here:

* `summary()`  - counts per agent and type, what needs attention, one revision.
* `changes()`  - rows written after a cursor, with removals as tombstones. A new
                 screen walks this once to build its index; afterwards it only
                 asks for what moved.
* `groups()`   - the newest few items per agent, in one query, for the overview.

Every answer carries the catalog `revision`. The API turns it into an ETag, so
asking "has anything changed?" costs a 304 with no body.
"""
from __future__ import annotations

import hashlib
import json

from shared.content_library import COLLECTION, ContentLibrary


def _source_clause(allowed_sources):
    if allowed_sources is None:
        return "", []
    ordered = sorted(set(allowed_sources))
    if not ordered:
        return "0", []
    return "source IN (" + ",".join("?" for _ in ordered) + ")", ordered


def _filters(q="", kind="", source="", collection="", allowed_sources=None):
    where, args = ["deleted=0"], []
    hidden = ContentLibrary.disabled_sources()
    if hidden:
        where.append("source NOT IN (" + ','.join('?' for _ in hidden) + ")")
        args.extend(hidden)
    source_sql, source_args = _source_clause(allowed_sources)
    if source_sql:
        where.append(source_sql)
        args.extend(source_args)
    for key, value in (("kind", kind), ("source", source), ("collection", collection)):
        if value:
            where.append((COLLECTION if key == "collection" else key) + "=?")
            args.append(value)
    if q:
        where.append("(instr(lower(title),lower(?)) OR instr(lower(labels),lower(?)) OR instr(lower(source_labels),lower(?)) OR instr(lower(annotation),lower(?)) OR instr(lower(source_text),lower(?)))")
        args.extend([q[:300]] * 5)
    return " WHERE " + " AND ".join(where), args


def etag(*parts) -> str:
    """A weak validator over values that decide whether a cached answer is still right."""
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]
    return 'W/"' + digest + '"'


def revision(library: ContentLibrary) -> int:
    with library.connect() as db:
        return db.execute("SELECT revision FROM clock WHERE id=1").fetchone()[0]


def summary(library: ContentLibrary, *, allowed_sources=None) -> dict:
    source_sql, source_args = _source_clause(allowed_sources)
    live_where = "WHERE deleted=0" + (" AND " + source_sql if source_sql else "")
    with library.connect() as db:
        db.execute("BEGIN")
        hidden = library.disabled_sources(db)
        if hidden:
            live_where += " AND source NOT IN (" + ','.join('?' for _ in hidden) + ")"
            source_args.extend(hidden)
        current = db.execute("SELECT revision FROM clock WHERE id=1").fetchone()[0]
        total = db.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(byte_size),0) AS bytes FROM content {live_where}", source_args).fetchone()
        sources = [dict(row) for row in db.execute(
            f"""SELECT source, COUNT(*) AS count, COALESCE(SUM(byte_size),0) AS bytes, MAX(updated) AS latest
               FROM content {live_where} GROUP BY source ORDER BY MAX(updated) DESC""", source_args)]
        kinds = {row["kind"]: row["n"] for row in db.execute(f"SELECT kind, COUNT(*) AS n FROM content {live_where} GROUP BY kind", source_args)}
        collections = [dict(row) for row in db.execute(
            f"""SELECT {COLLECTION} AS name, COUNT(*) AS count FROM content
                {live_where} AND {COLLECTION}!='' GROUP BY name ORDER BY name LIMIT 200""", source_args)]
        states = {row[0]: row[1] for row in db.execute(f"SELECT organization_state, COUNT(*) FROM content {live_where} GROUP BY organization_state", source_args)}
        worker = db.execute("SELECT value FROM content_state WHERE key='worker'").fetchone() if _has_table(db, "content_state") else None
    state = json.loads(worker[0]) if worker else {"state": "waiting"}
    if allowed_sources is not None:
        state.pop("scan_errors", None)
    return {
        "revision": current, "total": total["n"], "bytes": total["bytes"], "sources": sources, "kinds": kinds,
        "collections": collections,
        "attention": {"pending": states.get("pending", 0), "waiting_model": states.get("waiting_model", 0),
                      "needs_attention": states.get("failed", 0) + states.get("missing", 0)},
        "worker": {key: state.get(key) for key in ("state", "message", "last_scan", "scan_errors") if key in state},
    }


def _has_table(db, name) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def changes(library: ContentLibrary, after: int = 0, limit: int = 500, *, allowed_sources=None) -> dict:
    """Rows written after `after`. The first walk (`after=0`) skips removals: there is nothing to drop yet."""
    after = max(0, int(after))
    limit = min(1000, max(1, int(limit)))
    source_sql, source_args = _source_clause(allowed_sources)
    extra_where = (" AND deleted=0" if after == 0 else "") + (" AND " + source_sql if source_sql else "")
    with library.connect() as db:
        db.execute("BEGIN")
        hidden = library.disabled_sources(db)
        if hidden:
            extra_where += " AND (deleted=1 OR source NOT IN (" + ','.join('?' for _ in hidden) + "))"
            source_args.extend(hidden)
        current = db.execute("SELECT revision FROM clock WHERE id=1").fetchone()[0]
        rows = db.execute(
            "SELECT * FROM content WHERE revision>?" + extra_where + " ORDER BY revision LIMIT ?",
            (after, *source_args, limit + 1),
        ).fetchall()
    more = len(rows) > limit
    items = [ContentLibrary.compact(ContentLibrary.public(row)) for row in rows[:limit]]
    return {"items": items, "cursor": items[-1]["revision"] if items else after, "revision": current, "has_more": more}


def listing(library: ContentLibrary, *, q="", kind="", source="", collection="", offset=0, limit=60, sort="updated", allowed_sources=None) -> dict:
    result = library.list(q=q, kind=kind, source=source, collection=collection, offset=offset, limit=limit, sort=sort, allowed_sources=allowed_sources)
    return {"items": [ContentLibrary.compact(item) for item in result["items"]], "total": result["total"],
            "offset": result["offset"], "has_more": result["has_more"], "revision": result["revision"]}


def groups(library: ContentLibrary, *, per=6, q="", kind="", collection="", allowed_sources=None) -> dict:
    """Newest `per` items for every agent, plus each agent's matching total, in two reads."""
    per = min(24, max(1, int(per)))
    clause, args = _filters(q, kind, "", collection, allowed_sources=allowed_sources)
    with library.connect() as db:
        db.execute("BEGIN")
        current = db.execute("SELECT revision FROM clock WHERE id=1").fetchone()[0]
        counts = {row["source"]: row["n"] for row in db.execute(f"SELECT source, COUNT(*) AS n FROM content{clause} GROUP BY source", args)}
        rows = db.execute(
            f"""SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY source ORDER BY updated DESC, id) AS rank
                FROM content{clause}) WHERE rank<=? ORDER BY updated DESC, id""", [*args, per]).fetchall()
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["source"], []).append(ContentLibrary.compact(ContentLibrary.public(row)))
    ordered = sorted(grouped, key=lambda source: -grouped[source][0]["updated"])
    return {"revision": current, "groups": [{"source": source, "count": counts.get(source, 0), "items": grouped[source]} for source in ordered]}
