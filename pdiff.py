#!/usr/bin/env python3
################################################################
# Copyright (c) 2026 Witalis Domitrz <witekdomitrz@gmail.com>
# AGPL License
################################################################
#
# /// script
# dependencies = [
#   "typer",
# ]
# ///
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false
"""A small patdiff-like diff tool."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import BinaryIO, ClassVar, Literal, assert_never

import typer  # pyright: ignore[reportMissingImports]

app = typer.Typer()


type Kind = Literal["same", "prev", "next", "replace", "move_from", "move_to"]


@dataclass(frozen=True, kw_only=True)
class Range:
    kind: Kind
    prev: list[str] = field(default_factory=list)
    next: list[str] = field(default_factory=list)
    move_id: int = 0

    def prev_size(self) -> int:
        match self.kind:
            case "same" | "prev" | "replace" | "move_from":
                return len(self.prev)
            case "next" | "move_to":
                return 0
            case _:
                assert_never(self.kind)

    def next_size(self) -> int:
        match self.kind:
            case "same":
                return len(self.prev)
            case "next" | "replace" | "move_to":
                return len(self.next)
            case "prev" | "move_from":
                return 0
            case _:
                assert_never(self.kind)

    def advance(self, *, prev_line: int, next_line: int) -> tuple[int, int]:
        return prev_line + self.prev_size(), next_line + self.next_size()

    def is_move_source_candidate(self) -> bool:
        match self.kind:
            case "prev":
                return len(self.prev) >= MIN_MOVE_LINES
            case "same" | "next" | "replace" | "move_from" | "move_to":
                return False
            case _:
                assert_never(self.kind)

    def is_move_target_candidate(self) -> bool:
        match self.kind:
            case "next":
                return len(self.next) >= MIN_MOVE_LINES
            case "same" | "prev" | "replace" | "move_from" | "move_to":
                return False
            case _:
                assert_never(self.kind)

    def move_source_key(self, *, ignore_whitespace: bool) -> str:
        return move_key(lines=self.prev, ignore_whitespace=ignore_whitespace)

    def move_target_key(self, *, ignore_whitespace: bool) -> str:
        return move_key(lines=self.next, ignore_whitespace=ignore_whitespace)

    def refine(self) -> RefinedReplace:
        return RefinedReplace.from_lines(prev_lines=self.prev, next_lines=self.next)


@dataclass(frozen=True, kw_only=True)
class Hunk:
    prev_start: int
    prev_size: int
    next_start: int
    next_size: int
    ranges: list[Range]

    @classmethod
    def from_ranges(
        cls, *, ranges: list[Range], prev_start: int, next_start: int
    ) -> Hunk:
        return cls(
            prev_start=prev_start,
            prev_size=sum(r.prev_size() for r in ranges),
            next_start=next_start,
            next_size=sum(r.next_size() for r in ranges),
            ranges=ranges,
        )

    def header(self) -> str:
        return f"@@ -{self.prev_start},{self.prev_size} +{self.next_start},{self.next_size} @@{HUNK_SEPARATOR}"


@dataclass(frozen=True, kw_only=True)
class Segment:
    kind: Kind
    text: str


RefinedLine = list[Segment]


@dataclass(frozen=True, kw_only=True)
class RefinedReplace:
    prev: list[RefinedLine]
    next: list[RefinedLine]

    @classmethod
    def from_lines(cls, *, prev_lines: list[str], next_lines: list[str]) -> RefinedReplace:
        sentinel = "\n"

        def flatten(lines: list[str]) -> list[str]:
            tokens: list[str] = []
            for line in lines:
                tokens.extend(tokenize(line))
                tokens.append(sentinel)
            return tokens

        token_ranges = diff(prev=flatten(prev_lines), next_=flatten(next_lines))
        return cls(
            prev=cls.collapse_tokens(
                token_ranges=token_ranges, prev_side=True, sentinel=sentinel
            ),
            next=cls.collapse_tokens(
                token_ranges=token_ranges, prev_side=False, sentinel=sentinel
            ),
        )

    @classmethod
    def collapse_tokens(
        cls, *, token_ranges: list[Range], prev_side: bool, sentinel: str
    ) -> list[RefinedLine]:
        lines: list[RefinedLine] = []
        cur: list[Segment] = []

        def emit(kind: Kind, text: str) -> None:
            nonlocal cur
            if text == sentinel:
                lines.append(list(cur))
                cur = []
            elif cur and cur[-1].kind == kind:
                cur[-1] = replace(cur[-1], text=cur[-1].text + text)
            else:
                cur.append(Segment(kind=kind, text=text))

        for r in token_ranges:
            match r.kind:
                case "same":
                    for tok in r.prev:
                        emit("same", tok)
                case "prev":
                    if prev_side:
                        for tok in r.prev:
                            emit("prev", tok)
                case "next":
                    if not prev_side:
                        for tok in r.next:
                            emit("next", tok)
                case "replace":
                    for tok in r.prev if prev_side else r.next:
                        emit("prev" if prev_side else "next", tok)
                case "move_from" | "move_to":
                    pass
                case _:
                    assert_never(r.kind)
        if cur:
            lines.append(list(cur))
        return lines

    def is_whitespace_only(self) -> bool:
        for line in self.prev:
            for seg in line:
                match seg.kind:
                    case "same":
                        pass
                    case "prev":
                        if seg.text.strip():
                            return False
                    case "next" | "replace" | "move_from" | "move_to":
                        pass
                    case _:
                        assert_never(seg.kind)
        for line in self.next:
            for seg in line:
                match seg.kind:
                    case "same":
                        pass
                    case "next":
                        if seg.text.strip():
                            return False
                    case "prev" | "replace" | "move_from" | "move_to":
                        pass
                    case _:
                        assert_never(seg.kind)
        return True

    def unified_lines(self) -> list[RefinedLine]:
        return self.next or self.prev


@dataclass(frozen=True, kw_only=True)
class MoveCandidate:
    range_index: int
    start_line: int


HUNK_SEPARATOR = " ============================================================"
MIN_MOVE_LINES = 3
NULL_SHA = "."
GIT_CONTEXT = 3
def diff(*, prev: list[str], next_: list[str]) -> list[Range]:
    """
    >>> [r.kind for r in diff(prev=["a", "b", "c"], next_=["a", "X", "c"])]
    ['same', 'replace', 'same']
    """
    return patience_diff(
        prev=prev, next_=next_, p0=0, p1=len(prev), n0=0, n1=len(next_)
    )


def patience_diff(
    *, prev: list[str], next_: list[str], p0: int, p1: int, n0: int, n1: int
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
        ranges = append_same(ranges=ranges, lines=prev[p0 : p0 + prefix_len])
    ranges.extend(
        patience_diff_middle(
            prev=prev,
            next_=next_,
            p0=p0 + prefix_len,
            p1=p1 - suffix_len,
            n0=n0 + prefix_len,
            n1=n1 - suffix_len,
        )
    )
    if suffix_len:
        ranges = append_same(ranges=ranges, lines=prev[p1 - suffix_len : p1])
    return ranges


def patience_diff_middle(
    *, prev: list[str], next_: list[str], p0: int, p1: int, n0: int, n1: int
) -> list[Range]:
    if p0 == p1 and n0 == n1:
        return []
    if p0 == p1:
        return [Range(kind="next", next=next_[n0:n1])]
    if n0 == n1:
        return [Range(kind="prev", prev=prev[p0:p1])]

    unique_prev = unique_lines(lines=prev, lo=p0, hi=p1)
    unique_next = unique_lines(lines=next_, lo=n0, hi=n1)
    matches = sorted(
        (pi, unique_next[line])
        for line, pi in unique_prev.items()
        if line in unique_next
    )
    if not matches:
        return [Range(kind="replace", prev=prev[p0:p1], next=next_[n0:n1])]

    anchors = [matches[i] for i in lis([ni for _, ni in matches])]
    ranges: list[Range] = []
    cur_p, cur_n = p0, n0
    for pi, ni in anchors:
        ranges.extend(
            patience_diff(prev=prev, next_=next_, p0=cur_p, p1=pi, n0=cur_n, n1=ni)
        )
        ranges = append_same(ranges=ranges, lines=[prev[pi]])
        cur_p, cur_n = pi + 1, ni + 1
    ranges.extend(
        patience_diff(prev=prev, next_=next_, p0=cur_p, p1=p1, n0=cur_n, n1=n1)
    )
    return ranges


def unique_lines(*, lines: list[str], lo: int, hi: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    index: dict[str, int] = {}
    for i in range(lo, hi):
        counts[lines[i]] = counts.get(lines[i], 0) + 1
        index[lines[i]] = i
    return {line: index[line] for line, count in counts.items() if count == 1}


def append_same(*, ranges: list[Range], lines: list[str]) -> list[Range]:
    if not lines:
        return ranges
    subject = ranges[-1].kind if ranges else None
    match subject:
        case "same":
            ranges[-1] = replace(ranges[-1], prev=ranges[-1].prev + lines)
        case None | "prev" | "next" | "replace" | "move_from" | "move_to":
            ranges.append(Range(kind="same", prev=list(lines)))
        case _:
            assert_never(subject)
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


def make_hunks(*, flat_ranges: list[Range], context: int) -> list[Hunk]:
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
            current.append(Range(kind="same", prev=list(tail)))
        hunks.append(
            Hunk.from_ranges(
                ranges=list(current),
                prev_start=hunk_prev_start,
                next_start=hunk_next_start,
            )
        )
        current = []
        in_hunk = False

    for r in flat_ranges:
        match r.kind:
            case "same":
                if not in_hunk:
                    prefix.extend(r.prev)
                    prev_line, next_line = r.advance(
                        prev_line=prev_line, next_line=next_line
                    )
                    if len(prefix) > context:
                        prefix = prefix[-context:]
                else:
                    same_after.extend(r.prev)
                    prev_line, next_line = r.advance(
                        prev_line=prev_line, next_line=next_line
                    )
                    if len(same_after) > 2 * context:
                        snap = same_after
                        close_hunk(snap[:context])
                        prefix = snap[-context:] if context else []
                        same_after = []
                continue
            case "prev" | "next" | "replace" | "move_from" | "move_to":
                pass
            case _:
                assert_never(r.kind)

        if not in_hunk:
            hunk_prev_start = prev_line - len(prefix)
            hunk_next_start = next_line - len(prefix)
            current = []
            if prefix:
                current.append(Range(kind="same", prev=list(prefix)))
                prefix = []
            in_hunk = True
        elif same_after:
            current.append(Range(kind="same", prev=list(same_after)))
            same_after = []

        current.append(r)
        prev_line, next_line = r.advance(prev_line=prev_line, next_line=next_line)

    if in_hunk:
        close_hunk(same_after)
    return hunks


def detect_moves(*, ranges: list[Range], ignore_whitespace: bool) -> list[Range]:
    """
    >>> ranges = detect_moves(ranges=[Range(kind="same", prev=["h"]), Range(kind="prev", prev=["a", "b", "c"]), Range(kind="same", prev=["m"]), Range(kind="next", next=["a", "b", "c"])], ignore_whitespace=False)
    >>> [r.kind for r in ranges]
    ['same', 'move_from', 'same', 'move_to']
    """
    ranges = list(ranges)
    prev_buckets: dict[str, list[MoveCandidate]] = {}
    next_buckets: dict[str, list[MoveCandidate]] = {}
    prev_line = next_line = 1
    for i, r in enumerate(ranges):
        match r.kind:
            case "prev":
                if r.is_move_source_candidate():
                    key = r.move_source_key(ignore_whitespace=ignore_whitespace)
                    prev_buckets.setdefault(key, []).append(
                        MoveCandidate(range_index=i, start_line=prev_line)
                    )
                prev_line, next_line = r.advance(
                    prev_line=prev_line, next_line=next_line
                )
            case "next":
                if r.is_move_target_candidate():
                    key = r.move_target_key(ignore_whitespace=ignore_whitespace)
                    next_buckets.setdefault(key, []).append(
                        MoveCandidate(range_index=i, start_line=next_line)
                    )
                prev_line, next_line = r.advance(
                    prev_line=prev_line, next_line=next_line
                )
            case "same" | "replace":
                prev_line, next_line = r.advance(
                    prev_line=prev_line, next_line=next_line
                )
            case "move_from" | "move_to":
                prev_line, next_line = r.advance(
                    prev_line=prev_line, next_line=next_line
                )
            case _:
                assert_never(r.kind)

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
                rp, kind="move_from", move_id=move_id, next=rn.next
            )
            ranges[n.range_index] = replace(
                rn, kind="move_to", move_id=move_id, prev=rp.prev
            )
            move_id += 1

    unmatched_prevs, unmatched_nexts = unmatched_move_candidates(ranges=ranges)
    if len(unmatched_prevs) * len(unmatched_nexts) <= 40000:
        for p in unmatched_prevs:
            prev_kind = ranges[p.range_index].kind
            match prev_kind:
                case "prev":
                    pass
                case "same" | "next" | "replace" | "move_from" | "move_to":
                    continue
                case _:
                    assert_never(prev_kind)
            best_idx, best_score = -1, 0.0
            for j, n in enumerate(unmatched_nexts):
                next_kind = ranges[n.range_index].kind
                match next_kind:
                    case "next":
                        pass
                    case "same" | "prev" | "replace" | "move_from" | "move_to":
                        continue
                    case _:
                        assert_never(next_kind)
                score = block_similarity(
                    prev=ranges[p.range_index].prev,
                    next_=ranges[n.range_index].next,
                    ignore_whitespace=ignore_whitespace,
                )
                if score >= 0.5 and score > best_score:
                    best_idx, best_score = j, score
            if best_idx != -1:
                n = unmatched_nexts[best_idx]
                rp, rn = ranges[p.range_index], ranges[n.range_index]
                ranges[p.range_index] = replace(
                    rp, kind="move_from", move_id=move_id, next=rn.next
                )
                ranges[n.range_index] = replace(
                    rn, kind="move_to", move_id=move_id, prev=rp.prev
                )
                move_id += 1
    return ranges


def unmatched_move_candidates(
    *, ranges: list[Range]
) -> tuple[list[MoveCandidate], list[MoveCandidate]]:
    prevs: list[MoveCandidate] = []
    nexts: list[MoveCandidate] = []
    prev_line = next_line = 1
    for i, r in enumerate(ranges):
        match r.kind:
            case "prev":
                if r.is_move_source_candidate():
                    prevs.append(MoveCandidate(range_index=i, start_line=prev_line))
                prev_line, next_line = r.advance(
                    prev_line=prev_line, next_line=next_line
                )
            case "next":
                if r.is_move_target_candidate():
                    nexts.append(MoveCandidate(range_index=i, start_line=next_line))
                prev_line, next_line = r.advance(
                    prev_line=prev_line, next_line=next_line
                )
            case "same" | "replace" | "move_from" | "move_to":
                prev_line, next_line = r.advance(
                    prev_line=prev_line, next_line=next_line
                )
            case _:
                assert_never(r.kind)
    return prevs, nexts


def block_similarity(
    *, prev: list[str], next_: list[str], ignore_whitespace: bool
) -> float:
    prev_norm = normalize_lines(prev) if ignore_whitespace else list(prev)
    next_norm = normalize_lines(next_) if ignore_whitespace else list(next_)
    same = 0
    for r in diff(prev=prev_norm, next_=next_norm):
        match r.kind:
            case "same":
                same += len(r.prev)
            case "prev" | "next" | "replace" | "move_from" | "move_to":
                pass
            case _:
                assert_never(r.kind)
    return same / max(len(prev), len(next_))


def normalize_lines(lines: list[str]) -> list[str]:
    return [strip_all_whitespace(line) for line in lines]


def move_key(*, lines: list[str], ignore_whitespace: bool) -> str:
    return "\n".join(normalize_lines(lines) if ignore_whitespace else lines)


def render_unified_diff(
    *, prev_name: str, next_name: str, hunks: list[Hunk], color: bool
) -> str:
    if not hunks:
        return ""
    out = [f"------ {prev_name}\n", f"++++++ {next_name}\n"]
    for h in hunks:
        write_line(out=out, kind="hunk", text=h.header(), color=color)
        for r in h.ranges:
            match r.kind:
                case "same":
                    write_plain_lines(out=out, kind="same", lines=r.prev, color=color)
                case "prev":
                    write_plain_lines(out=out, kind="prev", lines=r.prev, color=color)
                case "next":
                    write_plain_lines(out=out, kind="next", lines=r.next, color=color)
                case "replace":
                    rr = r.refine()
                    if rr.is_whitespace_only():
                        for line in rr.unified_lines():
                            write_line(
                                out=out,
                                kind="unified",
                                text=refined_plain(line),
                                color=color,
                            )
                    else:
                        for line in rr.prev:
                            write_refined_line(
                                out=out, kind="prev", line=line, color=color
                            )
                        for line in rr.next:
                            write_refined_line(
                                out=out, kind="next", line=line, color=color
                            )
                case "move_from":
                    for line in r.refine().prev:
                        write_refined_line(
                            out=out, kind="move_from", line=line, color=color
                        )
                case "move_to":
                    for line in r.refine().next:
                        write_refined_line(
                            out=out, kind="move_to", line=line, color=color
                        )
                case _:
                    assert_never(r.kind)
    return "".join(out)


def write_plain_lines(
    *, out: list[str], kind: str, lines: list[str], color: bool
) -> None:
    for line in lines:
        write_line(out=out, kind=kind, text=line, color=color)


def write_line(*, out: list[str], kind: str, text: str, color: bool) -> None:
    prefix = line_prefix(kind)
    if color:
        prefix = ansi(line_prefix_style(kind), prefix)
        style = line_text_style(kind)
        if style:
            text = ansi(style, text)
    out.append(f"{prefix} {text}\n")


def write_refined_line(
    *, out: list[str], kind: str, line: RefinedLine, color: bool
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
    match line_kind:
        case "prev":
            match seg_kind:
                case "same":
                    return "90"
                case "prev" | "next" | "replace" | "move_from" | "move_to":
                    return "31"
                case _:
                    assert_never(seg_kind)
        case "next":
            match seg_kind:
                case "same":
                    return ""
                case "prev" | "next" | "replace" | "move_from" | "move_to":
                    return "32"
                case _:
                    assert_never(seg_kind)
        case "move_from":
            match seg_kind:
                case "same":
                    return "90"
                case "prev" | "next" | "replace" | "move_from" | "move_to":
                    return "31;1"
                case _:
                    assert_never(seg_kind)
        case "move_to":
            match seg_kind:
                case "same":
                    return "33"
                case "prev" | "next" | "replace" | "move_from" | "move_to":
                    return "32;1"
                case _:
                    assert_never(seg_kind)
        case _:
            return ""


def ansi(style: str, s: str) -> str:
    return f"\x1b[{style}m{s}\x1b[0m"


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


def refine_unified_diff_input(*, data: bytes, color: bool) -> str:
    """
    >>> print(refine_unified_diff_input(data=b\"\"\"--- a/x
    ... +++ b/x
    ... @@ -1,1 +1,1 @@
    ... -old token
    ... +new token
    ... \"\"\", color=False), end="")
    --- a/x
    +++ b/x
    @@ -1,1 +1,1 @@
    -old token
    +new token
    """
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
                write_unified_plain_line(out=out, prefix="+", text=line, color=color)
        elif not add_run:
            for line in del_run:
                write_unified_plain_line(out=out, prefix="-", text=line, color=color)
        else:
            rr = RefinedReplace.from_lines(prev_lines=del_run, next_lines=add_run)
            for line in rr.prev:
                write_unified_refined_line(out=out, prefix="-", line=line, color=color)
            for line in rr.next:
                write_unified_refined_line(out=out, prefix="+", line=line, color=color)
        del_run, add_run = [], []

    for line in lines:
        if line.startswith("@@"):
            flush()
            in_hunk = True
            write_unified_meta_line(out=out, line=line, color=color)
        elif in_hunk and line.startswith("-") and not line.startswith("---"):
            del_run.append(line[1:])
        elif in_hunk and line.startswith("+") and not line.startswith("+++"):
            add_run.append(line[1:])
        else:
            flush()
            write_unified_meta_line(out=out, line=line, color=color)
    flush()
    rendered = "".join(out)
    return rendered.rstrip("\n") if data[-1:] != b"\n" else rendered


def write_unified_meta_line(*, out: list[str], line: str, color: bool) -> None:
    out.append((ansi("1", line) if color and line.startswith("@@") else line) + "\n")


def write_unified_plain_line(
    *, out: list[str], prefix: str, text: str, color: bool
) -> None:
    line = prefix + text
    if color and prefix == "-":
        line = ansi("31", line)
    elif color and prefix == "+":
        line = ansi("32", line)
    out.append(line + "\n")


def write_unified_refined_line(
    *, out: list[str], prefix: str, line: RefinedLine, color: bool
) -> None:
    if not color:
        out.append(prefix + refined_plain(line) + "\n")
        return
    parts = [ansi("31" if prefix == "-" else "32", prefix)]
    for seg in line:
        text = seg.text
        if prefix == "-":
            seg_kind = seg.kind
            match seg_kind:
                case "same":
                    text = ansi("90", text)
                case "prev" | "next" | "replace" | "move_from" | "move_to":
                    text = ansi("31", text)
                case _:
                    assert_never(seg_kind)
        else:
            seg_kind = seg.kind
            match seg_kind:
                case "same":
                    pass
                case "prev" | "next" | "replace" | "move_from" | "move_to":
                    text = ansi("32", text)
                case _:
                    assert_never(seg_kind)
        parts.append(text)
    out.append("".join(parts) + "\n")


def diff_output(
    *,
    prev_data: bytes,
    next_data: bytes,
    prev_name: str,
    next_name: str,
    context: int,
    color: bool,
    ignore_whitespace: bool,
    find_moves: bool,
) -> tuple[str, bool]:
    """
    >>> out, changed = diff_output(prev_data=b"apple\\nbanana\\ncherry\\n", next_data=b"apple\\nBANANA\\ncherry\\n", prev_name="old.txt", next_name="new.txt", context=16, color=False, ignore_whitespace=False, find_moves=False)
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
    >>> diff_output(prev_data=b"x = 1\\n", next_data=b"x  = 1\\n", prev_name="old", next_name="new", context=16, color=False, ignore_whitespace=True, find_moves=False)
    ('', False)
    >>> print(diff_output(prev_data=b"x = 1\\n", next_data=b"x  = 1\\n", prev_name="old", next_name="new", context=16, color=False, ignore_whitespace=False, find_moves=False)[0], end="")
    ------ old
    ++++++ new
    @| @@ -1,1 +1,1 @@ ============================================================
    !| x  = 1
    >>> "\\x1b[41m-|" in diff_output(prev_data=b"a\\n", next_data=b"b\\n", prev_name="old", next_name="new", context=16, color=True, ignore_whitespace=False, find_moves=False)[0]
    True
    """
    if prev_data == next_data:
        return "", False
    if is_binary(prev_data) or is_binary(next_data):
        return f"Binary files {prev_name} and {next_name} differ\n", True
    ranges = diff_ranges(
        prev_lines=split_lines(prev_data),
        next_lines=split_lines(next_data),
        ignore_whitespace=ignore_whitespace,
    )
    if find_moves:
        ranges = detect_moves(ranges=ranges, ignore_whitespace=ignore_whitespace)
    hunks = make_hunks(flat_ranges=ranges, context=context)
    if not hunks:
        return "", False
    return (
        render_unified_diff(
            prev_name=prev_name, next_name=next_name, hunks=hunks, color=color
        ),
        True,
    )


def diff_ranges(
    *, prev_lines: list[str], next_lines: list[str], ignore_whitespace: bool
) -> list[Range]:
    if not ignore_whitespace:
        return diff(prev=prev_lines, next_=next_lines)
    return remap_ranges_to_original(
        key_ranges=diff(
            prev=normalize_lines(prev_lines), next_=normalize_lines(next_lines)
        ),
        prev_orig=prev_lines,
        next_orig=next_lines,
    )


def remap_ranges_to_original(
    *, key_ranges: list[Range], prev_orig: list[str], next_orig: list[str]
) -> list[Range]:
    out: list[Range] = []
    pi = ni = 0
    for r in key_ranges:
        match r.kind:
            case "same":
                n = len(r.prev)
                out.append(Range(kind="same", prev=prev_orig[pi : pi + n]))
                pi += n
                ni += n
            case "prev":
                n = len(r.prev)
                out.append(Range(kind="prev", prev=prev_orig[pi : pi + n]))
                pi += n
            case "next":
                n = len(r.next)
                out.append(Range(kind="next", next=next_orig[ni : ni + n]))
                ni += n
            case "replace":
                pn, nn = len(r.prev), len(r.next)
                out.append(
                    Range(
                        kind="replace",
                        prev=prev_orig[pi : pi + pn],
                        next=next_orig[ni : ni + nn],
                    )
                )
                pi += pn
                ni += nn
            case "move_from" | "move_to":
                pass
            case _:
                assert_never(r.kind)
    return out


def split_lines(data: bytes) -> list[str]:
    """
    >>> split_lines(b"apple\\nbanana\\n")
    ['apple', 'banana']
    """
    if not data:
        return []
    text = data.decode().rstrip("\n")
    return [] if text == "" else text.split("\n")


def strip_all_whitespace(s: str) -> str:
    return "".join(ch for ch in s if not ch.isspace())


def is_binary(data: bytes) -> bool:
    return b"\0" in data


def resolve_color(*, mode: str, stdout: BinaryIO, git_mode: bool) -> bool:
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


def read_git_side(*, sha: str, path: str) -> bytes:
    if sha == NULL_SHA:
        return b""
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise OSError(f"read {path}: {exc}") from exc


def run_git_mode(
    *,
    args: list[str],
    context: int,
    color: bool,
    ignore_whitespace: bool,
    find_moves: bool,
) -> tuple[str, int]:
    if len(args) < 7 or len(args) > 9:
        raise ValueError(GitArgs.USAGE)
    path, old_file, old_hex, old_mode, new_file, new_hex, new_mode = args[:7]
    new_path = args[7] if len(args) >= 8 else path
    info = args[8] if len(args) >= 9 else ""
    prev_name, next_name = "a/" + path, "b/" + new_path
    is_new_file = old_hex == NULL_SHA
    is_deleted_file = new_hex == NULL_SHA
    prev_data = read_git_side(sha=old_hex, path=old_file)
    next_data = read_git_side(sha=new_hex, path=new_file)
    diff_out, diff_changed = diff_output(
        prev_data=prev_data,
        next_data=next_data,
        prev_name=prev_name,
        next_name=next_name,
        context=context,
        color=color,
        ignore_whitespace=ignore_whitespace,
        find_moves=find_moves,
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
    old_path: str
    new_path: str
    context: int = 16
    find_moves: bool = True
    color: str = "auto"
    whitespace: bool = False

    def __post_init__(self) -> None:
        self.main()

    def main(self) -> None:
        if self.context < 0:
            typer.echo("pdiff: --context must be >= 0", err=True)
            raise typer.Exit(2)
        try:
            use_color = resolve_color(
                mode=self.color, stdout=sys.stdout.buffer, git_mode=False
            )
        except ValueError as exc:
            typer.echo(f"pdiff: {exc}", err=True)
            raise typer.Exit(2) from exc
        ignore_ws = not self.whitespace
        try:
            prev_data = Path(self.old_path).read_bytes()
            next_data = Path(self.new_path).read_bytes()
        except OSError as exc:
            target = (
                self.old_path if not Path(self.old_path).exists() else self.new_path
            )
            typer.echo(f"pdiff: read {target}: {exc}", err=True)
            raise typer.Exit(2) from exc
        out, changed = diff_output(
            prev_data=prev_data,
            next_data=next_data,
            prev_name=self.old_path,
            next_name=self.new_path,
            context=self.context,
            color=use_color,
            ignore_whitespace=ignore_ws,
            find_moves=self.find_moves,
        )
        if out:
            sys.stdout.write(out)
        raise typer.Exit(1 if changed else 0)


@dataclass(frozen=True, kw_only=True)
class StdinArgs:
    color: str = "auto"

    def __post_init__(self) -> None:
        self.main()

    def main(self) -> None:
        try:
            use_color = resolve_color(
                mode=self.color, stdout=sys.stdout.buffer, git_mode=False
            )
        except ValueError as exc:
            typer.echo(f"pdiff: {exc}", err=True)
            raise typer.Exit(2) from exc
        sys.stdout.write(
            refine_unified_diff_input(data=sys.stdin.buffer.read(), color=use_color)
        )
        raise typer.Exit(0)


@dataclass(frozen=True, kw_only=True)
class GitArgs:
    USAGE: ClassVar[str] = "usage: pdiff git path old-file old-hex old-mode new-file new-hex new-mode [new-path] [info]"

    path: str
    old_file: str
    old_hex: str
    old_mode: str
    new_file: str
    new_hex: str
    new_mode: str
    new_path: str | None = None
    info: str | None = None
    context: int = GIT_CONTEXT
    find_moves: bool = True
    color: str = "auto"
    whitespace: bool = False

    def __post_init__(self) -> None:
        self.main()

    def main(self) -> None:
        if self.context < 0:
            typer.echo("pdiff: --context must be >= 0", err=True)
            raise typer.Exit(2)
        args = [
            self.path,
            self.old_file,
            self.old_hex,
            self.old_mode,
            self.new_file,
            self.new_hex,
            self.new_mode,
        ]
        if self.new_path is not None:
            args.append(self.new_path)
        if self.info is not None:
            args.append(self.info)
        try:
            use_color = resolve_color(
                mode=self.color, stdout=sys.stdout.buffer, git_mode=True
            )
            out, _code = run_git_mode(
                args=args,
                context=self.context,
                color=use_color,
                ignore_whitespace=not self.whitespace,
                find_moves=self.find_moves,
            )
        except (OSError, ValueError) as exc:
            typer.echo(f"pdiff: {exc}", err=True)
            raise typer.Exit(2) from exc
        if out:
            sys.stdout.write(out)
        raise typer.Exit(0)


@app.command(name="diff")
def diff_cmd(
    old_path: str,
    new_path: str,
    context: int = 16,
    find_moves: bool = True,
    color: str = "auto",
    whitespace: bool = False,
) -> None:
    Args(
        old_path=old_path,
        new_path=new_path,
        context=context,
        find_moves=find_moves,
        color=color,
        whitespace=whitespace,
    )


@app.command()
def stdin(color: str = "auto") -> None:
    StdinArgs(color=color)


@app.command()
def git(
    path: str,
    old_file: str,
    old_hex: str,
    old_mode: str,
    new_file: str,
    new_hex: str,
    new_mode: str,
    new_path: str | None = None,
    info: str | None = None,
    context: int = GIT_CONTEXT,
    find_moves: bool = True,
    color: str = "auto",
    whitespace: bool = False,
) -> None:
    GitArgs(
        path=path,
        old_file=old_file,
        old_hex=old_hex,
        old_mode=old_mode,
        new_file=new_file,
        new_hex=new_hex,
        new_mode=new_mode,
        new_path=new_path,
        info=info,
        context=context,
        find_moves=find_moves,
        color=color,
        whitespace=whitespace,
    )


if __name__ == "__main__":
    app()
