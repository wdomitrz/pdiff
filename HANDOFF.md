# Python pdiff handoff

## Status

`pdiff.py` is implemented as a single-file Python/Typer version of `pdiff`, but the latest refactor is not fully validated yet.

Current important state:
- The code was being converted from `Enum`-based `Kind` to a `Literal` type alias.
- Kind dispatch is being converted from `if` chains to exhaustive `match` statements.
- The last `make check` run failed during this refactor, before this handoff update.
- Do not assume the current `pdiff.py` passes lint/type/doctest until `make check` is rerun and fixed.

## Scope

Implemented behavior targets the simplified Go `pdiff` behavior:
- patience diff
- hunk creation with configurable context
- refined word-level output
- ANSI color modes
- whitespace-ignore default with `--whitespace` opt-in
- binary-file reporting
- stdin unified-diff refinement
- git external-diff mode
- simple exact/fuzzy move detection

## CLI

The Python CLI is intentionally Typer-style with explicit subcommands:

- `./pdiff.py diff OLD NEW`
- `./pdiff.py stdin`
- `./pdiff.py git PATH OLD_FILE OLD_HEX OLD_MODE NEW_FILE NEW_HEX NEW_MODE`

Flags are long-form Typer options, for example:
- `--color always|auto|never`
- `--context N`
- `--whitespace`
- `--no-find-moves`

## Code Shape

- Runtime dependency: `typer`.
- Main data model uses frozen, keyword-only dataclasses.
- CLI mode dataclasses:
  - `Args` for file diff mode
  - `StdinArgs` for stdin refinement
  - `GitArgs` for git external-diff mode
- `GitArgs.USAGE` is a `ClassVar`.
- Variable-size collections use `list` consistently.
- Fixed-size return pairs still use `tuple[...]`, for example `(output, changed)`.
- Doctests were moved from the module docstring to relevant functions.
- Several helper functions were moved onto dataclasses:
  - `Range` owns size/advance/move-candidate/refinement helpers.
  - `Hunk.from_ranges()` builds hunk sizes.
  - `RefinedReplace.from_lines()` and `RefinedReplace.collapse_tokens()` own refinement construction.

## Tests

Makefile targets:
- `make fix`
- `make lint`
- `make check`
- `make test`

`make check` runs:
- `ruff check .`
- `basedpyright --project pyproject.toml --level error .`
- doctests
- `test_data/test.py`

Checked-in smoke fixtures live under `test_data/`:
- file-diff fixtures use `test_data/<name>/old.txt`, `new.txt`, and `expected.txt`
- stdin fixture uses `test_data/stdin/input.diff`
- git fixture uses `test_data/git/expected.txt`
- test runner is `test_data/test.py`

## Current Refactor Notes

The user requested:
- Make `Kind` a `Literal` type alias, not an `Enum`.
- Inline allowed `Kind` values in the alias.
- Remove separate kind constants such as `SAME`, `PREV`, etc.
- Replace kind `if`/`elif` checks with exhaustive `match` statements.
- Use `case _:` with `assert_never(value_from_match)`.
- Do not match on non-obvious expressions inline; assign the subject to a local first, then match on that local.
- Move more functions to be methods of relevant classes

Known work remaining:
- Finish replacing leftover old kind constants/references if any remain.
- Move more functions to be methods of relevant classes
