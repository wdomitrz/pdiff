# pdiff

Single-file Python diff tool with patience diff, word-level refinement, ANSI color, stdin unified-diff refinement, git external-diff mode, and simple move detection.

Inspired by [patdiff](https://github.com/janestreet/patdiff) and [pdiff_go](https://github.com/wdomitrz/pdiff_go).

## Usage

### diff

```sh
./pdiff.py diff OLD NEW
```

`OLD` and `NEW` may be files or directories.

### git

```sh
git config --global diff.external '<path_to>/pdiff.py git'
```

### stdin

```sh
git diff | ./pdiff.py stdin
```

Useful options: `--color always|auto|never`, `--context N`, `--whitespace`, `--no-find-moves`.
