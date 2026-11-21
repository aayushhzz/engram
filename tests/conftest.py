"""Test setup: point Engram at throwaway paths and replace Ollama with a fake
embedder, so the suite runs offline and never touches the real index."""
import hashlib
import math
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_tmp = Path(tempfile.mkdtemp(prefix="engram-tests-"))
os.environ["ENGRAM_HOME"] = str(_tmp)
os.environ["RAG_DB"] = str(_tmp / "index.db")
os.environ["ENGRAM_CONFIG"] = str(_tmp / "sources.json")
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
import rag  # noqa: E402

DIM = 768


def fake_embed(texts, kind):
    """Bag-of-words hashing: texts sharing words get similar vectors."""
    out = []
    for text in texts:
        vec = [0.0] * DIM
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        out.append([x / norm for x in vec])
    return out


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Fresh index and config for every test; fake embedder everywhere."""
    for p in (rag.DB_PATH, rag.CONFIG):
        if p.exists():
            p.unlink()
    monkeypatch.setattr(rag, "embed", fake_embed)
    yield
