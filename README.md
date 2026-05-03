# pdiff

`pdiff.py` is a single-file Python implementation of `pdiff`.

It provides:

- patience diff
- hunk output with configurable context
- word-level refinement inside changed lines
- ANSI color modes
- default whitespace-insensitive comparison, with `--whitespace` to show whitespace changes
- binary-file reporting
- stdin unified-diff refinement
- git external-diff mode
- simple exact/fuzzy move detection

## Usage

Run a file diff:

```sh
./pdiff.py diff OLD NEW
```

Refine a unified diff from stdin:

```sh
git diff | ./pdiff.py stdin
```

Run as a git external diff command:

```sh
./pdiff.py git PATH OLD_FILE OLD_HEX OLD_MODE NEW_FILE NEW_HEX NEW_MODE
```

Useful options:

```sh
--color always|auto|never
--context N
--whitespace
--no-find-moves
```

By default, file diffs ignore whitespace-only changes. Use `--whitespace` when whitespace changes should be reported.

## Tests

Run the test suite:

```sh
make test
```

The file-diff fixtures live in per-test directories:

```text
test_data/<name>/old.txt
test_data/<name>/new.txt
test_data/<name>/expected.txt
```

`test_data/test.py` runs a standard file-diff fixture from just the directory name. Expected snapshots are generated with `--color always`, so `expected.txt` files include ANSI escape sequences.

Special fixtures:

- `test_data/stdin/input.diff`
- `test_data/stdin/expected.txt`
- `test_data/git/expected.txt`

Run formatting and lint/type checks:

```sh
make fix
make lint
```
