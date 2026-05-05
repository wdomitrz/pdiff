#!/usr/bin/env python3
################################################################
# Copyright (c) 2026 Witalis Domitrz <witekdomitrz@gmail.com>
# AGPL License
################################################################

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import BinaryIO, ClassVar, Literal, Protocol, assert_never, cast

Kind = Literal["same", "prev", "next", "replace", "move_from", "move_to"]
Command = Literal["diff", "stdin", "git"]


class Subparsers(Protocol):
    def add_parser(self, name: str) -> argparse.ArgumentParser: ...


@dataclass(frozen=True, kw_only=True)
class Range:
    MIN_MOVE_LINES: ClassVar[int] = 3

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
                return len(self.prev) >= self.MIN_MOVE_LINES
            case "same" | "next" | "replace" | "move_from" | "move_to":
                return False
            case _:
                assert_never(self.kind)

    def is_move_target_candidate(self) -> bool:
        match self.kind:
            case "next":
                return len(self.next) >= self.MIN_MOVE_LINES
            case "same" | "prev" | "replace" | "move_from" | "move_to":
                return False
            case _:
                assert_never(self.kind)

    def move_source_key(self, *, ignore_whitespace: bool) -> str:
        return Text.move_key(lines=self.prev, ignore_whitespace=ignore_whitespace)

    def move_target_key(self, *, ignore_whitespace: bool) -> str:
        return Text.move_key(lines=self.next, ignore_whitespace=ignore_whitespace)

    def refine(self) -> RefinedReplace:
        return RefinedReplace.from_lines(prev_lines=self.prev, next_lines=self.next)


