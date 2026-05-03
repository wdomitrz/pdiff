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
        capture_output=True,
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
    assert result.returncode == expected_code
    assert expected_stdout is None or result.stdout == expected_stdout
    assert not result.stderr
    return result.stdout


def assert_diff_case(
    name: str,
    *,
    args: list[str] | None = None,
    expected_code: int = 1,
    expected_stdout: bytes | None = None,
) -> bytes:
    case_dir = DATA / name
    old_path = case_dir / "old.txt"
    new_path = case_dir / "new.txt"
    if not old_path.exists() and not new_path.exists():
        old_path = case_dir / "old"
        new_path = case_dir / "new"
    return assert_run(
        [
            "diff",
            "--color",
            "always",
            *(args or []),
            str(old_path.relative_to(ROOT)),
            str(new_path.relative_to(ROOT)),
        ],
        expected_code=expected_code,
        expected_stdout=(
            (case_dir / "expected.txt").read_bytes()
            if expected_stdout is None
            else expected_stdout
        ),
    )


def main() -> None:
    assert_diff_case("simple")
    assert_diff_case("whitespace", expected_code=0, expected_stdout=b"")
    assert_diff_case("whitespace", args=["--whitespace"])
    assert_diff_case("move")
    assert_diff_case("indent")
    assert_diff_case("indent_move")
    assert_diff_case("directory")

    assert_run(
        ["stdin", "--color", "always"],
        expected_code=0,
        input_data=(DATA / "stdin" / "input.diff").read_bytes(),
        expected_stdout=(DATA / "stdin" / "expected.txt").read_bytes(),
    )

    assert_run(
        [
            "git",
            "--color",
            "always",
            "file.txt",
            "test_data/simple/old.txt",
            "aaa111",
            "100644",
            "test_data/simple/new.txt",
            "bbb222",
            "100644",
        ],
        expected_code=0,
        expected_stdout=(DATA / "git" / "expected.txt").read_bytes(),
    )


if __name__ == "__main__":
    main()
