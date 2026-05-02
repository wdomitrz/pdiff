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
- CLI parsing uses frozen keyword-only dataclasses: `Args` for normal file/stdin mode and `GitArgs` for the `git` subcommand.
- Data processing is kept separate from printing: core functions return strings/booleans, `Args.main()` handles I/O and exit codes.
- CLI is intentionally Python/Typer-style now: use long options such as `--color always`, `--context 3`, `--whitespace`, and `--no-find-moves`. Git external-diff mode is `pdiff.py git ...`, not `--git`.
