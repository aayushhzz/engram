#!/usr/bin/env bash
# Installs Engram: checks prerequisites, sets up Python and the embedding model,
# registers the MCP server with Claude Code and, if given, adds your primary
# source (an Obsidian vault or any folder of .md/.txt files).
# Other sources (Apple Notes, Google Docs exports, web links) are opt-in:
#   rag.py add ...   see INSTALL.md
# Safe to run again. Options:
#   --vault PATH   your Obsidian vault or markdown folder (optional)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ENGRAM_HOME="${ENGRAM_HOME:-$HOME/.local/share/engram}"
VENV="$ENGRAM_HOME/venv"
VAULT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vault) VAULT="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

say "Checking prerequisites"
command -v python3 >/dev/null || die "python3 not found. Install Python 3.10 or newer."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10 or newer is required."
command -v ollama >/dev/null || die "Ollama not found. Install it from https://ollama.com/download and run this again."
command -v claude >/dev/null || die "Claude Code CLI ('claude') not found on PATH."
echo "python3, ollama and claude found."

say "Starting Ollama"
if ! curl -s -m 2 http://localhost:11434/api/tags >/dev/null; then
  (ollama serve >/dev/null 2>&1 &)
  sleep 3
fi
curl -s -m 5 http://localhost:11434/api/tags >/dev/null || die "Ollama is not responding on port 11434. Open the Ollama app or run 'ollama serve'."

if ! ollama list | awk '{print $1}' | grep -q '^nomic-embed-text'; then
  say "Downloading the embedding model (about 270 MB, free, runs locally)"
  ollama pull nomic-embed-text
fi

say "Setting up the Python environment"
mkdir -p "$ENGRAM_HOME"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q -r "$HERE/requirements.txt"

if [ -n "$VAULT" ]; then
  say "Adding your primary source"
  [ -d "$VAULT" ] || die "Folder not found: $VAULT"
  "$VENV/bin/python" "$HERE/rag.py" add folder obsidian "$VAULT"
else
  say "No vault given: skipping the primary source for now (add one later with rag.py add folder obsidian PATH)"
fi

say "Building the index"
"$VENV/bin/python" "$HERE/rag.py" sync

say "Registering the MCP server with Claude Code"
if claude mcp list 2>/dev/null | grep -q '^engram:'; then
  claude mcp remove engram --scope user >/dev/null 2>&1 || true
fi
claude mcp add --scope user engram -- "$VENV/bin/python" "$HERE/mcp_server.py"

say "Health check"
"$VENV/bin/python" "$HERE/rag.py" doctor
claude mcp list 2>/dev/null | grep '^engram:' || true

cat <<MSG

Engram is installed. Restart Claude Code (or run /mcp) so it loads the engram tools.
Optional next step: connect more sources (Apple Notes, Google Docs exports, web links). See INSTALL.md.
MSG
