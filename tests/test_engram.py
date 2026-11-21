"""Engram unit and integration tests. Run: python -m pytest tests -q"""
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

import connectors
import rag

REAL_EMBED = rag.embed  # captured before the conftest fixture swaps in the fake


# --- redaction -------------------------------------------------------------

@pytest.mark.parametrize("secret", [
    "xox" + "b-1234567890-abcdefghij",  # fake, split so scanners do not flag the literal
    "xox" + "e.xoxp-1-Mi0yLTEzNDc0MzA3MzM4NTYtODg2MTgw",
    "ghp_" + "a" * 36,
    "sk-ant-api03-" + "b" * 40,
    "AKIAABCDEFGHIJKLMNOP",
])
def test_redacts_known_token_formats(secret):
    out = rag.redact(f"deploy with {secret} today")
    assert secret not in out
    assert "[REDACTED]" in out


def test_redacts_key_value_secrets():
    out = rag.redact("api_key = supersecretvalue123")
    assert "supersecretvalue123" not in out


def test_leaves_normal_prose_alone():
    text = "We moved billing to Postgres because of JSONB columns."
    assert rag.redact(text) == text


# --- chunking --------------------------------------------------------------

def test_chunk_splits_on_headings_and_prefixes_title():
    text = "# Kafka\nseven days retention\n## Cost\nbroker disk grew"
    chunks = rag.chunk("Kafka", text)
    headings = [h for h, _ in chunks]
    assert headings == ["Kafka", "Cost"]
    assert all(body.startswith("Kafka\n") for _, body in chunks)


def test_chunk_splits_long_sections_by_size():
    body = "word " * 1000  # about 5000 chars, no headings
    chunks = rag.chunk("Long", body)
    assert len(chunks) > 1
    assert all(len(t) <= rag.MAX_CHARS + len("Long\n") for _, t in chunks)


# --- format extraction -----------------------------------------------------

