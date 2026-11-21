"""MCP server exposing the Engram index to Claude Code.

Tools:
  search_notes(query, k, source) -> ranked chunks with source, title, url
  read_doc(doc_id)               -> full text of one document
  list_sources()                 -> configured sources and document counts
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

import rag  # noqa: E402

mcp = FastMCP("engram")


@mcp.tool()
def search_notes(query: str, k: int = 5, source: str | None = None) -> list[dict]:
    """Semantic search over the user's notes and docs: Obsidian learnings and decisions,
    other note folders, past Claude Code and Codex conversations, Apple Notes,
    Google Docs exports, and saved documentation links.
    Use this before answering questions about the user's past decisions, learnings or saved docs.
    Pass `source` (e.g. "obsidian") to limit results to one source."""
    return rag.search(query, max(1, min(k, 20)), source)


@mcp.tool()
def read_doc(doc_id: str) -> str:
    """Return the full text of a document. Use the doc_id from search_notes results."""
    text = rag.read_doc(doc_id)
    return text if text is not None else f"No document with id: {doc_id}"


@mcp.tool()
def list_sources() -> list[dict]:
    """List the configured note sources and how many documents each one has indexed."""
    return rag.list_sources()


if __name__ == "__main__":
    mcp.run()
