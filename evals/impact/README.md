# Impact eval: does Engram change what Claude answers?

20 questions about a fictional team's notes, asked twice with `claude -p` (Sonnet 5.5):
- **with Engram:** the MCP server (`search_notes`, `read_doc`) is available.
- **baseline:** no tools and no files; the model must answer from general knowledge.

Plus 3 questions the notes can't answer (correct = says it doesn't know) and 1 question whose answer is a secret in the notes (correct = the secret is not repeated).

## Results (run on the fictional demo vault)

| | With Engram | Baseline |
|---|---|---|
| Team-specific questions answered correctly | **20 / 20** | 0 / 20 |
| Says "I don't know" for unknown questions | 3 / 3 | 3 / 3 |
| Secret repeated | 0 | 0 |
| Mean time per question | 7.2 s | 5.8 s |
| Mean cost per question | $0.020 | $0.014 |

## How to read this

- **Grading is keyword-based.** The baseline's 5 keyword matches were general-knowledge answers that explicitly say the team's facts are unknown, so they are counted as 0 above. Engram's 20 answers were checked by hand and cite the right notes.
- **The vault is small and fictional.** Real notes are messier, so expect lower numbers. Questions in the set are paraphrases of the notes, which is the case Engram is built for.
- **Baseline is cheaper and faster** because it skips the search step. The cost is the answer quality.

## Run it

```bash
~/.local/share/engram/venv/bin/python evals/impact/run_impact.py          # both arms, about 50 model calls
BASELINE_ONLY=1 ~/.local/share/engram/venv/bin/python evals/impact/run_impact.py   # rerun baseline, keep Engram results
```

Runs use `--no-session-persistence` and disable the Obsidian capture hook, so eval sessions are not saved to your chat history or vault.
