"""Render the README images from real runs on a demo index (the eval notes),
so screenshots never show anyone's personal notes.

Run: ~/.local/share/engram/venv/bin/python docs/make_images.py
"""
import json
import os
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)
PY = sys.executable

work = Path(tempfile.mkdtemp(prefix="engram-demo-"))
env = {**os.environ, "RAG_DB": str(work / "index.db"), "ENGRAM_CONFIG": str(work / "sources.json")}
notes = work / "My Vault"
notes.mkdir()
for name, text in json.loads((ROOT / "evals" / "dataset.json").read_text())["notes"].items():
    (notes / name).write_text(text)


def run(*args: str) -> str:
    out = subprocess.run([PY, str(ROOT / "rag.py"), *args], capture_output=True, text=True, env=env, cwd=ROOT)
    return out.stdout.strip()


def terminal_svg(filename: str, title: str, blocks: list[tuple[str, str]]) -> None:
    """blocks: (command, output). Draws a macOS-style terminal window."""
    lines = []
    for cmd, output in blocks:
        lines.append(("cmd", f"$ {cmd}"))
        for raw in output.splitlines():
            while len(raw) > 92:
                lines.append(("out", raw[:92]))
                raw = "  " + raw[92:]
            lines.append(("out", raw))
        lines.append(("out", ""))
    h, w = 64 + 22 * len(lines), 900
    colors = {"cmd": "#7ee787", "out": "#e6edf3"}
    text = "\n".join(
        f'<text x="24" y="{70 + 22 * i}" fill="{colors[k]}">{escape(s) or " "}</text>'
        for i, (k, s) in enumerate(lines)
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<rect width="{w}" height="{h}" rx="12" fill="#0d1117"/>
<rect width="{w}" height="36" rx="12" fill="#161b22"/><rect y="24" width="{w}" height="12" fill="#161b22"/>
<circle cx="22" cy="18" r="6" fill="#ff5f57"/><circle cx="42" cy="18" r="6" fill="#febc2e"/><circle cx="62" cy="18" r="6" fill="#28c840"/>
<text x="{w // 2}" y="23" fill="#8b949e" font-family="-apple-system,Helvetica,Arial" font-size="13" text-anchor="middle">{escape(title)}</text>
<g font-family="SFMono-Regular,Menlo,Consolas,monospace" font-size="14">
{text}
</g></svg>"""
    (OUT / filename).write_text(svg)


# 1. Connect a vault and index it
add = run("add", "folder", "obsidian", str(notes)).replace(str(notes), "~/My Vault")
sync = run("sync")
terminal_svg("sync.svg", "engram — index your notes", [
    ('rag.py add folder obsidian "~/My Vault"', add),
    ("rag.py sync", sync),
])

# 2. Search by meaning
query = "why did we switch databases for invoices"
search = run("search", query)
top = "\n\n".join(search.split("\n\n")[:3])
terminal_svg("search.svg", "engram — search by meaning, not keywords", [(f'rag.py search "{query}"', top)])

# 3. Eval chart
results = json.loads((ROOT / "evals" / "results.json").read_text())
metrics = ["hit@1", "hit@3", "MRR"]
labels = {"hit@1": "Right note first", "hit@3": "Right note in top 3", "MRR": "Mean reciprocal rank"}
bars = []
for i, m in enumerate(metrics):
    y = 90 + i * 90
    kw, en = results["keyword (BM25)"][m], results["engram"][m]
    bars.append(f'<text x="30" y="{y - 12}" class="l">{labels[m]}</text>')
    bars.append(f'<rect x="30" y="{y}" width="{kw * 600:.0f}" height="24" rx="4" fill="#8b949e"/>')
    bars.append(f'<text x="{40 + kw * 600:.0f}" y="{y + 17}" class="v">{kw:.0%} keyword</text>' if m != "MRR"
                else f'<text x="{40 + kw * 600:.0f}" y="{y + 17}" class="v">{kw:.2f} keyword</text>')
    bars.append(f'<rect x="30" y="{y + 30}" width="{en * 600:.0f}" height="24" rx="4" fill="#a371f7"/>')
    bars.append(f'<text x="{40 + en * 600:.0f}" y="{y + 47}" class="v b">{en:.0%} Engram</text>' if m != "MRR"
                else f'<text x="{40 + en * 600:.0f}" y="{y + 47}" class="v b">{en:.2f} Engram</text>')
(OUT / "eval.svg").write_text(f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="370" viewBox="0 0 900 370">
<style>.l{{font:600 15px -apple-system,Helvetica,Arial;fill:#e6edf3}} .v{{font:13px -apple-system,Helvetica,Arial;fill:#c9d1d9}} .b{{font-weight:700;fill:#d2a8ff}}</style>
<rect width="900" height="370" rx="12" fill="#0d1117"/>
<text x="30" y="40" style="font:700 20px -apple-system,Helvetica,Arial;fill:#ffffff">{results['queries']} questions worded differently from the notes</text>
{chr(10).join(bars)}
</svg>""")

# 4. Architecture diagram
src = ["Obsidian / Markdown", "Word · PDF · text", "Claude & Codex chats", "Apple Notes", "Web docs"]
boxes = "\n".join(
    f'<rect x="30" y="{60 + i * 62}" width="230" height="46" rx="10" fill="#161b22" stroke="#30363d"/>'
    f'<text x="145" y="{89 + i * 62}" class="t" text-anchor="middle">{escape(s)}</text>'
    f'<path d="M260 {83 + i * 62} C 300 {83 + i * 62}, 300 200, 335 200" stroke="#a371f7" fill="none" stroke-width="2"/>'
    for i, s in enumerate(src)
)
(OUT / "architecture.svg").write_text(f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="400" viewBox="0 0 900 400">
<style>.t{{font:14px -apple-system,Helvetica,Arial;fill:#e6edf3}} .h{{font:700 16px -apple-system,Helvetica,Arial;fill:#ffffff}} .s{{font:12px -apple-system,Helvetica,Arial;fill:#8b949e}}</style>
<rect width="900" height="400" rx="12" fill="#0d1117"/>
<text x="30" y="36" class="h">Your notes</text><text x="345" y="36" class="h">Engram (runs on your Mac)</text><text x="690" y="36" class="h">Your AI</text>
{boxes}
<rect x="335" y="80" width="300" height="240" rx="14" fill="#1c1430" stroke="#a371f7" stroke-width="2"/>
<text x="485" y="120" class="t" text-anchor="middle">1. Extract text from any format</text>
<text x="485" y="160" class="t" text-anchor="middle">2. Redact API keys and tokens</text>
<text x="485" y="200" class="t" text-anchor="middle">3. Chunk by heading</text>
<text x="485" y="240" class="t" text-anchor="middle">4. Embed locally (Ollama)</text>
<text x="485" y="280" class="t" text-anchor="middle">5. Store in SQLite + sqlite-vec</text>
<text x="485" y="345" class="s" text-anchor="middle">free · private · no API keys</text>
<path d="M635 200 L 690 200" stroke="#a371f7" stroke-width="2" marker-end="url(#a)"/>
<defs><marker id="a" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#a371f7"/></marker></defs>
<rect x="695" y="130" width="175" height="140" rx="12" fill="#161b22" stroke="#30363d"/>
<text x="782" y="165" class="t" text-anchor="middle">MCP server</text>
<text x="782" y="200" class="s" text-anchor="middle">search_notes</text>
<text x="782" y="220" class="s" text-anchor="middle">read_doc · list_sources</text>
<text x="782" y="252" class="t" text-anchor="middle">Claude Code · Codex</text>
</svg>""")

print("wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))
