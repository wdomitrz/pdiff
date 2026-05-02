# Python pdiff handoff

## Status

Working version implemented in `pdiff.py`; validation currently passes.

## Scope

The Python version targets the Go implementation's behavior:
- patience diff
- hunk creation with context
- refined word-level output
- ANSI color modes
- whitespace-ignore default with `--whitespace` opt-in
- binary-file reporting
- stdin unified-diff refinement
- git external-diff mode
- simple exact/fuzzy move detection

Tests are doctests embedded in `pdiff.py`, per request.

## Current Notes

- Only external runtime dependency planned is `typer`.
- CLI parsing uses frozen keyword-only dataclasses: `Args` for file diff mode, `StdinArgs` for stdin refinement, and `GitArgs` for git external-diff mode.
- Data processing is kept separate from printing: core functions return strings/booleans, `Args.main()` handles I/O and exit codes.
- CLI is intentionally Python/Typer-style now with explicit subcommands: `pdiff.py diff ...`, `pdiff.py stdin ...`, and `pdiff.py git ...`.
