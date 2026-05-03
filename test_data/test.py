#!/usr/bin/env python3
################################################################
# Copyright (c) 2026 Witalis Domitrz <witekdomitrz@gmail.com>
# AGPL License
################################################################

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
PDIFF = ROOT / "pdiff.py"
DATA = ROOT / "test_data"


def run(
    args: list[str], *, input_data: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [PYTHON, str(PDIFF), *args],
        cwd=ROOT,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def assert_run(
    args: list[str],
    *,
    expected_code: int,
    expected_stdout: bytes | None = None,
    input_data: bytes | None = None,
) -> bytes:
    result = run(args, input_data=input_data)
    if result.returncode != expected_code:
        raise AssertionError(
            f"{args} exited {result.returncode}, expected {expected_code}\n"
            f"stdout:\n{result.stdout.decode(errors='replace')}\n"
            f"stderr:\n{result.stderr.decode(errors='replace')}"
        )
    if expected_stdout is not None and result.stdout != expected_stdout:
        raise AssertionError(
            f"{args} stdout mismatch\n"
            f"want:\n{expected_stdout.decode(errors='replace')}\n"
            f"got:\n{result.stdout.decode(errors='replace')}"
        )
    if result.stderr:
        raise AssertionError(
            f"{args} wrote stderr:\n{result.stderr.decode(errors='replace')}"
        )
    return result.stdout


def main() -> None:
    assert_run(
        [
            "diff",
            "--color",
            "never",
            "test_data/simple_old.txt",
            "test_data/simple_new.txt",
        ],
        expected_code=1,
        expected_stdout=(DATA / "simple_expected.txt").read_bytes(),
    )
    color_stdout = assert_run(
        [
            "diff",
            "--color",
            "always",
            "test_data/simple_old.txt",
            "test_data/simple_new.txt",
        ],
        expected_code=1,
    )
    if b"\x1b[41m-|" not in color_stdout:
        raise AssertionError("colored diff output is missing red deletion marker")

    assert_run(
        [
            "diff",
            "--color",
            "never",
            "test_data/whitespace_old.txt",
            "test_data/whitespace_new.txt",
        ],
        expected_code=0,
        expected_stdout=b"",
    )
    assert_run(
        [
            "diff",
            "--color",
            "never",
            "--whitespace",
            "test_data/whitespace_old.txt",
            "test_data/whitespace_new.txt",
        ],
        expected_code=1,
        expected_stdout=(DATA / "whitespace_expected.txt").read_bytes(),
    )

    assert_run(
        [
            "diff",
            "--color",
            "never",
            "test_data/move_old.txt",
            "test_data/move_new.txt",
        ],
        expected_code=1,
        expected_stdout=(DATA / "move_expected.txt").read_bytes(),
    )

    indent_stdout = assert_run(
        [
            "diff",
            "--color",
            "never",
            "test_data/indent_old.txt",
            "test_data/indent_new.txt",
        ],
        expected_code=1,
        expected_stdout=(DATA / "indent_expected.txt").read_bytes(),
    )
    if b" |         print(y)\n" not in indent_stdout:
        raise AssertionError("indent diff context should render from the new file")
    if b" |     print(y)\n" in indent_stdout:
        raise AssertionError("indent diff context rendered old-file indentation")

    stdin_color = assert_run(
        ["stdin", "--color", "always"],
        expected_code=0,
        input_data=(DATA / "stdin_unified.diff").read_bytes(),
    )
    if b"\x1b[" not in stdin_color or b"banana" not in stdin_color:
        raise AssertionError("stdin refined color output is missing expected ANSI/text")

    assert_run(
        [
            "git",
            "--color",
            "never",
            "file.txt",
            "test_data/simple_old.txt",
            "aaa111",
            "100644",
            "test_data/simple_new.txt",
            "bbb222",
            "100644",
        ],
        expected_code=0,
        expected_stdout=(DATA / "git_expected.txt").read_bytes(),
    )


if __name__ == "__main__":
    main()
