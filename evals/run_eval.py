"""Retrieval eval: does the right note come back for a question worded differently
from the note? Compares Engram (embeddings) with keyword search (SQLite FTS5 / BM25)
on the same notes. Uses a throwaway index, so your real index is untouched.

Run: ~/.local/share/engram/venv/bin/python evals/run_eval.py
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
work = Path(tempfile.mkdtemp(prefix="engram-eval-"))
os.environ["RAG_DB"] = str(work / "index.db")
os.environ["ENGRAM_CONFIG"] = str(work / "sources.json")
sys.path.insert(0, str(HERE.parent))
import rag  # noqa: E402  (imported after the env points at the temp index)

data = json.loads((HERE / "dataset.json").read_text())
corpus = work / "notes"
corpus.mkdir()
for name, text in data["notes"].items():
    (corpus / name).write_text(text)

rag.add_source("folder", "eval", str(corpus))
rag.sync()

# Keyword baseline over the same notes.
kw = sqlite3.connect(":memory:")
kw.execute("CREATE VIRTUAL TABLE notes USING fts5(name, body)")
for name, text in data["notes"].items():
    kw.execute("INSERT INTO notes VALUES (?, ?)", (name, text))


def keyword_rank(query: str) -> list[str]:
    terms = " OR ".join(re.findall(r"[a-z0-9]+", query.lower()))
    rows = kw.execute("SELECT name FROM notes WHERE notes MATCH ? ORDER BY bm25(notes)", (terms,))
    return [r[0] for r in rows]


def engram_rank(query: str) -> list[str]:
    ranked = []
    for hit in rag.search(query, k=10):
        name = hit["doc_id"].split(":", 1)[1]
        if name not in ranked:
            ranked.append(name)
    return ranked


def score(rank_fn) -> dict:
    hit1 = hit3 = rr = 0.0
    for query, expected in data["queries"]:
        ranked = rank_fn(query)
        pos = ranked.index(expected) + 1 if expected in ranked else None
        hit1 += pos == 1
        hit3 += bool(pos and pos <= 3)
        rr += 1 / pos if pos else 0
    n = len(data["queries"])
    return {"hit@1": round(hit1 / n, 2), "hit@3": round(hit3 / n, 2), "MRR": round(rr / n, 2)}


results = {"queries": len(data["queries"]), "keyword (BM25)": score(keyword_rank), "engram": score(engram_rank)}
print(json.dumps(results, indent=2))
(HERE / "results.json").write_text(json.dumps(results, indent=2) + "\n")
