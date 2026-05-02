# Python pdiff handoff

## Status

In progress: porting `pdiff_go/main.go` into a single pure-Python `pdiff.py`.

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
- Main CLI object will be `@dataclass(frozen=True, kw_only=True) Args`, run with `typer.run(Args)`.
- Data processing is kept separate from printing: core functions return strings/booleans, `Args.main()` handles I/O and exit codes.
- CLI is intentionally Python/Typer-style now: use long options such as `--color always`, `--context 3`, `--git`, `--whitespace`, and `--no-find-moves`. The Python version does not preserve Go-style single-dash long flags.
