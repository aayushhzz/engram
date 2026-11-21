"""Self-retrieval check on the user's real index.

For a sample of indexed notes, take the note's own title as the query and check
that the note comes back in the top results. This measures whether search can
find each note by its own topic, on real data. It prints only aggregate numbers,
never note text.

Run: ~/.local/share/engram/venv/bin/python evals/vault_selfcheck.py [sample_size]
"""
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rag  # noqa: E402

sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else 100
db = sqlite3.connect(rag.DB_PATH)
rows = db.execute(
    "SELECT id, title FROM documents WHERE source='obsidian' AND length(text) > 200"
).fetchall()
db.close()
if not rows:
    sys.exit("no obsidian documents indexed; run rag.py sync first")

random.seed(7)  # fixed sample, so runs are comparable
sample = random.sample(rows, min(sample_size, len(rows)))

hit1 = hit5 = rr = 0.0
for doc_id, title in sample:
    query = title.split(" - ", 1)[-1]  # drop a leading date prefix if present
    hits = rag.search(query, k=5)
    ids = [h["doc_id"] for h in hits]
    if doc_id in ids:
        rank = ids.index(doc_id) + 1
        rr += 1 / rank
        hit5 += 1
        hit1 += rank == 1

n = len(sample)
print({
    "notes_sampled": n,
    "indexed_obsidian_docs": len(rows),
    "found_first": round(hit1 / n, 3),
    "found_in_top5": round(hit5 / n, 3),
    "mrr": round(rr / n, 3),
})
