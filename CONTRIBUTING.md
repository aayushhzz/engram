# Contributing to Engram

Thanks for helping. Engram is small on purpose: one SQLite file, one embedding model, one MCP server.

## Set up

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

The tests replace Ollama with a deterministic fake embedder, so they run offline.

## Before you open a pull request

1. `pytest tests -q` passes.
2. New behaviour has a test. External calls (Ollama, `gh`, `osascript`, HTTP) are stubbed the same way the existing tests do it.
3. No personal data, real tokens or local paths in code, tests, docs or fixtures. Use fictional examples.
4. Docs match the code. If you change a command, update `README.md`, `INSTALL.md` and `llm.txt`.

## Adding a source type

See section 10 of [`llm.txt`](llm.txt). In short: write a generator in `connectors.py` that yields `{key, title, text, url}`, register it in `CONNECTORS`, add an `add` branch in `rag.py`, and add tests.

## Reporting problems

Open an issue with: your macOS or Linux version, `rag.py doctor` output (remove any paths you don't want public), and the exact error text.
