"""A small patdiff-like diff tool.

Doctests cover the core processing and color printing behavior.

>>> split_lines(b"apple\\nbanana\\n")
['apple', 'banana']
>>> [r.kind for r in diff(["a", "b", "c"], ["a", "X", "c"])]
[<Kind.SAME: 0>, <Kind.REPLACE: 3>, <Kind.SAME: 0>]
>>> out, changed = diff_output(b"apple\\nbanana\\ncherry\\n", b"apple\\nBANANA\\ncherry\\n", "old.txt", "new.txt", 16, False, False, False)
>>> changed
True
>>> print(out, end="")
------ old.txt
++++++ new.txt
@| @@ -1,3 +1,3 @@ ============================================================
 | apple
-| banana
+| BANANA
 | cherry
>>> diff_output(b"x = 1\\n", b"x  = 1\\n", "old", "new", 16, False, True, False)
('', False)
>>> print(diff_output(b"x = 1\\n", b"x  = 1\\n", "old", "new", 16, False, False, False)[0], end="")
------ old
++++++ new
@| @@ -1,1 +1,1 @@ ============================================================
!| x  = 1
>>> "\\x1b[41m-|" in diff_output(b"a\\n", b"b\\n", "old", "new", 16, True, False, False)[0]
True
>>> print(refine_unified_diff_input(b"--- a/x\\n+++ b/x\\n@@ -1,1 +1,1 @@\\n-old token\\n+new token\\n", False), end="")
--- a/x
+++ b/x
@@ -1,1 +1,1 @@
-old token
+new token
>>> ranges = detect_moves([Range(kind=Kind.SAME, prev=("h",)), Range(kind=Kind.PREV, prev=("a", "b", "c")), Range(kind=Kind.SAME, prev=("m",)), Range(kind=Kind.NEXT, next=("a", "b", "c"))], False)
>>> [r.kind for r in ranges]
[<Kind.SAME: 0>, <Kind.MOVE_FROM: 4>, <Kind.SAME: 0>, <Kind.MOVE_TO: 5>]
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, BinaryIO, cast

import typer as _typer  # pyright: ignore[reportMissingImports]

typer = cast(Any, _typer)


class Kind(Enum):
    SAME = 0
    PREV = 1
    NEXT = 2
    REPLACE = 3
    MOVE_FROM = 4
    MOVE_TO = 5


@dataclass(frozen=True, kw_only=True)
class Range:
    kind: Kind
    prev: tuple[str, ...] = ()
    next: tuple[str, ...] = ()
    move_id: int = 0


@dataclass(frozen=True, kw_only=True)
class Hunk:
    prev_start: int
    prev_size: int
    next_start: int
    next_size: int
    ranges: tuple[Range, ...]


@dataclass(frozen=True, kw_only=True)
class Segment:
    kind: Kind
    text: str


RefinedLine = tuple[Segment, ...]


@dataclass(frozen=True, kw_only=True)
class RefinedReplace:
    prev: tuple[RefinedLine, ...]
    next: tuple[RefinedLine, ...]


@dataclass(frozen=True, kw_only=True)
class MoveCandidate:
    range_index: int
    start_line: int


HUNK_SEPARATOR = " ============================================================"
MIN_MOVE_LINES = 3
NULL_SHA = "."
GIT_CONTEXT = 3
USAGE_NORMAL = "usage: pdiff [-context N] OLD NEW"
USAGE_GIT = "usage: pdiff -git path old-file old-hex old-mode new-file new-hex new-mode [new-path] [info]"


def diff(prev: list[str], next_: list[str]) -> list[Range]:
    return patience_diff(prev, next_, 0, len(prev), 0, len(next_))


def patience_diff(
    prev: list[str], next_: list[str], p0: int, p1: int, n0: int, n1: int
) -> list[Range]:
    prefix_len = 0
    while (
        p0 + prefix_len < p1
        and n0 + prefix_len < n1
        and prev[p0 + prefix_len] == next_[n0 + prefix_len]
    ):
        prefix_len += 1

    suffix_len = 0
    while (
        p1 - suffix_len > p0 + prefix_len
        and n1 - suffix_len > n0 + prefix_len
        and prev[p1 - 1 - suffix_len] == next_[n1 - 1 - suffix_len]
    ):
        suffix_len += 1

    ranges: list[Range] = []
    if prefix_len:
        ranges = append_same(ranges, tuple(prev[p0 : p0 + prefix_len]))
    ranges.extend(
        patience_diff_middle(
            prev,
            next_,
            p0 + prefix_len,
            p1 - suffix_len,
            n0 + prefix_len,
            n1 - suffix_len,
        )
    )
    if suffix_len:
        ranges = append_same(ranges, tuple(prev[p1 - suffix_len : p1]))
    return ranges


def patience_diff_middle(
    prev: list[str], next_: list[str], p0: int, p1: int, n0: int, n1: int
) -> list[Range]:
    if p0 == p1 and n0 == n1:
        return []
    if p0 == p1:
        return [Range(kind=Kind.NEXT, next=tuple(next_[n0:n1]))]
    if n0 == n1:
        return [Range(kind=Kind.PREV, prev=tuple(prev[p0:p1]))]

    unique_prev = unique_lines(prev, p0, p1)
    unique_next = unique_lines(next_, n0, n1)
    matches = sorted(
        (pi, unique_next[line])
        for line, pi in unique_prev.items()
        if line in unique_next
    )
    if not matches:
        return [
            Range(kind=Kind.REPLACE, prev=tuple(prev[p0:p1]), next=tuple(next_[n0:n1]))
        ]

    anchors = [matches[i] for i in lis([ni for _, ni in matches])]
    ranges: list[Range] = []
    cur_p, cur_n = p0, n0
    for pi, ni in anchors:
        ranges.extend(patience_diff(prev, next_, cur_p, pi, cur_n, ni))
        ranges = append_same(ranges, (prev[pi],))
        cur_p, cur_n = pi + 1, ni + 1
    ranges.extend(patience_diff(prev, next_, cur_p, p1, cur_n, n1))
    return ranges


def unique_lines(lines: list[str], lo: int, hi: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    index: dict[str, int] = {}
    for i in range(lo, hi):
        counts[lines[i]] = counts.get(lines[i], 0) + 1
        index[lines[i]] = i
    return {line: index[line] for line, count in counts.items() if count == 1}


def append_same(ranges: list[Range], lines: tuple[str, ...]) -> list[Range]:
    if not lines:
        return ranges
    if ranges and ranges[-1].kind is Kind.SAME:
        ranges[-1] = replace(ranges[-1], prev=ranges[-1].prev + lines)
    else:
        ranges.append(Range(kind=Kind.SAME, prev=lines))
    return ranges


def lis(values: list[int]) -> list[int]:
    if not values:
        return []
    tails: list[int] = []
    back = [-1] * len(values)
    for i, value in enumerate(values):
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if values[tails[mid]] < value:
                lo = mid + 1
            else:
                hi = mid
        if lo > 0:
            back[i] = tails[lo - 1]
        if lo == len(tails):
            tails.append(i)
        else:
            tails[lo] = i
    out = [0] * len(tails)
    cur = tails[-1]
    for i in range(len(tails) - 1, -1, -1):
        out[i] = cur
        cur = back[cur]
    return out


def make_hunks(flat_ranges: list[Range], context: int) -> list[Hunk]:
    hunks: list[Hunk] = []
    prev_line = next_line = 1
    prefix: list[str] = []
    in_hunk = False
    current: list[Range] = []
    same_after: list[str] = []
    hunk_prev_start = hunk_next_start = 1

    def close_hunk(trailing: list[str]) -> None:
        nonlocal in_hunk, current
        tail = trailing[:context] if len(trailing) > context else trailing
        if tail:
            current.append(Range(kind=Kind.SAME, prev=tuple(tail)))
        hunks.append(build_hunk(tuple(current), hunk_prev_start, hunk_next_start))
        current = []
        in_hunk = False

    for r in flat_ranges:
        if r.kind is Kind.SAME:
            n = len(r.prev)
            if not in_hunk:
                prefix.extend(r.prev)
                prev_line += n
                next_line += n
                if len(prefix) > context:
                    prefix = prefix[-context:]
            else:
                same_after.extend(r.prev)
                prev_line += n
                next_line += n
                if len(same_after) > 2 * context:
                    snap = same_after
                    close_hunk(snap[:context])
                    prefix = snap[-context:] if context else []
                    same_after = []
            continue

        if not in_hunk:
            hunk_prev_start = prev_line - len(prefix)
            hunk_next_start = next_line - len(prefix)
            current = []
            if prefix:
                current.append(Range(kind=Kind.SAME, prev=tuple(prefix)))
                prefix = []
            in_hunk = True
        elif same_after:
            current.append(Range(kind=Kind.SAME, prev=tuple(same_after)))
            same_after = []

        current.append(r)
        if r.kind in {Kind.PREV, Kind.MOVE_FROM}:
            prev_line += len(r.prev)
        elif r.kind in {Kind.NEXT, Kind.MOVE_TO}:
            next_line += len(r.next)
        elif r.kind is Kind.REPLACE:
            prev_line += len(r.prev)
            next_line += len(r.next)

    if in_hunk:
        close_hunk(same_after)
    return hunks


def build_hunk(ranges: tuple[Range, ...], prev_start: int, next_start: int) -> Hunk:
    prev_size = next_size = 0
    for r in ranges:
        if r.kind is Kind.SAME:
            prev_size += len(r.prev)
            next_size += len(r.prev)
        elif r.kind in {Kind.PREV, Kind.MOVE_FROM}:
            prev_size += len(r.prev)
        elif r.kind in {Kind.NEXT, Kind.MOVE_TO}:
            next_size += len(r.next)
        elif r.kind is Kind.REPLACE:
            prev_size += len(r.prev)
            next_size += len(r.next)
    return Hunk(
        prev_start=prev_start,
        prev_size=prev_size,
        next_start=next_start,
        next_size=next_size,
        ranges=ranges,
    )


def detect_moves(ranges: list[Range], ignore_whitespace: bool) -> list[Range]:
    ranges = list(ranges)
    prev_buckets: dict[str, list[MoveCandidate]] = {}
    next_buckets: dict[str, list[MoveCandidate]] = {}
    prev_line = next_line = 1
    for i, r in enumerate(ranges):
        if r.kind is Kind.SAME:
            prev_line += len(r.prev)
            next_line += len(r.prev)
        elif r.kind is Kind.PREV:
            if len(r.prev) >= MIN_MOVE_LINES:
                prev_buckets.setdefault(move_key(r.prev, ignore_whitespace), []).append(
                    MoveCandidate(range_index=i, start_line=prev_line)
                )
            prev_line += len(r.prev)
        elif r.kind is Kind.NEXT:
            if len(r.next) >= MIN_MOVE_LINES:
                next_buckets.setdefault(move_key(r.next, ignore_whitespace), []).append(
                    MoveCandidate(range_index=i, start_line=next_line)
                )
            next_line += len(r.next)
        elif r.kind is Kind.REPLACE:
            prev_line += len(r.prev)
            next_line += len(r.next)

    move_id = 1
    for key, prevs in prev_buckets.items():
        nexts = next_buckets.get(key, [])
        used = [False] * len(nexts)
        for p in sorted(prevs, key=lambda x: x.start_line):
            best = min(
                (j for j in range(len(nexts)) if not used[j]),
                key=lambda j: abs(p.start_line - nexts[j].start_line),
                default=-1,
            )
            if best == -1:
                continue
            used[best] = True
            n = nexts[best]
            rp, rn = ranges[p.range_index], ranges[n.range_index]
            ranges[p.range_index] = replace(
                rp, kind=Kind.MOVE_FROM, move_id=move_id, next=rn.next
            )
            ranges[n.range_index] = replace(
                rn, kind=Kind.MOVE_TO, move_id=move_id, prev=rp.prev
            )
            move_id += 1

    unmatched_prevs, unmatched_nexts = unmatched_move_candidates(ranges)
    if len(unmatched_prevs) * len(unmatched_nexts) <= 40000:
        for p in unmatched_prevs:
            if ranges[p.range_index].kind is not Kind.PREV:
                continue
            best_idx, best_score = -1, 0.0
            for j, n in enumerate(unmatched_nexts):
                if ranges[n.range_index].kind is not Kind.NEXT:
                    continue
                score = block_similarity(
                    ranges[p.range_index].prev,
                    ranges[n.range_index].next,
                    ignore_whitespace,
                )
                if score >= 0.5 and score > best_score:
                    best_idx, best_score = j, score
            if best_idx != -1:
                n = unmatched_nexts[best_idx]
                rp, rn = ranges[p.range_index], ranges[n.range_index]
                ranges[p.range_index] = replace(
                    rp, kind=Kind.MOVE_FROM, move_id=move_id, next=rn.next
                )
                ranges[n.range_index] = replace(
                    rn, kind=Kind.MOVE_TO, move_id=move_id, prev=rp.prev
                )
                move_id += 1
    return ranges


def unmatched_move_candidates(
    ranges: list[Range],
) -> tuple[list[MoveCandidate], list[MoveCandidate]]:
    prevs: list[MoveCandidate] = []
    nexts: list[MoveCandidate] = []
    prev_line = next_line = 1
    for i, r in enumerate(ranges):
        if r.kind is Kind.SAME:
            prev_line += len(r.prev)
            next_line += len(r.prev)
        elif r.kind is Kind.PREV:
            if len(r.prev) >= MIN_MOVE_LINES:
                prevs.append(MoveCandidate(range_index=i, start_line=prev_line))
            prev_line += len(r.prev)
        elif r.kind is Kind.NEXT:
            if len(r.next) >= MIN_MOVE_LINES:
                nexts.append(MoveCandidate(range_index=i, start_line=next_line))
            next_line += len(r.next)
        elif r.kind in {Kind.REPLACE, Kind.MOVE_FROM, Kind.MOVE_TO}:
            prev_line += len(r.prev)
            next_line += len(r.next)
    return prevs, nexts


def block_similarity(
    a: tuple[str, ...], b: tuple[str, ...], ignore_whitespace: bool
) -> float:
    aa = normalize_lines(a) if ignore_whitespace else list(a)
    bb = normalize_lines(b) if ignore_whitespace else list(b)
    same = sum(len(r.prev) for r in diff(aa, bb) if r.kind is Kind.SAME)
    return same / max(len(a), len(b))


def normalize_lines(lines: tuple[str, ...] | list[str]) -> list[str]:
    return [strip_all_whitespace(line) for line in lines]


def move_key(lines: tuple[str, ...], ignore_whitespace: bool) -> str:
    return "\n".join(normalize_lines(lines) if ignore_whitespace else lines)


def render_unified_diff(
    prev_name: str, next_name: str, hunks: list[Hunk], color: bool
) -> str:
    if not hunks:
        return ""
    out = [f"------ {prev_name}\n", f"++++++ {next_name}\n"]
    for h in hunks:
        write_line(
            out,
            "hunk",
            f"@@ -{h.prev_start},{h.prev_size} +{h.next_start},{h.next_size} @@{HUNK_SEPARATOR}",
            color,
        )
        for r in h.ranges:
            if r.kind is Kind.SAME:
                write_plain_lines(out, "same", r.prev, color)
            elif r.kind is Kind.PREV:
                write_plain_lines(out, "prev", r.prev, color)
            elif r.kind is Kind.NEXT:
                write_plain_lines(out, "next", r.next, color)
            elif r.kind is Kind.REPLACE:
                rr = refine_replace(list(r.prev), list(r.next))
                if is_whitespace_only(rr):
                    for line in unified_lines(rr):
                        write_line(out, "unified", refined_plain(line), color)
                else:
                    for line in rr.prev:
                        write_refined_line(out, "prev", line, color)
                    for line in rr.next:
                        write_refined_line(out, "next", line, color)
            elif r.kind is Kind.MOVE_FROM:
                for line in refine_replace(list(r.prev), list(r.next)).prev:
                    write_refined_line(out, "move_from", line, color)
            elif r.kind is Kind.MOVE_TO:
                for line in refine_replace(list(r.prev), list(r.next)).next:
                    write_refined_line(out, "move_to", line, color)
    return "".join(out)


def write_plain_lines(
    out: list[str], kind: str, lines: tuple[str, ...], color: bool
) -> None:
    for line in lines:
        write_line(out, kind, line, color)


def write_line(out: list[str], kind: str, text: str, color: bool) -> None:
    prefix = line_prefix(kind)
    if color:
        prefix = ansi(line_prefix_style(kind), prefix)
        style = line_text_style(kind)
        if style:
            text = ansi(style, text)
    out.append(f"{prefix} {text}\n")


def write_refined_line(
    out: list[str], kind: str, line: RefinedLine, color: bool
) -> None:
    prefix = line_prefix(kind)
    if color:
        prefix = ansi(line_prefix_style(kind), prefix)
    parts = [prefix, " "]
    for seg in line:
        text = seg.text
        if color:
            style = refined_segment_style(kind, seg.kind)
            if style:
                text = ansi(style, text)
        parts.append(text)
    parts.append("\n")
    out.append("".join(parts))


def line_prefix(kind: str) -> str:
    return {
        "same": " |",
        "prev": "-|",
        "next": "+|",
        "unified": "!|",
        "hunk": "@|",
        "move_from": "<|",
        "move_to": ">|",
    }[kind]


def line_prefix_style(kind: str) -> str:
    return {
        "same": "100",
        "prev": "41",
        "next": "42",
        "unified": "43",
        "hunk": "100",
        "move_from": "45",
        "move_to": "46",
    }[kind]


def line_text_style(kind: str) -> str:
    return {
        "prev": "31",
        "next": "32",
        "hunk": "1",
        "move_from": "35",
        "move_to": "36",
    }.get(kind, "")


def refined_segment_style(line_kind: str, seg_kind: Kind) -> str:
    if line_kind == "prev":
        return "90" if seg_kind is Kind.SAME else "31"
    if line_kind == "next":
        return "" if seg_kind is Kind.SAME else "32"
    if line_kind == "move_from":
        return "90" if seg_kind is Kind.SAME else "31;1"
    if line_kind == "move_to":
        return "33" if seg_kind is Kind.SAME else "32;1"
    return ""


def ansi(style: str, s: str) -> str:
    return f"\x1b[{style}m{s}\x1b[0m"


def refine_replace(prev_lines: list[str], next_lines: list[str]) -> RefinedReplace:
    sentinel = "\n"

    def flatten(lines: list[str]) -> list[str]:
        tokens: list[str] = []
        for line in lines:
            tokens.extend(tokenize(line))
            tokens.append(sentinel)
        return tokens

    token_ranges = diff(flatten(prev_lines), flatten(next_lines))
    return RefinedReplace(
        prev=collapse_tokens(token_ranges, True, sentinel),
        next=collapse_tokens(token_ranges, False, sentinel),
    )


def collapse_tokens(
    token_ranges: list[Range], prev_side: bool, sentinel: str
) -> tuple[RefinedLine, ...]:
    lines: list[RefinedLine] = []
    cur: list[Segment] = []

    def emit(kind: Kind, text: str) -> None:
        nonlocal cur
        if text == sentinel:
            lines.append(tuple(cur))
            cur = []
        elif cur and cur[-1].kind is kind:
            cur[-1] = replace(cur[-1], text=cur[-1].text + text)
        else:
            cur.append(Segment(kind=kind, text=text))

    for r in token_ranges:
        if r.kind is Kind.SAME:
            for tok in r.prev:
                emit(Kind.SAME, tok)
        elif r.kind is Kind.PREV and prev_side:
            for tok in r.prev:
                emit(Kind.PREV, tok)
        elif r.kind is Kind.NEXT and not prev_side:
            for tok in r.next:
                emit(Kind.NEXT, tok)
        elif r.kind is Kind.REPLACE:
            for tok in r.prev if prev_side else r.next:
                emit(Kind.PREV if prev_side else Kind.NEXT, tok)
    if cur:
        lines.append(tuple(cur))
    return tuple(lines)


def is_whitespace_only(rr: RefinedReplace) -> bool:
    return not any(
        seg.kind is Kind.PREV and seg.text.strip() for line in rr.prev for seg in line
    ) and not any(
        seg.kind is Kind.NEXT and seg.text.strip() for line in rr.next for seg in line
    )


def unified_lines(rr: RefinedReplace) -> tuple[RefinedLine, ...]:
    return rr.next or rr.prev


def refined_plain(line: RefinedLine) -> str:
    return "".join(seg.text for seg in line)


def tokenize(line: str) -> list[str]:
    delimiters = '"{}[]#,.;()_'
    punct = "=`+-/!@$%^&*:|<>"
    tokens: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch.isdigit():
            j = i + 1
            while j < len(line) and (line[j].isdigit() or line[j] in "._,eE+-"):
                j += 1
            tokens.extend(split_numeric_literal(line[i:j]))
            i = j
        elif ch in delimiters:
            tokens.append(ch)
            i += 1
        elif ch in punct:
            j = i + 1
            while j < len(line) and line[j] in punct:
                j += 1
            tokens.append(line[i:j])
            i = j
        elif ch in " \t":
            j = i + 1
            while j < len(line) and line[j] in " \t":
                j += 1
            tokens.append(line[i:j])
            i = j
        else:
            j = i + 1
            while (
                j < len(line)
                and line[j] not in delimiters
                and line[j] not in punct
                and line[j] not in " \t"
            ):
                j += 1
            tokens.append(line[i:j])
            i = j
    return tokens


def split_numeric_literal(s: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    while i < len(s):
        tokens.append(s[i])
        if s[i] in "eE" and i + 1 < len(s) and s[i + 1] in "+-":
            i += 1
            tokens.append(s[i])
        i += 1
    return tokens


def refine_unified_diff_input(data: bytes, color: bool) -> str:
    if not data or not data.rstrip(b"\n"):
        return ""
    lines = data.decode().rstrip("\n").split("\n")
    out: list[str] = []
    del_run: list[str] = []
    add_run: list[str] = []
    in_hunk = False

    def flush() -> None:
        nonlocal del_run, add_run
        if not del_run and not add_run:
            return
        if not del_run:
            for line in add_run:
                write_unified_plain_line(out, "+", line, color)
        elif not add_run:
            for line in del_run:
                write_unified_plain_line(out, "-", line, color)
        else:
            rr = refine_replace(del_run, add_run)
            for line in rr.prev:
                write_unified_refined_line(out, "-", line, color)
            for line in rr.next:
                write_unified_refined_line(out, "+", line, color)
        del_run, add_run = [], []

    for line in lines:
        if line.startswith("@@"):
            flush()
            in_hunk = True
            write_unified_meta_line(out, line, color)
        elif in_hunk and line.startswith("-") and not line.startswith("---"):
            del_run.append(line[1:])
        elif in_hunk and line.startswith("+") and not line.startswith("+++"):
            add_run.append(line[1:])
        else:
            flush()
            write_unified_meta_line(out, line, color)
    flush()
    rendered = "".join(out)
    return rendered.rstrip("\n") if data[-1:] != b"\n" else rendered


def write_unified_meta_line(out: list[str], line: str, color: bool) -> None:
    out.append((ansi("1", line) if color and line.startswith("@@") else line) + "\n")


def write_unified_plain_line(
    out: list[str], prefix: str, text: str, color: bool
) -> None:
    line = prefix + text
    if color and prefix == "-":
        line = ansi("31", line)
    elif color and prefix == "+":
        line = ansi("32", line)
    out.append(line + "\n")


def write_unified_refined_line(
    out: list[str], prefix: str, line: RefinedLine, color: bool
) -> None:
    if not color:
        out.append(prefix + refined_plain(line) + "\n")
        return
    parts = [ansi("31" if prefix == "-" else "32", prefix)]
    for seg in line:
        text = seg.text
        if prefix == "-":
            text = ansi("90" if seg.kind is Kind.SAME else "31", text)
        elif seg.kind is not Kind.SAME:
            text = ansi("32", text)
        parts.append(text)
    out.append("".join(parts) + "\n")


def diff_output(
    prev_data: bytes,
    next_data: bytes,
    prev_name: str,
    next_name: str,
    context: int,
    color: bool,
    ignore_whitespace: bool,
    find_moves: bool,
) -> tuple[str, bool]:
    if prev_data == next_data:
        return "", False
    if is_binary(prev_data) or is_binary(next_data):
        return f"Binary files {prev_name} and {next_name} differ\n", True
    ranges = diff_ranges(
        split_lines(prev_data), split_lines(next_data), ignore_whitespace
    )
    if find_moves:
        ranges = detect_moves(ranges, ignore_whitespace)
    hunks = make_hunks(ranges, context)
    if not hunks:
        return "", False
    return render_unified_diff(prev_name, next_name, hunks, color), True


def diff_ranges(
    prev_lines: list[str], next_lines: list[str], ignore_whitespace: bool
) -> list[Range]:
    if not ignore_whitespace:
        return diff(prev_lines, next_lines)
    return remap_ranges_to_original(
        diff(normalize_lines(prev_lines), normalize_lines(next_lines)),
        prev_lines,
        next_lines,
    )


def remap_ranges_to_original(
    key_ranges: list[Range], prev_orig: list[str], next_orig: list[str]
) -> list[Range]:
    out: list[Range] = []
    pi = ni = 0
    for r in key_ranges:
        if r.kind is Kind.SAME:
            n = len(r.prev)
            out.append(Range(kind=Kind.SAME, prev=tuple(prev_orig[pi : pi + n])))
            pi += n
            ni += n
        elif r.kind is Kind.PREV:
            n = len(r.prev)
            out.append(Range(kind=Kind.PREV, prev=tuple(prev_orig[pi : pi + n])))
            pi += n
        elif r.kind is Kind.NEXT:
            n = len(r.next)
            out.append(Range(kind=Kind.NEXT, next=tuple(next_orig[ni : ni + n])))
            ni += n
        elif r.kind is Kind.REPLACE:
            pn, nn = len(r.prev), len(r.next)
            out.append(
                Range(
                    kind=Kind.REPLACE,
                    prev=tuple(prev_orig[pi : pi + pn]),
                    next=tuple(next_orig[ni : ni + nn]),
                )
            )
            pi += pn
            ni += nn
    return out


def split_lines(data: bytes) -> list[str]:
    if not data:
        return []
    text = data.decode().rstrip("\n")
    return [] if text == "" else text.split("\n")


def strip_all_whitespace(s: str) -> str:
    return "".join(ch for ch in s if not ch.isspace())


def is_binary(data: bytes) -> bool:
    return b"\0" in data


def resolve_color(mode: str, stdout: BinaryIO, git_mode: bool) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    if mode == "auto":
        return stdout.isatty() or (git_mode and invoked_by_git())
    raise ValueError("-color must be one of: auto, always, never")


def invoked_by_git() -> bool:
    return bool(
        os.environ.get("GIT_DIFF_PATH_COUNTER") or os.environ.get("GIT_EXTERNAL_DIFF")
    )


def read_git_side(sha: str, path: str) -> bytes:
    if sha == NULL_SHA:
        return b""
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise OSError(f"read {path}: {exc}") from exc


def run_git_mode(
    args: list[str],
    context: int,
    color: bool,
    ignore_whitespace: bool,
    find_moves: bool,
) -> tuple[str, int]:
    if len(args) < 7 or len(args) > 9:
        raise ValueError(USAGE_GIT)
    path, old_file, old_hex, old_mode, new_file, new_hex, new_mode = args[:7]
    new_path = args[7] if len(args) >= 8 else path
    info = args[8] if len(args) >= 9 else ""
    prev_name, next_name = "a/" + path, "b/" + new_path
    is_new_file = old_hex == NULL_SHA
    is_deleted_file = new_hex == NULL_SHA
    prev_data = read_git_side(old_hex, old_file)
    next_data = read_git_side(new_hex, new_file)
    diff_out, diff_changed = diff_output(
        prev_data,
        next_data,
        prev_name,
        next_name,
        context,
        color,
        ignore_whitespace,
        find_moves,
    )

    meta: list[str] = []
    if is_new_file:
        meta.append("new file mode " + new_mode)
    elif is_deleted_file:
        meta.append("deleted file mode " + old_mode)
    elif old_mode != new_mode:
        meta.extend(["old mode " + old_mode, "new mode " + new_mode])
    if not diff_changed and not meta:
        return "", 0

    title = f"pdiff -git {prev_name} {next_name}"
    out = [ansi("1", title) + "\n" if color else title + "\n"]
    out.extend(m + "\n" for m in meta)
    if info:
        out.append(info + "\n")
    if diff_changed and not is_new_file and not is_deleted_file:
        out.append(f"index {old_hex}..{new_hex}\n")
    out.append(diff_out)
    return "".join(out), 1


@dataclass(frozen=True, kw_only=True)
class Args:
    context: int = 16
    git: bool = False
    find_moves: bool = True
    color: str = "auto"
    whitespace: bool = False
    paths: Annotated[list[str] | None, typer.Argument()] = None

    def __post_init__(self) -> None:
        self.main()

    def main(self) -> None:
        if self.context < 0:
            typer.echo("pdiff: -context must be >= 0", err=True)
            raise typer.Exit(2)
        try:
            use_color = resolve_color(self.color, sys.stdout.buffer, self.git)
        except ValueError as exc:
            typer.echo(f"pdiff: {exc}", err=True)
            raise typer.Exit(2) from exc
        rest = self.paths or []
        ignore_ws = not self.whitespace
        if self.git:
            context = self.context
            try:
                out, _code = run_git_mode(
                    rest, context, use_color, ignore_ws, self.find_moves
                )
            except (OSError, ValueError) as exc:
                typer.echo(f"pdiff: {exc}", err=True)
                raise typer.Exit(2) from exc
            if out:
                sys.stdout.write(out)
            raise typer.Exit(0)
        if not rest:
            sys.stdout.write(
                refine_unified_diff_input(sys.stdin.buffer.read(), use_color)
            )
            raise typer.Exit(0)
        if len(rest) != 2:
            typer.echo(USAGE_NORMAL, err=True)
            raise typer.Exit(2)
        try:
            prev_data = Path(rest[0]).read_bytes()
            next_data = Path(rest[1]).read_bytes()
        except OSError as exc:
            target = rest[0] if not Path(rest[0]).exists() else rest[1]
            typer.echo(f"pdiff: read {target}: {exc}", err=True)
            raise typer.Exit(2) from exc
        out, changed = diff_output(
            prev_data,
            next_data,
            rest[0],
            rest[1],
            self.context,
            use_color,
            ignore_ws,
            self.find_moves,
        )
        if out:
            sys.stdout.write(out)
        raise typer.Exit(1 if changed else 0)


if __name__ == "__main__":
    typer.run(Args)