@dataclass(frozen=True, kw_only=True)
class Hunk:
    SEPARATOR: ClassVar[str] = (
        " ============================================================"
    )

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

    @classmethod
    def from_flat_ranges(cls, *, flat_ranges: list[Range], context: int) -> list[Hunk]:
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
                cls.from_ranges(
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

    def header(self) -> str:
        return f"@@ -{self.prev_start},{self.prev_size} +{self.next_start},{self.next_size} @@{self.SEPARATOR}"


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
    def from_lines(
        cls, *, prev_lines: list[str], next_lines: list[str]
    ) -> RefinedReplace:
        sentinel = "\n"

        def flatten(lines: list[str]) -> list[str]:
            tokens: list[str] = []
            for line in lines:
                tokens.extend(Text.tokenize(line))
                tokens.append(sentinel)
            return tokens

        token_ranges = LineDiff(
            prev=flatten(prev_lines), next=flatten(next_lines)
        ).ranges()
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


@dataclass(frozen=True, kw_only=True)
class LineDiff:
    prev: list[str]
    next: list[str]

    def ranges(self) -> list[Range]:
        """
        >>> [r.kind for r in LineDiff(prev=["a", "b", "c"], next=["a", "X", "c"]).ranges()]
        ['same', 'replace', 'same']
        """
        return self.patience(p0=0, p1=len(self.prev), n0=0, n1=len(self.next))

    def patience(self, *, p0: int, p1: int, n0: int, n1: int) -> list[Range]:
        prefix_len = 0
        while (
            p0 + prefix_len < p1
            and n0 + prefix_len < n1
            and self.prev[p0 + prefix_len] == self.next[n0 + prefix_len]
        ):
            prefix_len += 1

        suffix_len = 0
        while (
            p1 - suffix_len > p0 + prefix_len
            and n1 - suffix_len > n0 + prefix_len
            and self.prev[p1 - 1 - suffix_len] == self.next[n1 - 1 - suffix_len]
        ):
            suffix_len += 1

        ranges: list[Range] = []
        if prefix_len:
            ranges = self.append_same(
                ranges=ranges, lines=self.prev[p0 : p0 + prefix_len]
            )
        ranges.extend(
            self.middle(
                p0=p0 + prefix_len,
                p1=p1 - suffix_len,
                n0=n0 + prefix_len,
                n1=n1 - suffix_len,
            )
        )
        if suffix_len:
            ranges = self.append_same(
                ranges=ranges, lines=self.prev[p1 - suffix_len : p1]
            )
        return ranges

    def middle(self, *, p0: int, p1: int, n0: int, n1: int) -> list[Range]:
        if p0 == p1 and n0 == n1:
            return []
        if p0 == p1:
            return [Range(kind="next", next=self.next[n0:n1])]
        if n0 == n1:
            return [Range(kind="prev", prev=self.prev[p0:p1])]

        unique_prev = self.unique_lines(lines=self.prev, lo=p0, hi=p1)
        unique_next = self.unique_lines(lines=self.next, lo=n0, hi=n1)
        matches = sorted(
            (pi, unique_next[line])
            for line, pi in unique_prev.items()
            if line in unique_next
        )
        if not matches:
            return [Range(kind="replace", prev=self.prev[p0:p1], next=self.next[n0:n1])]

        anchors = [
            matches[i]
            for i in self.longest_increasing_subsequence([ni for _, ni in matches])
        ]
        ranges: list[Range] = []
        cur_p, cur_n = p0, n0
        for pi, ni in anchors:
            ranges.extend(self.patience(p0=cur_p, p1=pi, n0=cur_n, n1=ni))
            ranges = self.append_same(ranges=ranges, lines=[self.prev[pi]])
            cur_p, cur_n = pi + 1, ni + 1
        ranges.extend(self.patience(p0=cur_p, p1=p1, n0=cur_n, n1=n1))
        return ranges

    @staticmethod
    def unique_lines(*, lines: list[str], lo: int, hi: int) -> dict[str, int]:
        counts: dict[str, int] = {}
        index: dict[str, int] = {}
        for i in range(lo, hi):
            counts[lines[i]] = counts.get(lines[i], 0) + 1
            index[lines[i]] = i
        return {line: index[line] for line, count in counts.items() if count == 1}

    @staticmethod
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

    @staticmethod
    def longest_increasing_subsequence(values: list[int]) -> list[int]:
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


@dataclass(frozen=True, kw_only=True)
class MoveDetector:
    MAX_FUZZY_MOVE_COMPARISONS: ClassVar[int] = 40_000
    MIN_FUZZY_MOVE_SIMILARITY: ClassVar[float] = 0.5

    ranges: list[Range]
    ignore_whitespace: bool

    def detect(self) -> list[Range]:
        """
        >>> ranges = MoveDetector(ranges=[Range(kind="same", prev=["h"]), Range(kind="prev", prev=["a", "b", "c"]), Range(kind="same", prev=["m"]), Range(kind="next", next=["a", "b", "c"])], ignore_whitespace=False).detect()
        >>> [r.kind for r in ranges]
        ['same', 'move_from', 'same', 'move_to']
        """
        ranges = list(self.ranges)
        prev_buckets: dict[str, list[MoveCandidate]] = {}
        next_buckets: dict[str, list[MoveCandidate]] = {}
        prev_line = next_line = 1
        for i, r in enumerate(ranges):
            match r.kind:
                case "prev":
                    if r.is_move_source_candidate():
                        key = r.move_source_key(
                            ignore_whitespace=self.ignore_whitespace
                        )
                        prev_buckets.setdefault(key, []).append(
                            MoveCandidate(range_index=i, start_line=prev_line)
                        )
                    prev_line, next_line = r.advance(
                        prev_line=prev_line, next_line=next_line
                    )
                case "next":
                    if r.is_move_target_candidate():
                        key = r.move_target_key(
                            ignore_whitespace=self.ignore_whitespace
                        )
                        next_buckets.setdefault(key, []).append(
                            MoveCandidate(range_index=i, start_line=next_line)
                        )
                    prev_line, next_line = r.advance(
                        prev_line=prev_line, next_line=next_line
                    )
                case "same" | "replace" | "move_from" | "move_to":
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

        unmatched_prevs, unmatched_nexts = self.unmatched_candidates(ranges=ranges)
        if (
            len(unmatched_prevs) * len(unmatched_nexts)
            <= self.MAX_FUZZY_MOVE_COMPARISONS
        ):
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
                    score = self.block_similarity(
                        prev=ranges[p.range_index].prev,
                        next_=ranges[n.range_index].next,
                    )
                    if score >= self.MIN_FUZZY_MOVE_SIMILARITY and score > best_score:
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

    @staticmethod
    def unmatched_candidates(
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

    def block_similarity(self, *, prev: list[str], next_: list[str]) -> float:
        prev_norm = Text.normalize_lines(prev) if self.ignore_whitespace else list(prev)
        next_norm = (
            Text.normalize_lines(next_) if self.ignore_whitespace else list(next_)
        )
        same = 0
        for r in LineDiff(prev=prev_norm, next=next_norm).ranges():
            match r.kind:
                case "same":
                    same += len(r.prev)
                case "prev" | "next" | "replace" | "move_from" | "move_to":
                    pass
                case _:
                    assert_never(r.kind)
        return same / max(len(prev), len(next_))


class Text:
    @staticmethod
    def normalize_lines(lines: list[str]) -> list[str]:
        return [Text.strip_all_whitespace(line) for line in lines]

    @staticmethod
    def move_key(*, lines: list[str], ignore_whitespace: bool) -> str:
        return "\n".join(Text.normalize_lines(lines) if ignore_whitespace else lines)

    @staticmethod
    def split_lines(data: bytes) -> list[str]:
        """
        >>> Text.split_lines(b"apple\\nbanana\\n")
        ['apple', 'banana']
        """
        if not data:
            return []
        text = data.decode().rstrip("\n")
        return [] if text == "" else text.split("\n")

    @staticmethod
    def strip_all_whitespace(s: str) -> str:
        return "".join(ch for ch in s if not ch.isspace())

    @staticmethod
    def is_binary(data: bytes) -> bool:
        return b"\0" in data

    @staticmethod
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
                tokens.extend(Text.split_numeric_literal(line[i:j]))
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

    @staticmethod
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


@dataclass(frozen=True, kw_only=True)
class UnifiedRenderer:
    LineKind = Literal[
        "same", "prev", "next", "unified", "hunk", "move_from", "move_to"
    ]
    RefinedLineKind = Literal["prev", "next", "move_from", "move_to"]
    CLEAR_EOL: ClassVar[str] = "\x1b[0m \x1b[0m\x1b[K"

    prev_name: str
    next_name: str
    hunks: list[Hunk]
    color: bool

    def render(self) -> str:
        if not self.hunks:
            return ""
        out = [f"------ {self.prev_name}\n", f"++++++ {self.next_name}\n"]
        for hunk in self.hunks:
            self.write_line(out=out, kind="hunk", text=hunk.header())
            for r in hunk.ranges:
                match r.kind:
                    case "same":
                        self.write_plain_lines(out=out, kind="same", lines=r.prev)
                    case "prev":
                        self.write_plain_lines(out=out, kind="prev", lines=r.prev)
                    case "next":
                        self.write_plain_lines(out=out, kind="next", lines=r.next)
                    case "replace":
                        rr = r.refine()
                        if rr.is_whitespace_only():
                            for line in rr.unified_lines():
                                self.write_line(
                                    out=out,
                                    kind="unified",
                                    text=self.refined_plain(line),
                                )
                        else:
                            for line in rr.prev:
                                self.write_refined_line(out=out, kind="prev", line=line)
                            for line in rr.next:
                                self.write_refined_line(out=out, kind="next", line=line)
                    case "move_from":
                        for line in r.refine().prev:
                            self.write_refined_line(
                                out=out, kind="move_from", line=line
                            )
                    case "move_to":
                        for line in r.refine().next:
                            self.write_refined_line(out=out, kind="move_to", line=line)
                    case _:
                        assert_never(r.kind)
        return "".join(out)

    def write_plain_lines(
        self, *, out: list[str], kind: LineKind, lines: list[str]
    ) -> None:
        for line in lines:
            self.write_line(out=out, kind=kind, text=line)

    def write_line(self, *, out: list[str], kind: LineKind, text: str) -> None:
        prefix = self.line_prefix(kind)
        if self.color:
            prefix = self.ansi(self.line_prefix_style(kind), prefix)
            style = self.line_text_style(kind)
            if style and text:
                text = self.ansi(style, text)
        out.append(
            f"{prefix}{self.end_line()}"
            if text == ""
            else f"{prefix} {text}{self.end_line()}"
        )

    def write_refined_line(
        self, *, out: list[str], kind: RefinedLineKind, line: RefinedLine
    ) -> None:
        prefix = self.line_prefix(kind)
        if self.color:
            prefix = self.ansi(self.line_prefix_style(kind), prefix)
        if not line:
            out.append(prefix + self.end_line())
            return
        parts = [prefix, " "]
        for seg in line:
            text = seg.text
            if self.color:
                style = self.refined_segment_style(kind, seg.kind)
                if style:
                    text = self.ansi(style, text)
            parts.append(text)
        parts.append(self.end_line())
        out.append("".join(parts))

    def end_line(self) -> str:
        return self.CLEAR_EOL + "\n" if self.color else "\n"

    @staticmethod
    def line_prefix(kind: LineKind) -> str:
        match kind:
            case "same":
                return " |"
            case "prev":
                return "-|"
            case "next":
                return "+|"
            case "unified":
                return "!|"
            case "hunk":
                return "@|"
            case "move_from":
                return "<|"
            case "move_to":
                return ">|"
            case _:
                assert_never(kind)

    @staticmethod
    def line_prefix_style(kind: LineKind) -> str:
        match kind:
            case "same":
                return "100"
            case "prev":
                return "41"
            case "next":
                return "42"
            case "unified":
                return "43"
            case "hunk":
                return "100"
            case "move_from":
                return "45"
            case "move_to":
                return "46"
            case _:
                assert_never(kind)

    @staticmethod
    def line_text_style(kind: LineKind) -> str:
        match kind:
            case "prev":
                return "31"
            case "next":
                return "32"
            case "hunk":
                return "1"
            case "move_from":
                return "35"
            case "move_to":
                return "36"
            case "same" | "unified":
                return ""
            case _:
                assert_never(kind)

    @staticmethod
    def refined_segment_style(line_kind: RefinedLineKind, seg_kind: Kind) -> str:
        match subject := (line_kind, seg_kind):
            case (("prev" | "move_from"), "same"):
                return "90"
            case "prev", "prev" | "next" | "replace" | "move_from" | "move_to":
                return "31"
            case "next", "same":
                return ""
            case "next", "prev" | "next" | "replace" | "move_from" | "move_to":
                return "32"
            case (
                "move_from",
                "prev" | "next" | "replace" | "move_from" | "move_to",
            ):
                return "31;1"
            case "move_to", "same":
                return "33"
            case "move_to", "prev" | "next" | "replace" | "move_from" | "move_to":
                return "32;1"
            case _:
                assert_never(subject)

    @staticmethod
    def ansi(style: str, s: str) -> str:
        """
        >>> UnifiedRenderer.ansi("31", "red\\nline")
        '\\x1b[31mred\\x1b[0m\\n\\x1b[31mline\\x1b[0m'
        >>> UnifiedRenderer.ansi("90", "gray\\r\\n")
        '\\x1b[90mgray\\x1b[0m\\r\\n'
        >>> "\\x1b[31m\\n" in UnifiedRenderer.ansi("31", "red\\n")
        False
        """
        out: list[str] = []
        start = 0
        for i, char in enumerate(s):
            if char not in "\r\n":
                continue
            if start < i:
                out.append(f"\x1b[{style}m{s[start:i]}\x1b[0m")
            out.append(char)
            start = i + 1
        if start < len(s):
            out.append(f"\x1b[{style}m{s[start:]}\x1b[0m")
        if out:
            return "".join(out)
        return f"\x1b[{style}m{s}\x1b[0m"

    @classmethod
    def colored_line(cls, text: str) -> str:
        """
        >>> e = chr(27)
        >>> UnifiedRenderer.colored_line("x") == "x" + e + "[0m " + e + "[0m" + e + "[K" + chr(10)
        True
        >>> UnifiedRenderer.colored_line("x").endswith(e + "[0m " + e + "[0m" + e + "[K" + chr(10))
        True
        """
        return text + cls.CLEAR_EOL + "\n"

    @staticmethod
    def refined_plain(line: RefinedLine) -> str:
        return "".join(seg.text for seg in line)


@dataclass(frozen=True, kw_only=True)
class StdinDiffRefiner:
    Prefix = Literal["-", "+"]

    data: bytes
    color: bool

    def render(self) -> str:
        """
        >>> print(StdinDiffRefiner(data=b\"\"\"--- a/x
        ... +++ b/x
        ... @@ -1,1 +1,1 @@
        ... -old token
        ... +new token
        ... \"\"\", color=False).render(), end="")
        --- a/x
        +++ b/x
        @@ -1,1 +1,1 @@
        -old token
        +new token
        """
        if not self.data or not self.data.rstrip(b"\n"):
            return ""
        lines = self.data.decode().rstrip("\n").split("\n")
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
                    self.write_plain_line(out=out, prefix="+", text=line)
            elif not add_run:
                for line in del_run:
                    self.write_plain_line(out=out, prefix="-", text=line)
            else:
                rr = RefinedReplace.from_lines(prev_lines=del_run, next_lines=add_run)
                for line in rr.prev:
                    self.write_refined_line(out=out, prefix="-", line=line)
                for line in rr.next:
                    self.write_refined_line(out=out, prefix="+", line=line)
            del_run, add_run = [], []

        for line in lines:
            if line.startswith("@@"):
                flush()
                in_hunk = True
                self.write_meta_line(out=out, line=line)
            elif in_hunk and line.startswith("-") and not line.startswith("---"):
                del_run.append(line[1:])
            elif in_hunk and line.startswith("+") and not line.startswith("+++"):
                add_run.append(line[1:])
            else:
                flush()
                self.write_meta_line(out=out, line=line)
        flush()
        rendered = "".join(out)
        return rendered.rstrip("\n") if self.data[-1:] != b"\n" else rendered

    def write_meta_line(self, *, out: list[str], line: str) -> None:
        if self.color and line.startswith("@@"):
            line = UnifiedRenderer.ansi("1", line)
        out.append(UnifiedRenderer.colored_line(line) if self.color else line + "\n")

    def write_plain_line(self, *, out: list[str], prefix: Prefix, text: str) -> None:
        line = prefix + text
        if self.color and prefix == "-":
            line = UnifiedRenderer.ansi("31", line)
        elif self.color and prefix == "+":
            line = UnifiedRenderer.ansi("32", line)
        out.append(UnifiedRenderer.colored_line(line) if self.color else line + "\n")

    def write_refined_line(
        self, *, out: list[str], prefix: Prefix, line: RefinedLine
    ) -> None:
        if not self.color:
            out.append(prefix + UnifiedRenderer.refined_plain(line) + "\n")
            return
        parts = [UnifiedRenderer.ansi("31" if prefix == "-" else "32", prefix)]
        for seg in line:
            text = seg.text
            style = self.refined_segment_style(prefix=prefix, seg_kind=seg.kind)
            if style:
                text = UnifiedRenderer.ansi(style, text)
            parts.append(text)
        out.append(UnifiedRenderer.colored_line("".join(parts)))

    @staticmethod
    def refined_segment_style(*, prefix: Prefix, seg_kind: Kind) -> str:
        match subject := (prefix, seg_kind):
            case "-", "same":
                return "90"
            case "-", "prev" | "next" | "replace" | "move_from" | "move_to":
                return "31"
            case "+", "same":
                return ""
            case "+", "prev" | "next" | "replace" | "move_from" | "move_to":
                return "32"
            case _:
                assert_never(subject)


@dataclass(frozen=True, kw_only=True)
class FileDiff:
    prev_data: bytes
    next_data: bytes
    prev_name: str
    next_name: str
    context: int
    color: bool
    ignore_whitespace: bool
    find_moves: bool

    def output(self) -> tuple[str, bool]:
        """
        >>> out, changed = FileDiff(prev_data=b"apple\\nbanana\\ncherry\\n", next_data=b"apple\\nBANANA\\ncherry\\n", prev_name="old.txt", next_name="new.txt", context=16, color=False, ignore_whitespace=False, find_moves=False).output()
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
        >>> FileDiff(prev_data=b"x = 1\\n", next_data=b"x  = 1\\n", prev_name="old", next_name="new", context=16, color=False, ignore_whitespace=True, find_moves=False).output()
        ('', False)
        >>> print(FileDiff(prev_data=b"x = 1\\n", next_data=b"x  = 1\\n", prev_name="old", next_name="new", context=16, color=False, ignore_whitespace=False, find_moves=False).output()[0], end="")
        ------ old
        ++++++ new
        @| @@ -1,1 +1,1 @@ ============================================================
        !| x  = 1
        >>> "\\x1b[41m-|" in FileDiff(prev_data=b"a\\n", next_data=b"b\\n", prev_name="old", next_name="new", context=16, color=True, ignore_whitespace=False, find_moves=False).output()[0]
        True
        >>> colored = FileDiff(prev_data=b"old\\n\\nsame\\n", next_data=b"new\\nsame\\n", prev_name="old", next_name="new", context=16, color=True, ignore_whitespace=False, find_moves=False).output()[0]
        >>> "\\x1b[K\\n" in colored
        True
        >>> Test.has_active_color_at_eol(colored)
        False
        """
        if self.prev_data == self.next_data:
            return "", False
        if Text.is_binary(self.prev_data) or Text.is_binary(self.next_data):
            return f"Binary files {self.prev_name} and {self.next_name} differ\n", True
        ranges = self.ranges(
            prev_lines=Text.split_lines(self.prev_data),
            next_lines=Text.split_lines(self.next_data),
        )
        if self.find_moves:
            ranges = MoveDetector(
                ranges=ranges, ignore_whitespace=self.ignore_whitespace
            ).detect()
        hunks = Hunk.from_flat_ranges(flat_ranges=ranges, context=self.context)
        if not hunks:
            return "", False
        return (
            UnifiedRenderer(
                prev_name=self.prev_name,
                next_name=self.next_name,
                hunks=hunks,
                color=self.color,
            ).render(),
            True,
        )

    def ranges(self, *, prev_lines: list[str], next_lines: list[str]) -> list[Range]:
        if not self.ignore_whitespace:
            return LineDiff(prev=prev_lines, next=next_lines).ranges()
        return self.remap_ranges_to_original(
            key_ranges=LineDiff(
                prev=Text.normalize_lines(prev_lines),
                next=Text.normalize_lines(next_lines),
            ).ranges(),
            prev_orig=prev_lines,
            next_orig=next_lines,
        )

    @staticmethod
    def remap_ranges_to_original(
        *, key_ranges: list[Range], prev_orig: list[str], next_orig: list[str]
    ) -> list[Range]:
        out: list[Range] = []
        pi = ni = 0
        for r in key_ranges:
            match r.kind:
                case "same":
                    n = len(r.prev)
                    out.append(Range(kind="same", prev=next_orig[ni : ni + n]))
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


PathKind = Literal["file", "dir", "other", "missing"]


@dataclass(frozen=True, kw_only=True)
class PathDiff:
    prev_path: Path
    next_path: Path
    context: int
    color: bool
    ignore_whitespace: bool
    find_moves: bool

    def output(self) -> tuple[str, bool]:
        prev_kind = self.path_kind(self.prev_path)
        next_kind = self.path_kind(self.next_path)
        match (prev_kind, next_kind):
            case ("file", "file"):
                return FileDiff(
                    prev_data=self.prev_path.read_bytes(),
                    next_data=self.next_path.read_bytes(),
                    prev_name=str(self.prev_path),
                    next_name=str(self.next_path),
                    context=self.context,
                    color=self.color,
                    ignore_whitespace=self.ignore_whitespace,
                    find_moves=self.find_moves,
                ).output()
            case ("dir", "dir"):
                return DirectoryDiff(
                    prev_dir=self.prev_path,
                    next_dir=self.next_path,
                    context=self.context,
                    color=self.color,
                    ignore_whitespace=self.ignore_whitespace,
                    find_moves=self.find_moves,
                ).output()
            case ("missing", "file"):
                return FileDiff(
                    prev_data=b"",
                    next_data=self.next_path.read_bytes(),
                    prev_name="/dev/null",
                    next_name=str(self.next_path),
                    context=self.context,
                    color=self.color,
                    ignore_whitespace=self.ignore_whitespace,
                    find_moves=self.find_moves,
                ).output()
            case ("file", "missing"):
                return FileDiff(
                    prev_data=self.prev_path.read_bytes(),
                    next_data=b"",
                    prev_name=str(self.prev_path),
                    next_name="/dev/null",
                    context=self.context,
                    color=self.color,
                    ignore_whitespace=self.ignore_whitespace,
                    find_moves=self.find_moves,
                ).output()
            case _:
                return (
                    f"Files {self.prev_path} and {self.next_path} are not the same type\n",
                    True,
                )

    @staticmethod
    def path_kind(path: Path) -> PathKind:
        if path.is_file():
            return "file"
        if path.is_dir():
            return "dir"
        if path.exists():
            return "other"
        return "missing"


@dataclass(frozen=True, kw_only=True)
class DirectoryDiff:
    prev_dir: Path
    next_dir: Path
    context: int
    color: bool
    ignore_whitespace: bool
    find_moves: bool

    def output(self) -> tuple[str, bool]:
        out: list[str] = []
        changed = False
        prev_names = {path.name for path in self.prev_dir.iterdir()}
        next_names = {path.name for path in self.next_dir.iterdir()}

        for name in sorted(prev_names - next_names):
            changed = True
            out.append(f"Only in {self.prev_dir}: {name}\n")
            path = self.prev_dir / name
            if path.is_file():
                diff_out, _ = FileDiff(
                    prev_data=path.read_bytes(),
                    next_data=b"",
                    prev_name=str(path),
                    next_name="/dev/null",
                    context=self.context,
                    color=self.color,
                    ignore_whitespace=self.ignore_whitespace,
                    find_moves=self.find_moves,
                ).output()
                out.append(diff_out)

        for name in sorted(next_names - prev_names):
            changed = True
            out.append(f"Only in {self.next_dir}: {name}\n")
            path = self.next_dir / name
            if path.is_file():
                diff_out, _ = FileDiff(
                    prev_data=b"",
                    next_data=path.read_bytes(),
                    prev_name="/dev/null",
                    next_name=str(path),
                    context=self.context,
                    color=self.color,
                    ignore_whitespace=self.ignore_whitespace,
                    find_moves=self.find_moves,
                ).output()
                out.append(diff_out)

        for name in sorted(prev_names & next_names):
            diff_out, diff_changed = PathDiff(
                prev_path=self.prev_dir / name,
                next_path=self.next_dir / name,
                context=self.context,
                color=self.color,
                ignore_whitespace=self.ignore_whitespace,
                find_moves=self.find_moves,
            ).output()
            if diff_changed:
                changed = True
                out.append(diff_out)

        return "".join(out), changed


@dataclass(frozen=True, kw_only=True)
class GitExternalDiff:
    DEFAULT_CONTEXT: ClassVar[int] = 3
    NULL_SHA: ClassVar[str] = "."

    path: Path
    old_file: Path
    old_hex: str
    old_mode: str
    new_file: Path
    new_hex: str
    new_mode: str
    new_path: Path | None
    info: str | None
    context: int
    color: bool
    ignore_whitespace: bool
    find_moves: bool

    def run(self) -> tuple[str, int]:
        path = str(self.path)
        new_path = str(self.new_path) if self.new_path is not None else path
        prev_name, next_name = "a/" + path, "b/" + new_path
        is_new_file = self.old_hex == self.NULL_SHA
        is_deleted_file = self.new_hex == self.NULL_SHA
        diff_out, diff_changed = FileDiff(
            prev_data=self.read_side(sha=self.old_hex, path=self.old_file),
            next_data=self.read_side(sha=self.new_hex, path=self.new_file),
            prev_name=prev_name,
            next_name=next_name,
            context=self.context,
            color=self.color,
            ignore_whitespace=self.ignore_whitespace,
            find_moves=self.find_moves,
        ).output()

        meta: list[str] = []
        if is_new_file:
            meta.append("new file mode " + self.new_mode)
        elif is_deleted_file:
            meta.append("deleted file mode " + self.old_mode)
        elif self.old_mode != self.new_mode:
            meta.extend(["old mode " + self.old_mode, "new mode " + self.new_mode])
        if not diff_changed and not meta and not self.info:
            return "", 0

        title = f"pdiff -git {prev_name} {next_name}"
        title = UnifiedRenderer.ansi("1", title) if self.color else title
        out = [UnifiedRenderer.colored_line(title) if self.color else title + "\n"]
        out.extend(m + "\n" for m in meta)
        if self.info:
            out.append(self.info + "\n")
        if diff_changed and not is_new_file and not is_deleted_file:
            out.append(f"index {self.old_hex}..{self.new_hex}\n")
        out.append(diff_out)
        return "".join(out), 1

    @classmethod
    def read_side(cls, *, sha: str, path: Path) -> bytes:
        if sha == cls.NULL_SHA:
            return b""
        return path.read_bytes()


class Color:
    Mode = Literal["auto", "never", "always"]
    CHOICES: ClassVar[tuple[Mode, ...]] = ("auto", "never", "always")

    @staticmethod
    def use(*, color_mode: Mode, stdout: BinaryIO, git_mode: bool) -> bool:
        match color_mode:
            case "always":
                return True
            case "never":
                return False
            case "auto":
                return stdout.isatty() or (git_mode and Color.invoked_by_git())
            case _:
                assert_never(color_mode)

    @staticmethod
    def invoked_by_git() -> bool:
        return bool(
            os.environ.get("GIT_DIFF_PATH_COUNTER")
            or os.environ.get("GIT_EXTERNAL_DIFF")
        )


@dataclass(frozen=True, kw_only=True)
class Args:
    old_path: Path
    new_path: Path
    context: int = 16
    find_moves: bool = True
    color: Color.Mode = "auto"
    whitespace: bool = False

    @classmethod
    def add_parser(cls, subparsers: Subparsers) -> None:
        parser = subparsers.add_parser("diff")
        parser.add_argument("--context", type=int, default=16)
        parser.add_argument(
            "--find-moves",
            action=argparse.BooleanOptionalAction,
            default=True,
        )
        parser.add_argument("--color", choices=Color.CHOICES, default="auto")
        parser.add_argument(
            "--whitespace",
            action=argparse.BooleanOptionalAction,
            default=False,
        )
        parser.add_argument("old_path", type=Path)
        parser.add_argument("new_path", type=Path)

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> Args:
        return cls(
            old_path=cast(Path, namespace.old_path),
            new_path=cast(Path, namespace.new_path),
            context=cast(int, namespace.context),
            find_moves=cast(bool, namespace.find_moves),
            color=cast(Color.Mode, namespace.color),
            whitespace=cast(bool, namespace.whitespace),
        )

    def main(self) -> int:
        assert self.context >= 0

        out, changed = PathDiff(
            prev_path=self.old_path,
            next_path=self.new_path,
            context=self.context,
            color=Color.use(
                color_mode=self.color,
                stdout=sys.stdout.buffer,
                git_mode=False,
            ),
            ignore_whitespace=not self.whitespace,
            find_moves=self.find_moves,
        ).output()
        if out:
            sys.stdout.write(out)

        return 1 if changed else 0


@dataclass(frozen=True, kw_only=True)
class StdinArgs:
    color: Color.Mode = "auto"

    @classmethod
    def add_parser(cls, subparsers: Subparsers) -> None:
        parser = subparsers.add_parser("stdin")
        parser.add_argument("--color", choices=Color.CHOICES, default="auto")

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> StdinArgs:
        return cls(color=cast(Color.Mode, namespace.color))

    def main(self) -> int:
        sys.stdout.write(
            StdinDiffRefiner(
                data=sys.stdin.buffer.read(),
                color=Color.use(
                    color_mode=self.color,
                    stdout=sys.stdout.buffer,
                    git_mode=False,
                ),
            ).render()
        )
        return 0


@dataclass(frozen=True, kw_only=True)
class GitArgs:
    path: Path
    old_file: Path
    old_hex: str
    old_mode: str
    new_file: Path
    new_hex: str
    new_mode: str
    new_path: Path | None = None
    info: str | None = None
    context: int = GitExternalDiff.DEFAULT_CONTEXT
    find_moves: bool = True
    color: Color.Mode = "auto"
    whitespace: bool = False

    @classmethod
    def add_parser(cls, subparsers: Subparsers) -> None:
        parser = subparsers.add_parser("git")
        parser.add_argument(
            "--context", type=int, default=GitExternalDiff.DEFAULT_CONTEXT
        )
        parser.add_argument(
            "--find-moves",
            action=argparse.BooleanOptionalAction,
            default=True,
        )
        parser.add_argument("--color", choices=Color.CHOICES, default="auto")
        parser.add_argument(
            "--whitespace",
            action=argparse.BooleanOptionalAction,
            default=False,
        )
        parser.add_argument("path", type=Path)
        parser.add_argument("old_file", type=Path)
        parser.add_argument("old_hex")
        parser.add_argument("old_mode")
        parser.add_argument("new_file", type=Path)
        parser.add_argument("new_hex")
        parser.add_argument("new_mode")
        parser.add_argument("new_path", nargs="?", type=Path)
        parser.add_argument("info", nargs="?")

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> GitArgs:
        return cls(
            path=cast(Path, namespace.path),
            old_file=cast(Path, namespace.old_file),
            old_hex=cast(str, namespace.old_hex),
            old_mode=cast(str, namespace.old_mode),
            new_file=cast(Path, namespace.new_file),
            new_hex=cast(str, namespace.new_hex),
            new_mode=cast(str, namespace.new_mode),
            new_path=cast(Path | None, namespace.new_path),
            info=cast(str | None, namespace.info),
            context=cast(int, namespace.context),
            find_moves=cast(bool, namespace.find_moves),
            color=cast(Color.Mode, namespace.color),
            whitespace=cast(bool, namespace.whitespace),
        )

    def main(self) -> int:
        assert self.context >= 0
        out, _code = GitExternalDiff(
            path=self.path,
            old_file=self.old_file,
            old_hex=self.old_hex,
            old_mode=self.old_mode,
            new_file=self.new_file,
            new_hex=self.new_hex,
            new_mode=self.new_mode,
            new_path=self.new_path,
            info=self.info,
            context=self.context,
            color=Color.use(
                color_mode=self.color,
                stdout=sys.stdout.buffer,
                git_mode=True,
            ),
            ignore_whitespace=not self.whitespace,
            find_moves=self.find_moves,
        ).run()
        if out:
            sys.stdout.write(out)
        return 0


CLIArgs = Args | StdinArgs | GitArgs


class CLI:
    @classmethod
    def from_argv(cls, argv: list[str] | None = None) -> CLIArgs:
        parser = argparse.ArgumentParser(description="Pretty diff tool.")
        subparsers = parser.add_subparsers(dest="command", required=True)
        Args.add_parser(subparsers)
        StdinArgs.add_parser(subparsers)
        GitArgs.add_parser(subparsers)

        namespace = parser.parse_args(argv)
        command = cast(Command, namespace.command)
        match command:
            case "diff":
                return Args.from_namespace(namespace)
            case "stdin":
                return StdinArgs.from_namespace(namespace)
            case "git":
                return GitArgs.from_namespace(namespace)
            case _:
                assert_never(command)


class Test:
    r"""
    >>> import tempfile
    >>> tmp_dir = tempfile.TemporaryDirectory(prefix="pdiff-test-")
    >>> test = Test(Path(tmp_dir.name))

    >>> _ = test.assert_diff(
    ...     "simple",
    ...     '''
    ...     apple
    ...     banana
    ...     cherry
    ...     ''',
    ...     '''
    ...     apple
    ...     BANANA
    ...     cherry
    ...     ''',
    ...     b'''------ simple/old.txt
    ... ++++++ simple/new.txt
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,3 +1,3 @@ ============================================================\x1b[0m
    ... \x1b[100m |\x1b[0m apple
    ... \x1b[41m-|\x1b[0m \x1b[31mbanana\x1b[0m
    ... \x1b[42m+|\x1b[0m \x1b[32mBANANA\x1b[0m
    ... \x1b[100m |\x1b[0m cherry
    ... ''',
    ... )

    >>> _ = test.assert_diff(
    ...     "whitespace",
    ...     '''
    ...     x = 1
    ...     ''',
    ...     '''
    ...     x  = 1
    ...     ''',
    ...     b'',
    ...     expected_code=0,
    ... )

    >>> _ = test.assert_diff(
    ...     "whitespace",
    ...     '''
    ...     x = 1
    ...     ''',
    ...     '''
    ...     x  = 1
    ...     ''',
    ...     b'''------ whitespace/old.txt
    ... ++++++ whitespace/new.txt
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,1 +1,1 @@ ============================================================\x1b[0m
    ... \x1b[43m!|\x1b[0m x  = 1
    ... ''',
    ...     args=["--whitespace"],
    ... )

    >>> _ = test.assert_diff(
    ...     "move",
    ...     '''
    ...     alpha
    ...     moved-one
    ...     moved-two-old
    ...     moved-three
    ...     beta-1
    ...     beta-2
    ...     beta-3
    ...     beta-4
    ...     beta-5
    ...     gamma
    ...     ''',
    ...     '''
    ...     alpha
    ...     beta-1
    ...     beta-2
    ...     beta-3
    ...     beta-4
    ...     beta-5
    ...     moved-one
    ...     moved-two-new
    ...     moved-three
    ...     gamma
    ...     ''',
    ...     b'''------ move/old.txt
    ... ++++++ move/new.txt
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,10 +1,10 @@ ============================================================\x1b[0m
    ... \x1b[100m |\x1b[0m alpha
    ... \x1b[45m<|\x1b[0m \x1b[90mmoved-one\x1b[0m
    ... \x1b[45m<|\x1b[0m \x1b[90mmoved-two-\x1b[0m\x1b[31;1mold\x1b[0m
    ... \x1b[45m<|\x1b[0m \x1b[90mmoved-three\x1b[0m
    ... \x1b[100m |\x1b[0m beta-1
    ... \x1b[100m |\x1b[0m beta-2
    ... \x1b[100m |\x1b[0m beta-3
    ... \x1b[100m |\x1b[0m beta-4
    ... \x1b[100m |\x1b[0m beta-5
    ... \x1b[46m>|\x1b[0m \x1b[33mmoved-one\x1b[0m
    ... \x1b[46m>|\x1b[0m \x1b[33mmoved-two-\x1b[0m\x1b[32;1mnew\x1b[0m
    ... \x1b[46m>|\x1b[0m \x1b[33mmoved-three\x1b[0m
    ... \x1b[100m |\x1b[0m gamma
    ... ''',
    ... )

    >>> _ = test.assert_diff(
    ...     "indent",
    ...     '''
    ...     class X:
    ...         x: int
    ...
    ...
    ...     def a(x: int):
    ...         y = x + 7
    ...         print(y)
    ...     ''',
    ...     '''
    ...     class X:
    ...         x: int
    ...
    ...         def a(self):
    ...             y = self.x + 7
    ...             print(y)
    ...     ''',
    ...     b'''------ indent/old.txt
    ... ++++++ indent/new.txt
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,7 +1,6 @@ ============================================================\x1b[0m
    ... \x1b[100m |\x1b[0m class X:
    ... \x1b[100m |\x1b[0m     x: int
    ... \x1b[100m |\x1b[0m
    ... \x1b[41m-|\x1b[0m
    ... \x1b[41m-|\x1b[0m \x1b[90mdef a(\x1b[0m\x1b[31mx: int\x1b[0m\x1b[90m):\x1b[0m
    ... \x1b[41m-|\x1b[0m \x1b[31m    \x1b[0m\x1b[90my = x + 7\x1b[0m
    ... \x1b[42m+|\x1b[0m \x1b[32m    \x1b[0mdef a(\x1b[32mself\x1b[0m):
    ... \x1b[42m+|\x1b[0m \x1b[32m        \x1b[0my = \x1b[32mself.\x1b[0mx + 7
    ... \x1b[100m |\x1b[0m         print(y)
    ... ''',
    ... )

    >>> _ = test.assert_diff(
    ...     "indent_move",
    ...     '''
    ...     class X:
    ...         x: int
    ...
    ...     def b(asdf):
    ...         print(asdf)
    ...
    ...     def c():
    ...         b(7)
    ...         b("asdf")
    ...         b("zxcv")
    ...
    ...     def a(self: X) -> None:
    ...         y = self.x + 7
    ...         z = self.x + 5 * y
    ...         print(y, z)
    ...     ''',
    ...     '''
    ...     class X:
    ...         x: int
    ...
    ...         def a(self) -> None:
    ...             y = self.x + 7
    ...             z = self.x + 5 * y
    ...             print(y, z)
    ...
    ...     def b(asdf):
    ...         print(asdf)
    ...
    ...     def c():
    ...         b(7)
    ...         b("asdf")
    ...         b("zxcv")
    ...     ''',
    ...     b'''------ indent_move/old.txt
    ... ++++++ indent_move/new.txt
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,15 +1,15 @@ ============================================================\x1b[0m
    ... \x1b[100m |\x1b[0m class X:
    ... \x1b[100m |\x1b[0m     x: int
    ... \x1b[100m |\x1b[0m
    ... \x1b[46m>|\x1b[0m \x1b[32;1m    \x1b[0m\x1b[33mdef a(self) -> None:\x1b[0m
    ... \x1b[46m>|\x1b[0m \x1b[32;1m        \x1b[0m\x1b[33my = self.x + 7\x1b[0m
    ... \x1b[46m>|\x1b[0m \x1b[32;1m        \x1b[0m\x1b[33mz = self.x + 5 * y\x1b[0m
    ... \x1b[46m>|\x1b[0m \x1b[32;1m        \x1b[0m\x1b[33mprint(y, z)\x1b[0m
    ... \x1b[46m>|\x1b[0m
    ... \x1b[100m |\x1b[0m def b(asdf):
    ... \x1b[100m |\x1b[0m     print(asdf)
    ... \x1b[100m |\x1b[0m
    ... \x1b[100m |\x1b[0m def c():
    ... \x1b[100m |\x1b[0m     b(7)
    ... \x1b[100m |\x1b[0m     b("asdf")
    ... \x1b[100m |\x1b[0m     b("zxcv")
    ... \x1b[45m<|\x1b[0m
    ... \x1b[45m<|\x1b[0m \x1b[90mdef a(self\x1b[0m\x1b[31;1m: X\x1b[0m\x1b[90m) -> None:\x1b[0m
    ... \x1b[45m<|\x1b[0m \x1b[31;1m    \x1b[0m\x1b[90my = self.x + 7\x1b[0m
    ... \x1b[45m<|\x1b[0m \x1b[31;1m    \x1b[0m\x1b[90mz = self.x + 5 * y\x1b[0m
    ... \x1b[45m<|\x1b[0m \x1b[31;1m    \x1b[0m\x1b[90mprint(y, z)\x1b[0m
    ... ''',
    ... )

    >>> for path, content in {
    ...     "directory/old/removed.txt": '''
    ...     removed
    ...     ''',
    ...     "directory/old/changed.txt": '''
    ...     old value
    ...     ''',
    ...     "directory/old/same.txt": '''
    ...     same
    ...     ''',
    ...     "directory/old/subdir/nested.txt": '''
    ...     old nested
    ...     ''',
    ...     "directory/old/typeflip/file.txt": '''
    ...     inside old dir
    ...     ''',
    ...     "directory/new/added.txt": '''
    ...     added
    ...     ''',
    ...     "directory/new/changed.txt": '''
    ...     new value
    ...     ''',
    ...     "directory/new/same.txt": '''
    ...     same
    ...     ''',
    ...     "directory/new/subdir/nested.txt": '''
    ...     new nested
    ...     ''',
    ...     "directory/new/typeflip": '''
    ...     new plain file
    ...     ''',
    ... }.items():
    ...     test.write_file(test.tmp / path, content)
    >>> _ = test.assert_run(
    ...     ["diff", "--color", "always", str(test.tmp / "directory" / "old"), str(test.tmp / "directory" / "new")],
    ...     expected_code=1,
    ...     expected_stdout=b'''Only in directory/old: removed.txt
    ... ------ directory/old/removed.txt
    ... ++++++ /dev/null
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,1 +1,0 @@ ============================================================\x1b[0m
    ... \x1b[41m-|\x1b[0m \x1b[31mremoved\x1b[0m
    ... Only in directory/new: added.txt
    ... ------ /dev/null
    ... ++++++ directory/new/added.txt
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,0 +1,1 @@ ============================================================\x1b[0m
    ... \x1b[42m+|\x1b[0m \x1b[32madded\x1b[0m
    ... ------ directory/old/changed.txt
    ... ++++++ directory/new/changed.txt
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,1 +1,1 @@ ============================================================\x1b[0m
    ... \x1b[41m-|\x1b[0m \x1b[31mold\x1b[0m\x1b[90m value\x1b[0m
    ... \x1b[42m+|\x1b[0m \x1b[32mnew\x1b[0m value
    ... ------ directory/old/subdir/nested.txt
    ... ++++++ directory/new/subdir/nested.txt
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,1 +1,1 @@ ============================================================\x1b[0m
    ... \x1b[41m-|\x1b[0m \x1b[31mold\x1b[0m\x1b[90m nested\x1b[0m
    ... \x1b[42m+|\x1b[0m \x1b[32mnew\x1b[0m nested
    ... Files directory/old/typeflip and directory/new/typeflip are not the same type
    ... ''',
    ... )

    >>> _ = test.assert_run(
    ...     ["stdin", "--color", "always"],
    ...     expected_code=0,
    ...     input_data=Test.input_text('''
    ...     --- a/sample.txt
    ...     +++ b/sample.txt
    ...     @@ -1,1 +1,1 @@
    ...     -banana split
    ...     +banana split now
    ...     '''),
    ...     expected_stdout=b'''--- a/sample.txt
    ... +++ b/sample.txt
    ... \x1b[1m@@ -1,1 +1,1 @@\x1b[0m
    ... \x1b[31m-\x1b[0m\x1b[90mbanana split\x1b[0m
    ... \x1b[32m+\x1b[0mbanana split\x1b[32m now\x1b[0m
    ... ''',
    ... )

    >>> git_named_path = test.tmp / "git"
    >>> git_named_next_path = test.tmp / "git.next"
    >>> test.write_file(git_named_path, '''
    ... old
    ... ''')
    >>> test.write_file(git_named_next_path, '''
    ... new
    ... ''')
    >>> _ = test.assert_run(
    ...     ["diff", "--color", "always", "--", "git", str(git_named_next_path)],
    ...     expected_code=1,
    ...     expected_stdout=b'''------ git
    ... ++++++ git.next
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,1 +1,1 @@ ============================================================\x1b[0m
    ... \x1b[41m-|\x1b[0m \x1b[31mold\x1b[0m
    ... \x1b[42m+|\x1b[0m \x1b[32mnew\x1b[0m
    ... ''',
    ... )
    >>> git_named_path.unlink()

    >>> old_path = test.tmp / "git" / "old.txt"
    >>> new_path = test.tmp / "git" / "new.txt"
    >>> test.write_file(old_path, '''
    ... apple
    ... banana
    ... cherry
    ... ''')
    >>> test.write_file(new_path, '''
    ... apple
    ... BANANA
    ... cherry
    ... ''')
    >>> _ = test.assert_run(
    ...     ["git", "--color", "always", "file.txt", str(old_path), "aaa111", "100644", str(new_path), "bbb222", "100644"],
    ...     expected_code=0,
    ...     expected_stdout=b'''\x1b[1mpdiff -git a/file.txt b/file.txt\x1b[0m
    ... index aaa111..bbb222
    ... ------ a/file.txt
    ... ++++++ b/file.txt
    ... \x1b[100m@|\x1b[0m \x1b[1m@@ -1,3 +1,3 @@ ============================================================\x1b[0m
    ... \x1b[100m |\x1b[0m apple
    ... \x1b[41m-|\x1b[0m \x1b[31mbanana\x1b[0m
    ... \x1b[42m+|\x1b[0m \x1b[32mBANANA\x1b[0m
    ... \x1b[100m |\x1b[0m cherry
    ... ''',
    ... )

    >>> rename_old_path = test.tmp / "git" / "rename-old.txt"
    >>> rename_new_path = test.tmp / "git" / "rename-new.txt"
    >>> test.write_file(rename_old_path, '''
    ... unchanged
    ... ''')
    >>> test.write_file(rename_new_path, '''
    ... unchanged
    ... ''')
    >>> _ = test.assert_run(
    ...     [
    ...         "git",
    ...         "--color",
    ...         "always",
    ...         ".local/opt/findfile.nvim",
    ...         str(rename_old_path),
    ...         "aaa111",
    ...         "100644",
    ...         str(rename_new_path),
    ...         "bbb222",
    ...         "100644",
    ...         ".local/opt/nvim_plugins/findfile.nvim",
    ...         "similarity index 100%\nrename from .local/opt/findfile.nvim\nrename to .local/opt/nvim_plugins/findfile.nvim",
    ...     ],
    ...     expected_code=0,
    ...     expected_stdout=b'''\x1b[1mpdiff -git a/.local/opt/findfile.nvim b/.local/opt/nvim_plugins/findfile.nvim\x1b[0m
    ... similarity index 100%
    ... rename from .local/opt/findfile.nvim
    ... rename to .local/opt/nvim_plugins/findfile.nvim
    ... ''',
    ... )

    >>> tmp_dir.cleanup()
    """

    PDIFF: ClassVar[Path] = Path(__file__).resolve()

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp

    @staticmethod
    def input_text(value: str) -> bytes:
        from textwrap import dedent  # noqa: PLC0415

        return dedent(value.removeprefix(chr(10))).encode()

    @classmethod
    def write_file(cls, path: Path, data: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cls.input_text(data))

    def assert_run(
        self,
        args: list[str],
        *,
        expected_code: int,
        expected_stdout: bytes | None = None,
        input_data: bytes | None = None,
    ) -> bytes:
        import subprocess  # noqa: PLC0415

        result = subprocess.run(
            [sys.executable, str(self.PDIFF), *args],
            cwd=self.tmp,
            input=input_data,
            capture_output=True,
            check=False,
        )
        stdout = result.stdout.replace(f"{self.tmp}/".encode(), b"")
        stdout_for_assert = stdout.replace(UnifiedRenderer.CLEAR_EOL.encode(), b"")
        assert result.returncode == expected_code
        assert expected_stdout is None or stdout_for_assert == expected_stdout
        assert not result.stderr
        return stdout

    def assert_diff(
        self,
        name: str,
        old: str,
        new: str,
        expected: bytes,
        *,
        args: list[str] | None = None,
        expected_code: int = 1,
    ) -> bytes:
        old_path = self.tmp / name / "old.txt"
        new_path = self.tmp / name / "new.txt"
        self.write_file(old_path, old)
        self.write_file(new_path, new)
        return self.assert_run(
            [
                "diff",
                "--color",
                "always",
                *(args or []),
                str(old_path),
                str(new_path),
            ],
            expected_code=expected_code,
            expected_stdout=expected,
        )

    @staticmethod
    def has_active_color_at_eol(text: str) -> bool:
        import re  # noqa: PLC0415

        active = False
        i = 0
        while i < len(text):
            if text.startswith("\x1b[", i):
                match = re.match(r"\x1b\[([0-9;]*)m", text[i:])
                if match is not None:
                    params = match.group(1).split(";") if match.group(1) else ["0"]
                    active = "0" not in params
                    i += len(match.group(0))
                    continue
            if text[i] in "\r\n" and active:
                return True
            i += 1
        return False


if __name__ == "__main__":
    raise SystemExit(CLI.from_argv().main())