def test_extract_plain_text(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("hello markdown")
    assert connectors.extract_text(p) == "hello markdown"


def test_extract_html_drops_scripts_and_keeps_text(tmp_path):
    p = tmp_path / "page.html"
    p.write_text("<html><head><script>var x=1</script></head><body><p>Terraform state in S3</p></body></html>")
    text = connectors.extract_text(p)
    assert "Terraform state in S3" in text
    assert "var x" not in text


def test_extract_docx_from_zip_xml(tmp_path):
    p = tmp_path / "doc.docx"
    xml = ('<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
           '<w:p><w:r><w:t>Pelican plan: move to Postgres 16.</w:t></w:r></w:p>'
           '</w:body></w:document>')
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", xml)
    assert "Pelican plan" in connectors._zip_xml_text(p)


def test_extract_unsupported_type_raises(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(b"\x89PNG")
    with pytest.raises(RuntimeError):
        connectors.extract_text(p)


@pytest.mark.skipif(shutil.which("cupsfilter") is None, reason="needs macOS cupsfilter to build a PDF")
def test_extract_pdf(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("Zebra caching insight: session keys expire after thirty minutes.")
    pdf = tmp_path / "report.pdf"
    subprocess.run(["cupsfilter", str(src)], stdout=pdf.open("wb"), check=True)
    assert "session keys expire" in connectors.extract_text(pdf)


# --- folder connector ------------------------------------------------------

def test_folder_connector_reads_supported_skips_excluded_and_lockfiles(tmp_path):
    (tmp_path / "keep.md").write_text("keep me")
    (tmp_path / "notes.txt").write_text("plain text note")
    (tmp_path / "~$draft.docx").write_bytes(b"lock file")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "config.md").write_text("hidden config")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")
    (tmp_path / "folder.md").mkdir()  # a folder named like a note
    docs = list(connectors.folder({"path": str(tmp_path)}))
    keys = sorted(d["key"] for d in docs)
    assert keys == ["keep.md", "notes.txt"]


def test_folder_connector_survives_one_unreadable_file(tmp_path, capsys):
    (tmp_path / "good.md").write_text("fine")
    (tmp_path / "bad.pdf").write_bytes(b"not a pdf")
    docs = list(connectors.folder({"path": str(tmp_path)}))
    assert [d["key"] for d in docs] == ["good.md"]
    assert "skip bad.pdf" in capsys.readouterr().err


# --- URL and Apple Notes connectors (network and osascript stubbed) --------

def test_urls_connector_skips_comments_and_bad_links(tmp_path, monkeypatch):
    f = tmp_path / "urls.txt"
    f.write_text("# comment\nhttps://ok.example/a\n\nhttps://broken.example/b\n")

    def fake_fetch(url):
        if "broken" in url:
            raise OSError("404")
        return "Page A", "body of A"

    monkeypatch.setattr(connectors, "_fetch", fake_fetch)
    docs = list(connectors.urls({"file": str(f)}))
    assert [d["key"] for d in docs] == ["https://ok.example/a"]
    assert docs[0]["title"] == "Page A" and docs[0]["url"] == "https://ok.example/a"


def test_apple_notes_parses_records(monkeypatch):
    fs, rs = "\x1f", "\x1e"
    fake = f"x-coredata://1{fs}First note{fs}Body one, with commas{rs}x-coredata://2{fs}Second{fs}Body two{rs}"
    monkeypatch.setattr(connectors.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=fake, stderr=""))
    docs = list(connectors.apple_notes({}))
    assert [d["title"] for d in docs] == ["First note", "Second"]
    assert docs[0]["text"] == "Body one, with commas"


# --- chat connectors --------------------------------------------------------

def _claude_line(**kw):
    return json.dumps(kw)


def test_claude_chats_keeps_prompts_and_replies_drops_tools_and_wrappers(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    lines = [
        _claude_line(type="ai-title", aiTitle="Invoice database choice"),
        _claude_line(type="user", timestamp="2026-11-18T10:00:00Z",
                     message={"content": "why did we switch databases for invoices"}),
        _claude_line(type="assistant", message={"content": [
            {"type": "thinking", "thinking": "secret reasoning"},
            {"type": "text", "text": "Because billing needed JSONB columns."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "cat .env"}},
        ]}),
        _claude_line(type="user", message={"content": [
            {"type": "tool_result", "content": "API_KEY=leaked"},
            {"type": "text", "text": "<system-reminder>hidden context</system-reminder>thanks"},
        ]}),
        _claude_line(type="assistant", isSidechain=True, message={"content": "sidechain noise"}),
    ]
    (proj / "s1.jsonl").write_text("\n".join(lines) + "\n")
    docs = list(connectors.claude_chats({"path": str(tmp_path)}))
    assert len(docs) == 1
    text = docs[0]["text"]
    assert "why did we switch databases" in text
    assert "JSONB columns" in text
    assert "thanks" in text
    for banned in ("secret reasoning", "cat .env", "leaked", "hidden context", "sidechain noise"):
        assert banned not in text
    assert "Invoice database choice" in docs[0]["title"]


def test_claude_chats_skips_subagent_transcripts(tmp_path):
    sub = tmp_path / "p" / "subagents"
    sub.mkdir(parents=True)
    (sub / "a.jsonl").write_text(_claude_line(type="user", message={"content": "hi"}) + "\n")
    assert list(connectors.claude_chats({"path": str(tmp_path)})) == []


def test_codex_chats_parses_rollout_and_skips_tool_calls(tmp_path):
    lines = [
        {"timestamp": "2026-11-20T10:00:00Z", "type": "session_meta", "payload": {"id": "x"}},
        {"timestamp": "2026-11-20T10:00:01Z", "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "why is docker slow"}]}},
        {"timestamp": "2026-11-20T10:00:02Z", "type": "response_item", "payload": {
            "type": "function_call", "name": "shell", "arguments": "{}"}},
        {"timestamp": "2026-11-20T10:00:03Z", "type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Copy package.json first."}]}},
    ]
    (tmp_path / "rollout.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    docs = list(connectors.codex_chats({"path": str(tmp_path)}))
    assert len(docs) == 1
    assert "why is docker slow" in docs[0]["text"]
    assert "Copy package.json first." in docs[0]["text"]
    assert "shell" not in docs[0]["text"]


def test_exchange_grouping_pairs_question_with_answer():
    doc = connectors._exchanges_to_doc(
        [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")], "Claude")
    assert doc.index("q1") < doc.index("a1") < doc.index("q2") < doc.index("a2")
    assert doc.count("## Q:") == 2


# --- source config ----------------------------------------------------------

def test_no_sources_by_default(tmp_path):
    assert rag.load_sources() == []


def test_add_and_remove_source(tmp_path):
    rag.add_source("folder", "obsidian", str(tmp_path))
    names = [s["name"] for s in rag.load_sources()]
    assert names == ["obsidian"]
    assert rag.load_sources()[0].get("exclude")  # vault exclusions applied
    assert rag.remove_source("obsidian") is True
    assert rag.load_sources() == []


def test_add_folder_rejects_missing_path(tmp_path):
    with pytest.raises(SystemExit):
        rag.add_source("folder", "nope", str(tmp_path / "missing"))


def test_add_rejects_unknown_type():
    with pytest.raises(SystemExit):
        rag.add_source("notion", "x")


def test_add_chat_source_with_day_limit():
    src = rag.add_source("claude-chats", "claude-chats", "90")
    assert src["type"] == "claude_chats" and src["days"] == 90


# --- sync and search (fake embedder) ---------------------------------------

def _vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "postgres.md").write_text("# Decision\nWe moved billing to Postgres for JSONB invoice columns.")
    (v / "kafka.md").write_text("# Kafka\nOrders topic keeps seven days of retention.")
    (v / "secrets.md").write_text("# Ops\nslack token " + "xox" + "b-1234567890-abcdefghij" + " should never leak")
    return v


def test_sync_indexes_and_search_finds_the_right_note(tmp_path):
    rag.add_source("folder", "obsidian", str(_vault(tmp_path)))
    report = rag.sync()
    assert report["obsidian"]["updated"] == 3
    hits = rag.search("why did we switch to postgres for invoices", k=3)
    assert hits[0]["title"] == "postgres"
    assert hits[0]["source"] == "obsidian"


def test_sync_is_incremental(tmp_path):
    v = _vault(tmp_path)
    rag.add_source("folder", "obsidian", str(v))
    rag.sync()
    again = rag.sync()
    assert again["obsidian"] == {"updated": 0, "unchanged": 3, "removed": 0}
    (v / "kafka.md").write_text("# Kafka\nchanged text")
    third = rag.sync()
    assert third["obsidian"]["updated"] == 1 and third["obsidian"]["unchanged"] == 2


def test_sync_removes_deleted_documents(tmp_path):
    v = _vault(tmp_path)
    rag.add_source("folder", "obsidian", str(v))
    rag.sync()
    (v / "kafka.md").unlink()
    report = rag.sync()
    assert report["obsidian"]["removed"] == 1
    assert rag.read_doc("obsidian:kafka.md") is None


def test_secrets_never_reach_the_index(tmp_path):
    rag.add_source("folder", "obsidian", str(_vault(tmp_path)))
    rag.sync()
    for hit in rag.search("slack token leak", k=5):
        assert "xoxb-" not in hit["text"]
    assert "xoxb-" not in (rag.read_doc("obsidian:secrets.md") or "")


def test_search_source_filter(tmp_path):
    rag.add_source("folder", "obsidian", str(_vault(tmp_path)))
    rag.sync()
    other = tmp_path / "other"
    other.mkdir()
    (other / "billing.txt").write_text("billing postgres invoices plan from another folder")
    rag.add_source("folder", "docs", str(other))
    rag.sync()
    hits = rag.search("postgres invoices", k=5, source="docs")
    assert hits and all(h["source"] == "docs" for h in hits)


def test_read_doc_returns_full_text(tmp_path):
    rag.add_source("folder", "obsidian", str(_vault(tmp_path)))
    rag.sync()
    assert "seven days" in rag.read_doc("obsidian:kafka.md")
    assert rag.read_doc("obsidian:missing.md") is None


def test_remove_source_drops_its_documents(tmp_path):
    rag.add_source("folder", "obsidian", str(_vault(tmp_path)))
    rag.sync()
    rag.remove_source("obsidian")
    assert rag.search("postgres", k=5) == []


def test_failed_source_keeps_last_good_index(tmp_path, monkeypatch):
    rag.add_source("folder", "obsidian", str(_vault(tmp_path)))
    rag.sync()

    def boom(cfg):
        raise RuntimeError("disk unplugged")
        yield  # pragma: no cover

    monkeypatch.setitem(connectors.CONNECTORS, "folder", boom)
    report = rag.sync()
    assert "error" in report["obsidian"]
    assert rag.read_doc("obsidian:kafka.md") is not None


def test_doctor_reports_sources_without_crashing(tmp_path):
    rag.add_source("folder", "obsidian", str(_vault(tmp_path)))
    rag.sync()
    report = rag.doctor()
    assert isinstance(report["sources"], list)
    assert report["sources"][0]["name"] == "obsidian"


# --- regressions for the Ollama and chat-pollution failures -----------------

def test_embed_batches_large_inputs(monkeypatch):
    calls = []
    monkeypatch.setattr(rag, "_embed_request", lambda inputs: (calls.append(len(inputs)), [[0.0]] * len(inputs))[1])
    monkeypatch.setattr(rag, "embed", REAL_EMBED)  # real batching logic, stubbed transport
    out = rag.embed(["x"] * 100, "search_document")
    assert len(out) == 100
    assert max(calls) <= rag.EMBED_BATCH and sum(calls) == 100


def test_embed_surfaces_ollama_error_text(monkeypatch):
    import io
    import urllib.error

    def fail(*a, **k):
        raise urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(b'{"error":"runner crashed"}'))

    monkeypatch.setattr(rag.urllib.request, "urlopen", fail)
    monkeypatch.setattr(rag.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError) as err:
        rag._embed_request(["hi"])
    assert "runner crashed" in str(err.value)


def test_automated_eval_and_capture_sessions_are_not_indexed(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    eval_line = json.dumps({"type": "user", "message": {"content":
        "Answer this question about our team's notes and past decisions. Question: x"}})
    capture_line = json.dumps({"type": "user", "message": {"content":
        "You are a background note-capture job working inside the user's Obsidian vault."}})
    real_line = json.dumps({"type": "user", "message": {"content": "how do I deploy the worker"}})
    (proj / "eval.jsonl").write_text(eval_line + "\n")
    (proj / "capture.jsonl").write_text(capture_line + "\n")
    (proj / "real.jsonl").write_text(real_line + "\n")
    titles = [d["title"] for d in connectors.claude_chats({"path": str(tmp_path)})]
    assert len(titles) == 1 and "deploy the worker" in titles[0]


# --- GitHub connector (gh CLI stubbed) ---------------------------------------

def _fake_gh(responses):
    def run(args):
        key = " ".join(args)
        for needle, value in responses.items():
            if needle in key:
                return value if isinstance(value, str) else json.dumps(value)
        raise RuntimeError(f"unexpected gh call: {key}")
    return run


def test_github_reads_md_from_public_non_fork_repos(tmp_path, monkeypatch):
    responses = {
        "repo list": [
            {"nameWithOwner": "me/tool", "isFork": False, "isArchived": False},
            {"nameWithOwner": "me/forked", "isFork": True, "isArchived": False},
            {"nameWithOwner": "me/old", "isFork": False, "isArchived": True},
        ],
        "repos/me/tool/git/trees": {"tree": [
            {"path": "README.md", "type": "blob", "sha": "a1", "size": 40},
            {"path": "docs/design.markdown", "type": "blob", "sha": "b2", "size": 40},
            {"path": "main.py", "type": "blob", "sha": "c3", "size": 40},
            {"path": "node_modules/x/README.md", "type": "blob", "sha": "d4", "size": 40},
        ]},
        "git/blobs/a1": "# Tool\nWhat it does.",
        "git/blobs/b2": "## Design\nWhy we chose SQLite.",
        "repos/me/tool": {"default_branch": "main"},
    }
    monkeypatch.setattr(connectors, "_gh", _fake_gh(responses))
    docs = list(connectors.github({"cache": str(tmp_path / "cache")}))
    keys = sorted(d["key"] for d in docs)
    assert keys == ["me/tool:README.md", "me/tool:docs/design.markdown"]
    readme = next(d for d in docs if d["key"] == "me/tool:README.md")
    assert readme["url"] == "https://github.com/me/tool/blob/main/README.md"
    assert "What it does" in readme["text"]


def test_github_cache_avoids_refetching_unchanged_files(tmp_path, monkeypatch):
    calls = []
    base = {
        "repo list": [{"nameWithOwner": "me/tool", "isFork": False, "isArchived": False}],
        "repos/me/tool/git/trees": {"tree": [{"path": "README.md", "type": "blob", "sha": "a1", "size": 40}]},
        "repos/me/tool": {"default_branch": "main"},
        "git/blobs/a1": "hello",
    }
    fake = _fake_gh(base)

    def counting(args):
        calls.append(" ".join(args))
        return fake(args)

    monkeypatch.setattr(connectors, "_gh", counting)
    cfg = {"cache": str(tmp_path / "cache")}
    list(connectors.github(cfg))
    blob_calls_first = sum("git/blobs" in c for c in calls)
    calls.clear()
    list(connectors.github(cfg))
    assert blob_calls_first == 1
    assert sum("git/blobs" in c for c in calls) == 0


def test_github_missing_cli_gives_clear_error(monkeypatch):
    def no_gh(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(connectors.subprocess, "run", no_gh)
    with pytest.raises(RuntimeError) as err:
        connectors._gh(["repo", "list"])
    assert "brew install gh" in str(err.value)


def test_add_github_source_with_optional_user():
    src = rag.add_source("github", "github")
    assert src["type"] == "github" and "user" not in src
    src = rag.add_source("github", "gh-other", "someone")
    assert src["user"] == "someone"
