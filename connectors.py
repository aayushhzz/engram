"""Source connectors. Each connector takes its config dict and yields documents:

    {"key": stable id within the source, "title": str, "text": str, "url": str | None}

Add a new source by writing a function here and registering it in CONNECTORS.
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

PLAIN_EXTENSIONS = {".md", ".markdown", ".txt", ".text", ".org", ".rst", ".adoc", ".tex", ".csv"}
HTML_EXTENSIONS = {".html", ".htm"}
# Formats macOS `textutil` converts to text (built in, free). On other systems
# .docx and .odt fall back to reading the XML inside the file.
TEXTUTIL_EXTENSIONS = {".doc", ".docx", ".rtf", ".rtfd", ".odt", ".webarchive"}
SUPPORTED_EXTENSIONS = PLAIN_EXTENSIONS | HTML_EXTENSIONS | TEXTUTIL_EXTENSIONS | {".pdf"}
DEFAULT_EXCLUDE = {".obsidian", ".omx", ".trash", ".git", "node_modules"}
MAX_FILE_BYTES = 50 * 1024 * 1024  # skip huge files such as scanned-book PDFs


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr)  # stderr, so it never mixes with JSON output


def _xml_text(xml: str, paragraph_tag: str) -> str:
    xml = re.sub(rf"</{paragraph_tag}>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()


def _zip_xml_text(path: Path) -> str:
    """Read .docx / .odt without extra packages: both are zip files of XML."""
    with zipfile.ZipFile(path) as z:
        if path.suffix.lower() == ".docx":
            return _xml_text(z.read("word/document.xml").decode("utf-8", "replace"), "w:p")
        return _xml_text(z.read("content.xml").decode("utf-8", "replace"), "text:p")


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("pypdf is not installed (pip install pypdf)")
    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()


def extract_text(path: Path) -> str:
    """Plain text from a note file, whatever its format."""
    ext = path.suffix.lower()
    if ext in PLAIN_EXTENSIONS:
        return path.read_text(encoding="utf-8", errors="replace")
    if ext in HTML_EXTENSIONS:
        parser = _PageText()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    if ext == ".pdf":
        return _pdf_text(path)
    if ext in TEXTUTIL_EXTENSIONS:
        if shutil.which("textutil"):
            out = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(path)],
                capture_output=True, text=True, timeout=120,
            )
            if out.returncode == 0:
                return out.stdout
        if ext in (".docx", ".odt"):
            return _zip_xml_text(path)
        raise RuntimeError(f"{ext} needs macOS textutil")
    raise RuntimeError(f"unsupported file type: {ext}")


def folder(cfg: dict):
    """Note files in a folder: Obsidian vaults, Google Docs exports, or any folder of
    .md, .txt, Word (.doc/.docx), PDF, RTF, OpenDocument, HTML and other text files."""
    root = Path(cfg["path"]).expanduser()
    exclude = set(cfg.get("exclude", DEFAULT_EXCLUDE))
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue  # also skips folders that happen to end in .md
        rel = p.relative_to(root)
        if any(part in exclude for part in rel.parts) or p.name.startswith("~$"):
            continue  # ~$ files are Word lock files
        if p.stat().st_size > MAX_FILE_BYTES:
            _warn(f"skip {rel}: larger than {MAX_FILE_BYTES // 2**20} MB")
            continue
        # shortcut: every sync re-extracts each file to hash its text, which is slow
        # for big PDF folders; cache by mtime if syncs get slow.
        try:
            text = extract_text(p)
        except Exception as e:  # one unreadable file must not stop the sync
            _warn(f"skip {rel}: {e}")
            continue
        yield {"key": rel.as_posix(), "title": p.stem, "text": text, "url": None}


class _PageText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer"}

    def __init__(self):
        super().__init__()
        self.parts, self.title = [], ""
        self._skip, self._in_title = 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("p", "br", "li", "h1", "h2", "h3", "h4", "tr", "div", "pre"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def _fetch(url: str) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Engram/0.1 (personal notes indexer)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        ctype = r.headers.get("Content-Type", "")
        body = r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
    if "html" not in ctype and not url.endswith((".html", ".htm")):
        return url.rsplit("/", 1)[-1] or url, body  # raw markdown or text
    parser = _PageText()
    parser.feed(body)
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return parser.title.strip() or url, text


def urls(cfg: dict):
    """Web pages and raw docs listed one per line in a text file (# starts a comment)."""
    lines = Path(cfg["file"]).expanduser().read_text().splitlines()
    for line in lines:
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        try:
            title, text = _fetch(url)
        except Exception as e:  # one bad link should not stop the sync
            _warn(f"skip {url}: {e}")
            continue
        yield {"key": url, "title": title, "text": text, "url": url}


# AppleScript returns records separated by ASCII 30 and fields by ASCII 31,
# so note text containing newlines or punctuation cannot break the parsing.
_APPLE_NOTES_SCRIPT = """
set FS to ASCII character 31
set RS to ASCII character 30
tell application "Notes"
    set out to ""
    repeat with n in notes
        set out to out & (id of n) & FS & (name of n) & FS & (plaintext of n) & RS
    end repeat
    return out
end tell
"""


def apple_notes(cfg: dict):
    """Notes from the macOS Notes app. The first run asks macOS for permission
    to let the terminal control Notes."""
    result = subprocess.run(
        ["osascript", "-e", _APPLE_NOTES_SCRIPT],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed")
    for record in result.stdout.split("\x1e"):
        if not record.strip():
            continue
        note_id, name, body = (record.split("\x1f") + ["", ""])[:3]
        yield {"key": note_id.strip(), "title": name.strip(), "text": body.strip(), "url": None}


# --- AI chat history -------------------------------------------------------
# Only what the user typed and what the assistant replied is indexed. Thinking,
# tool calls and tool output are skipped: they are noisy and often hold secrets.

_WRAPPER_TAGS = re.compile(
    r"<(system-reminder|command-message|command-name|command-args|local-command-stdout|"
    r"local-command-caveat|local-command-stderr|environment_context|user_instructions|"
    r"pasted_content)\b[^>]*>.*?</\1>",
    re.DOTALL,
)


def _clean(text: str) -> str:
    return _WRAPPER_TAGS.sub("", text).strip()


def _exchanges_to_doc(messages: list[tuple[str, str]], assistant: str) -> str:
    """Group each user message with the replies after it into one '## Q:' section,
    so a search hit carries both the question and the answer."""
    sections, current = [], None
    for role, text in messages:
        if role == "user":
            if current:
                sections.append(current)
            first_line = text.splitlines()[0][:100]
            current = [f"## Q: {first_line}", f"**You:** {text}"]
        elif current is not None:
            current.append(f"**{assistant}:** {text}")
    if current:
        sections.append(current)
    return "\n\n".join("\n\n".join(s) for s in sections)


def _recent(path: Path, days: int) -> bool:
    import time
    return days <= 0 or (time.time() - path.stat().st_mtime) <= days * 86400


# Automated headless sessions are not your chats: the impact eval and the Obsidian capture job.
AUTOMATED_PROMPT_PREFIXES = (
    "Answer this question about our team's notes and past decisions.",
    "You are a background note-capture job",
)


def claude_chats(cfg: dict):
    """Claude Code conversations from ~/.claude/projects (one document per session)."""
    import json
    root = Path(cfg.get("path", "~/.claude/projects")).expanduser()
    days = int(cfg.get("days", 0))
    for p in sorted(root.rglob("*.jsonl")):
        if "subagents" in p.parts or not _recent(p, days):
            continue
        title, date, messages = "", "", []
        for line in p.open(encoding="utf-8", errors="replace"):
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("type") == "ai-title":
                title = o.get("aiTitle") or title
            if o.get("type") not in ("user", "assistant") or o.get("isSidechain"):
                continue
            content = (o.get("message") or {}).get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
            else:
                continue
            text = _clean(text)
            if text:
                date = date or (o.get("timestamp") or "")[:10]
                messages.append((o["type"], text))
        if not any(role == "user" for role, _ in messages):
            continue
        first_prompt = next(t for r, t in messages if r == "user")
        if first_prompt.startswith(AUTOMATED_PROMPT_PREFIXES):
            continue
        first = first_prompt.splitlines()[0][:80]
        yield {
            "key": p.relative_to(root).as_posix(),
            "title": f"Claude chat {date}: {title or first}",
            "text": _exchanges_to_doc(messages, "Claude"),
            "url": None,
        }


def _codex_message(o: dict):
    """Return (role, text) for a Codex rollout line, or None. Handles the
    {"type":"response_item","payload":{...}} shape and the older flat shape."""
    item = o.get("payload") if o.get("type") == "response_item" else o
    if not isinstance(item, dict) or item.get("type") != "message":
        return None
    role = item.get("role")
    if role not in ("user", "assistant"):
        return None
    blocks = item.get("content") or []
    text = "\n".join(
        b.get("text", "") for b in blocks
        if isinstance(b, dict) and b.get("type") in ("input_text", "output_text", "text")
    )
    return role, text


def codex_chats(cfg: dict):
    """OpenAI Codex CLI conversations from ~/.codex/sessions (one document per session).
    Shortcut: parser written from Codex's documented rollout format and tested on a
    sample file, not on a real Codex install."""
    import json
    root = Path(cfg.get("path", "~/.codex/sessions")).expanduser()
    if not root.is_dir():
        return
    days = int(cfg.get("days", 0))
    for p in sorted(root.rglob("*.jsonl")):
        if not _recent(p, days):
            continue
        date, messages = "", []
        for line in p.open(encoding="utf-8", errors="replace"):
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            date = date or (o.get("timestamp") or "")[:10]
            msg = _codex_message(o)
            if msg:
                text = _clean(msg[1])
                if text:
                    messages.append((msg[0], text))
        if not any(role == "user" for role, _ in messages):
            continue
        first = next(t for r, t in messages if r == "user").splitlines()[0][:80]
        yield {
            "key": p.relative_to(root).as_posix(),
            "title": f"Codex chat {date}: {first}",
            "text": _exchanges_to_doc(messages, "Codex"),
            "url": None,
        }


# --- GitHub ----------------------------------------------------------------
# Reads markdown from public repos using the GitHub CLI (`gh`), so no token is stored
# in sources.json. Each file is cached by its git blob hash, so a sync only downloads
# files that changed.
GH_SKIP_DIRS = {"node_modules", "vendor", "third_party"}


def _gh(args: list[str]) -> str:
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI not found. Install it with `brew install gh`, then `gh auth login`.")
    if out.returncode != 0:
        raise RuntimeError(f"gh {args[0]} failed: {out.stderr.strip()[:200]}")
    return out.stdout


def github(cfg: dict):
    """Markdown files from your public GitHub repos (or cfg['user']'s repos)."""
    home = Path(os.environ.get("ENGRAM_HOME", Path.home() / ".local/share/engram"))
    cache = Path(cfg.get("cache", home / "github-cache"))
    list_args = ["repo", "list"] + ([cfg["user"]] if cfg.get("user") else [])
    list_args += ["--visibility", "public", "--limit", str(int(cfg.get("max_repos", 200))),
                  "--json", "nameWithOwner,isFork,isArchived"]
    for repo in json.loads(_gh(list_args)):
        if repo["isFork"] and not cfg.get("include_forks"):
            continue
        if repo["isArchived"] and not cfg.get("include_archived"):
            continue
        nwo = repo["nameWithOwner"]
        try:
            branch = json.loads(_gh(["api", f"repos/{nwo}"]))["default_branch"]
            tree = json.loads(_gh(["api", f"repos/{nwo}/git/trees/{branch}?recursive=1"]))
        except (RuntimeError, KeyError, json.JSONDecodeError) as e:
            _warn(f"skip {nwo}: {e}")
            continue
        for item in tree.get("tree", []):
            path = item.get("path", "")
            if item.get("type") != "blob" or not path.lower().endswith((".md", ".markdown")):
                continue
            if GH_SKIP_DIRS & set(path.split("/")) or item.get("size", 0) > MAX_FILE_BYTES:
                continue
            sha = item["sha"]
            cached = cache / f"{sha}.md"
            if cached.exists():
                text = cached.read_text(encoding="utf-8", errors="replace")
            else:
                try:
                    text = _gh(["api", "-H", "Accept: application/vnd.github.raw",
                                f"repos/{nwo}/git/blobs/{sha}"])
                except RuntimeError as e:
                    _warn(f"skip {nwo}/{path}: {e}")
                    continue
                cache.mkdir(parents=True, exist_ok=True)
                cached.write_text(text, encoding="utf-8")
            yield {"key": f"{nwo}:{path}", "title": f"{nwo} · {path}", "text": text,
                   "url": f"https://github.com/{nwo}/blob/{branch}/{path}"}


CONNECTORS = {
    "folder": folder,
    "urls": urls,
    "apple_notes": apple_notes,
    "claude_chats": claude_chats,
    "codex_chats": codex_chats,
    "github": github,
}
