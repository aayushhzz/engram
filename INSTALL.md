# Install Engram (instructions for Claude Code)

You are installing Engram for the user. Do every step below yourself with the Bash tool. Ask the user only where this file says to ask. Keep the user informed in short lines as you go.

Engram has one primary source: an **Obsidian vault or any folder of notes**. The folder can hold Markdown, text, Word (`.doc`/`.docx`), PDF, RTF, OpenDocument or HTML files. Every other source is optional and only added when the user chooses it. Never add a source the user did not pick.

## 1. Check prerequisites

Run `command -v python3 ollama claude`, then `python3 --version`. Python must be 3.10 or newer.

- If `ollama` is missing: tell the user to install it from https://ollama.com/download (or `brew install ollama`). Ask them to tell you when it is done, then wait.
- If `python3` is missing or too old: suggest `brew install python` and wait for their answer.
- If `claude` is missing: you are running inside Claude Code, so stop and tell the user.

## 2. Ask one question: the primary source

Ask: "Where are your notes? Give me the path to your Obsidian vault, or to any folder of notes (Markdown, text, Word, PDF and similar files all work). Reply 'skip' if you want to set it up later."

Keep the answer as `VAULT`. Any folder of notes works the same as a vault.

## 3. Run the installer

From the Engram folder (the one that contains this file):

```bash
./install.sh --vault "VAULT_PATH"   # use the path from step 2
./install.sh                         # if the user said skip
```

The installer:
- starts Ollama if needed and downloads the free embedding model `nomic-embed-text` (about 270 MB, runs locally)
- creates a Python environment under `~/.local/share/engram`
- adds the primary source only if a path was given
- builds the index
- registers the `engram` MCP server with Claude Code at user scope

If it fails, read the error. Fix what you can (start Ollama, check the path). Ask the user only when the fix needs their input.

## 4. Verify

- `~/.local/share/engram/venv/bin/python rag.py doctor` (run from the Engram folder): `ollama` and `embedding_model` should be `ok`, and `sources` should list the primary source with a document count.
- `claude mcp list`: `engram` should show as connected. If it is still starting, wait a few seconds and check once more.

## 5. Offer the other sources

After a successful install, ask the user once: "Do you want to connect any other sources? Engram can read:"

1. **Apple Notes** (macOS only): `rag.py add apple-notes apple-notes`, then `rag.py sync`. The first sync asks macOS for permission for the terminal to control Notes.
2. **Google Docs**: in Google Drive, select the docs and choose Download (the default `.docx` works, as do `.md`, `.txt` and `.pdf`). Unzip them into a folder, then `rag.py add folder google-docs "FOLDER_PATH"` and `rag.py sync`. No Google login is needed.
3. **Web links and docs pages**: list URLs one per line in `inbox/urls.txt`, then `rag.py add links doc-links` and `rag.py sync`. Engram fetches each page's text.
4. **Another folder of notes**: `rag.py add folder NAME "PATH"` and `rag.py sync`.
5. **Claude Code chat history**: `rag.py add claude-chats claude-chats`, then `rag.py sync`. This makes Engram smarter over time, because past answers and fixes become searchable. Only the user's prompts and Claude's replies are indexed; tool calls and tool output are skipped. To limit it to recent chats, add a day count, for example `rag.py add claude-chats claude-chats 90`.
6. **Codex chat history**: `rag.py add codex-chats codex-chats`, then `rag.py sync`. This reads `~/.codex/sessions`, so only offer it if that folder exists.

Run only the ones the user picks. If they pick none, move on. Ask again only if they bring it up later.

Run commands from the Engram folder, with `PY=~/.local/share/engram/venv/bin/python` (for example `$PY rag.py add links doc-links`).

To remove a source: `$PY rag.py remove NAME`. This also deletes its indexed documents.

## 6. Finish

Tell the user:
- Restart Claude Code, or run `/mcp`, so it loads the Engram tools.
- Claude can now search their notes with `search_notes`, `read_doc` and `list_sources`.
- To re-index after changing notes: `$PY rag.py sync`.

### If the user also uses Codex

Codex can use the same MCP server. Register the command `~/.local/share/engram/venv/bin/python /ABSOLUTE/PATH/TO/engram/mcp_server.py` as an MCP server in Codex's own MCP settings. This has not been tested here, so check the Codex documentation for the exact syntax.

## Rules for Claude while installing

- Do not print or copy note contents into chat. Engram redacts tokens before indexing, but still keep secrets out of the conversation.
- Do not change shell profiles or unrelated Claude settings. The only change is the `engram` MCP entry.
- Do not delete the user's notes or vault.
- Do not add any source the user did not choose.
