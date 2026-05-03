# Coding Instructions

- Prefer a single-file implementation when the program is small enough to stay readable.
- Keep external dependencies minimal. If one dependency is allowed, isolate it at the CLI boundary.
- Use `@dataclass(frozen=True, kw_only=True)` as the main data abstraction.
- Put behavior on the data that owns it. Use methods or class methods for stateful logic, and namespace-style classes with static methods for cohesive stateless helpers.
- Keep constants as `ClassVar`s on the class that owns the behavior.
- Separate data processing from printing/rendering.
- Use precise `Literal[...]` aliases for string-mode values and kind dispatch. Avoid `Enum` when simple literals are enough.
- Prefer exhaustive `match` statements over kind `if`/`elif` chains. Include `case _:` with `assert_never(...)`.
- For tuple matches, a walrus-bound subject is acceptable when it helps type checkers understand `assert_never`.
- Do not over-abstract. Remove dataclasses or wrappers that only add ceremony.
- Use `list[...]` for variable-size collections and `tuple[...]` for fixed-size return pairs.
- Keep CLI argument classes thin: parse inputs, call core logic, print output, return exit codes.
- Some CLI frameworks may not support rich annotations such as `Literal[...]`; keep those fields runtime-compatible and cast at the boundary.
- Add doctests near small units of logic, and fixture tests for whole-program behavior.
- Test colored output explicitly when rendering uses ANSI.
- Keep fixtures small and organized by case.
- Use a simple Makefile: `all` should run formatting/fixes, lint/type checks, and tests. Run `make` regularly.
- Maintain a short handoff/status document for ongoing refactors and unfinished decisions.
