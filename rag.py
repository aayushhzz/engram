"""Engram: local vector index over your notes and docs.

Sources are configured in sources.json. Each source is read by a connector
(connectors.py), chunked, embedded with Ollama (nomic-embed-text, free, local)
and stored in SQLite with sqlite-vec.

Commands:
  rag.py sync                     update the index from every configured source
  rag.py search "query" [source]  semantic search, optionally one source
  rag.py sources                  list configured sources and their document counts
"""
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import sys
import urllib.request
from pathlib import Path

import sqlite_vec

import connectors

HERE = Path(__file__).parent
ENGRAM_HOME = Path(os.environ.get("ENGRAM_HOME", Path.home() / ".local/share/engram"))
CONFIG = Path(os.environ.get("ENGRAM_CONFIG", HERE / "sources.json"))
DB_PATH = Path(os.environ.get("RAG_DB", ENGRAM_HOME / "index.db"))
MODEL = os.environ.get("RAG_MODEL", "nomic-embed-text")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DIM = 768  # nomic-embed-text output size
MAX_CHARS = 1500


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    # The first version of the index used a different schema. It is rebuilt
    # from the sources, so the old tables are dropped.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='files'").fetchone():
        db.executescript("DROP TABLE IF EXISTS vec; DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS files;")
    db.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT,
            hash TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS documents_source ON documents(source);
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            doc_id TEXT NOT NULL,
            heading TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS vec USING vec0(embedding float[{DIM}]);
        """
    )
    return db


EMBED_BATCH = 32  # big batches crash Ollama's embedding runner (seen with a 628-chunk chat)


def _embed_request(inputs: list[str]) -> list[list[float]]:
    req = urllib.request.Request(
        f"{OLLAMA}/api/embed",
        data=json.dumps({"model": MODEL, "input": inputs}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())["embeddings"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Ollama embed failed (HTTP {e.code}): {detail}") from e


def embed(texts: list[str], kind: str) -> list[list[float]]:
    """Embed texts with Ollama. kind is 'search_document' or 'search_query'; nomic-embed-text
    expects the task prefix on both. Batched, with one retry in case the runner was restarting."""
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [f"{kind}: {t}" for t in texts[i:i + EMBED_BATCH]]
        for attempt in (1, 2):
            try:
                out.extend(_embed_request(batch))
                break
            except RuntimeError:
                if attempt == 2:
                    raise
                time.sleep(3)  # Ollama restarts the runner on the next request
    return out


def chunk(title: str, text: str) -> list[tuple[str, str]]:
    """Split by headings, then by size. Each chunk carries the document title."""
    sections, heading, buf = [], title, []
    for line in text.splitlines():
        if line.startswith("#"):
            if buf:
                sections.append((heading, "\n".join(buf).strip()))
            heading, buf = line.lstrip("#").strip() or title, [line]
        else:
            buf.append(line)
    if buf:
        sections.append((heading, "\n".join(buf).strip()))

    chunks = []
    for h, body in sections:
        for i in range(0, len(body), MAX_CHARS):
            piece = body[i:i + MAX_CHARS]
            if piece.strip():
                chunks.append((h, f"{title}\n{piece}"))
    return chunks


def _delete_doc(db: sqlite3.Connection, doc_id: str) -> None:
    for (cid,) in db.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall():
        db.execute("DELETE FROM vec WHERE rowid=?", (cid,))
    db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))


SECRET_PATTERNS = [
    re.compile(r"xox[a-z][-.][A-Za-z0-9.-]{10,}"),       # Slack tokens (incl. xoxe.xoxp-)
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),           # GitHub tokens
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),                # OpenAI / Anthropic style keys
    re.compile(r"AKIA[0-9A-Z]{16}"),                     # AWS access key IDs
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*\S{8,}"),
]


def redact(text: str) -> str:
    """Mask credentials before they are embedded or stored. Shortcut: regex
    patterns only, so unknown secret formats still need a pattern here."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _upsert(db: sqlite3.Connection, source: str, doc: dict) -> bool:
    """Index one document. Returns False when its text is unchanged."""
    doc = {**doc, "title": redact(doc["title"]), "text": redact(doc["text"])}
    doc_id = f"{source}:{doc['key']}"
    digest = hashlib.sha256(doc["text"].encode()).hexdigest()
    row = db.execute("SELECT hash FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row and row[0] == digest:
        return False

    _delete_doc(db, doc_id)
    chunks = chunk(doc["title"], doc["text"])
    if chunks:
        vectors = embed([t for _, t in chunks], "search_document")
        for (heading, text), vec in zip(chunks, vectors):
            cur = db.execute(
                "INSERT INTO chunks(doc_id, heading, text) VALUES (?, ?, ?)",
                (doc_id, heading, text),
            )
            db.execute(
                "INSERT INTO vec(rowid, embedding) VALUES (?, ?)",
                (cur.lastrowid, sqlite_vec.serialize_float32(vec)),
            )
    db.execute(
        "INSERT INTO documents(id, source, title, url, hash, text) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, source, doc["title"], doc.get("url"), digest, doc["text"]),
    )
    return True


def load_sources() -> list[dict]:
    return _read_config()


def sync() -> dict:
    db = connect()
    report = {}
    for cfg in load_sources():
        name, kind = cfg["name"], cfg["type"]
        stats = {"updated": 0, "unchanged": 0, "removed": 0}
        try:
            docs = list(connectors.CONNECTORS[kind](cfg))
        except Exception as e:
            # Keep the last good index for this source instead of deleting it.
            report[name] = {"error": str(e)}
            continue

        seen = set()
        for doc in docs:
            seen.add(f"{name}:{doc['key']}")
            if _upsert(db, name, doc):
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
            db.commit()

        # Documents that disappeared from the source are removed from the index.
        for (doc_id,) in db.execute("SELECT id FROM documents WHERE source=?", (name,)).fetchall():
            if doc_id not in seen:
                _delete_doc(db, doc_id)
                stats["removed"] += 1
        db.commit()
        report[name] = stats
    db.close()
    return report


def search(query: str, k: int = 5, source: str | None = None) -> list[dict]:
    db = connect()
    qvec = embed([query], "search_query")[0]
    # Over-fetch when filtering by source, since the vector index cannot filter.
    fetch = k * 10 if source else k
    rows = db.execute(
        """
        SELECT d.id, d.source, d.title, d.url, c.heading, c.text, v.distance
        FROM vec v
        JOIN chunks c ON c.id = v.rowid
        JOIN documents d ON d.id = c.doc_id
        WHERE v.embedding MATCH ? AND k = ?
        ORDER BY v.distance
        """,
        (sqlite_vec.serialize_float32(qvec), fetch),
    ).fetchall()
    db.close()
    hits = [
        {"doc_id": i, "source": s, "title": t, "url": u, "heading": h,
         "distance": round(d, 4), "text": x}
        for i, s, t, u, h, x, d in rows
        if source is None or s == source
    ]
    return hits[:k]


def read_doc(doc_id: str) -> str | None:
    db = connect()
    row = db.execute("SELECT text FROM documents WHERE id=?", (doc_id,)).fetchone()
    db.close()
    return row[0] if row else None


def list_sources() -> list[dict]:
    db = connect()
    counts = dict(db.execute("SELECT source, COUNT(*) FROM documents GROUP BY source").fetchall())
    db.close()
    return [{"name": c["name"], "type": c["type"], "documents": counts.get(c["name"], 0)}
            for c in load_sources()]


def _read_config() -> list[dict]:
    return json.loads(CONFIG.read_text())["sources"] if CONFIG.exists() else []


def _write_config(sources: list[dict]) -> None:
    CONFIG.write_text(json.dumps({"sources": sources}, indent=2) + "\n")


def add_source(kind: str, name: str, target: str | None = None) -> dict:
    """Add or replace one source. Nothing is added by default; the user opts in.
    kind: folder (Obsidian vault or any folder of .md/.txt, e.g. Google Docs exports),
          links (a text file of URLs), apple-notes (macOS Notes app)."""
    if kind == "folder":
        path = Path(target or "").expanduser()
        if not path.is_dir():
            raise SystemExit(f"folder not found: {path}")
        source = {"name": name, "type": "folder", "path": str(path)}
        if name == "obsidian":
            source["exclude"] = [".obsidian", ".omx", ".trash", ".git"]
    elif kind == "links":
        path = Path(target or HERE / "inbox" / "urls.txt").expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        source = {"name": name, "type": "urls", "file": str(path)}
    elif kind == "apple-notes":
        source = {"name": name, "type": "apple_notes"}
    elif kind == "github":
        source = {"name": name, "type": "github"}
        if target:
            source["user"] = target
    elif kind in ("claude-chats", "codex-chats"):
        # target is an optional day limit, e.g. "90" to index only recent sessions
        source = {"name": name, "type": kind.replace("-", "_")}
        if target:
            source["days"] = int(target)
    else:
        raise SystemExit(
            f"unknown source type: {kind} (use folder, links, apple-notes, claude-chats or codex-chats)"
        )
    sources = [s for s in _read_config() if s["name"] != name] + [source]
    _write_config(sources)
    return source


def remove_source(name: str) -> bool:
    sources = _read_config()
    kept = [s for s in sources if s["name"] != name]
    _write_config(kept)
    if kept != sources:
        # Drop the indexed documents for this source right away.
        db = connect()
        for (doc_id,) in db.execute("SELECT id FROM documents WHERE source=?", (name,)).fetchall():
            _delete_doc(db, doc_id)
        db.commit()
        db.close()
        return True
    return False


def doctor() -> dict:
    """Health check: Ollama and the embedding model, the index, and each source."""
    report = {"ollama": "ok", "embedding_model": "ok", "index": str(DB_PATH)}
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as r:
            models = [m["name"] for m in json.loads(r.read())["models"]]
        if not any(m.split(":")[0] == MODEL for m in models):
            report["embedding_model"] = f"missing: run `ollama pull {MODEL}`"
    except Exception as e:
        report["ollama"] = f"not reachable: {e}"
    try:
        report["sources"] = list_sources()
    except Exception as e:
        report["sources"] = f"error: {e}"
    return report


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("sync", "index"):
        print(json.dumps(sync(), indent=2))
    elif cmd == "sources":
        print(json.dumps(list_sources(), indent=2))
    elif cmd == "doctor":
        print(json.dumps(doctor(), indent=2))
    elif cmd == "add" and len(sys.argv) >= 4:
        # rag.py add folder obsidian ~/Documents/Obsidian Vault
        # rag.py add folder google-docs ~/Downloads/Google Docs Export
        # rag.py add links doc-links [path/to/urls.txt]
        # rag.py add apple-notes apple-notes
        src = add_source(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
        print(f"added {src['name']} ({src['type']}). Run: rag.py sync")
    elif cmd == "remove" and len(sys.argv) > 2:
        print("removed" if remove_source(sys.argv[2]) else "no such source")
    elif cmd == "search" and len(sys.argv) > 2:
        source = sys.argv[3] if len(sys.argv) > 3 else None
        for hit in search(sys.argv[2], source=source):
            print(f"[{hit['distance']}] {hit['source']} | {hit['title']} > {hit['heading']}")
            print(hit["text"][:300].replace("\n", " "), "\n")
    else:
        print("usage: rag.py add folder|links|apple-notes NAME [PATH] | remove NAME | sync | sources | doctor | search <query> [source]")
