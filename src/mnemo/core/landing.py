"""The last metre of a contract: land its pieces in ``consumes`` order.

A contract's whole point is that piece A ``consumes`` a signature piece B
``exposes``. A was written against a signature that did not exist yet, and
the merge is where it becomes real — which is why dispatch is a flat fan-out
rather than a scheduler (CHANGELOG 1.5.0). Until now that resolution was done
by hand: merge the PRs in dependency order, run the suite after each, and
find out at the end whether A's assumption about B held (#236).

This module gives that check a home. It reads the contract through
:mod:`mnemo.core.contracts` — never the markdown — and answers three
questions from git:

- **In what order?** A stable topological sort by ``consumes``: an owner
  lands before every piece that consumes from it, and ties keep the order
  the contract was written in.
- **Is the exposed signature there?** By *name*, in the piece's own ``files``
  boundary, at the ref that carries the piece. :func:`contracts._validate`
  refuses to compare a consumed signature with an exposed one by string
  equality, because the two are hand-written and differ cosmetically; a name
  is the granularity that survives a renamed argument and still catches a
  function that was never written.
- **Does the whole thing merge and stay green?** A rehearsal in a temporary
  worktree, one piece at a time, with the suite run at each step. It stops
  at the first conflict, missing name or red suite, and removes the worktree
  on every path. Nothing is pushed or merged on GitHub until the rehearsal
  passed in full.

Nothing is persisted. As with :mod:`mnemo.core.sessions.delivery`, every
fact is derived from git and ``gh`` on each read, so it can never disagree
with them.
"""
from __future__ import annotations

import ast
import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from mnemo.core import contracts


class LandingError(RuntimeError):
    """A contract cannot be landed as it stands. Raised before any merge."""


# --- order -----------------------------------------------------------------


def order(contract: contracts.Contract) -> list[contracts.Piece]:
    """The pieces in landing order: every owner before its consumers.

    Kahn's algorithm, seeded and advanced in contract order so that pieces
    with no dependency between them keep the order the maintainer wrote —
    the order is then a pure function of the file, and two runs print the
    same plan.

    A cycle has no landing order. ``contracts._validate`` admits one (it
    checks that an owner exists and is not the consumer itself, nothing
    more), so it is refused here, naming the pieces left over.
    """
    by_slug = {p.slug: p for p in contract.pieces}
    # in-degree: how many distinct owners this piece still waits on
    waiting = {
        p.slug: {owner for _sig, owner in p.consumes if owner in by_slug}
        for p in contract.pieces
    }
    landed: list[contracts.Piece] = []
    done: set[str] = set()
    while len(landed) < len(contract.pieces):
        ready = [p for p in contract.pieces
                 if p.slug not in done and not (waiting[p.slug] - done)]
        if not ready:
            stuck = [p.slug for p in contract.pieces if p.slug not in done]
            raise LandingError(
                "no landing order: pieces consume from each other in a cycle "
                f"({', '.join(stuck)})"
            )
        piece = ready[0]
        landed.append(piece)
        done.add(piece.slug)
    return landed


# --- signatures ------------------------------------------------------------

# The leading identifier of a signature, after an optional `class`/`def`
# keyword and any dotted owner (`Readiness.ready` is looked up as `ready`,
# inside `Readiness` when that is a class — see `_owner_members`).
# What follows must be a call, an annotation, a subscript, a return arrow
# or nothing — a second bare word (`mnemo dispatch --contract`) is a CLI
# shape, not a Python name, and is reported as unverifiable rather than
# looked up as `mnemo` and found missing. TypeScript's optional marker may
# sit between the name and its annotation or call (`ChildSession.notice?:
# string`, #675); a `?` followed by anything else is still no name.
_NAME_RE = re.compile(
    r"^(?:(?:async\s+)?def\s+|class\s+)?"
    r"((?:[A-Za-z_][A-Za-z0-9_]*\.)*)([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\?(?=\s*[:(]))?\s*(?:$|[(:\[]|->)"
)


def _signature_parts(signature: str) -> tuple[str | None, str] | None:
    """``(owner, name)`` of a signature: ``Outer.Inner.depth`` -> ``("Inner", "depth")``.

    ``owner`` is the segment immediately before the name, or ``None`` when
    the signature was not dotted; the whole answer is ``None`` when the
    signature names no Python identifier (see :func:`signature_name`).
    """
    if "`" not in signature:
        # A contract signature is backticked by rule (`contracts._validate`
        # refuses one that is not); a bare word here is `nothing`, or prose.
        return None
    text = signature.strip().strip("`").strip()
    if not text:
        return None
    match = _NAME_RE.match(text)
    if not match:
        return None
    qualifier = match.group(1).rstrip(".")
    return (qualifier.rsplit(".", 1)[-1] or None), match.group(2)


def signature_name(signature: str) -> str | None:
    """The name a signature is looked up by, or ``None`` when it has none.

    ``None`` is the honest answer for a signature that names no Python
    identifier — a CLI invocation, a flag. Treating it as missing would fail
    a landing over a contract that was right; treating it as present would
    make the check vacuous. The caller prints it as unverifiable.
    """
    parts = _signature_parts(signature)
    return parts[1] if parts else None


def _definition_re(name: str) -> re.Pattern:
    # A def, a class, or a module-level assignment/annotation. Indented
    # defs count: `Readiness.ready` is a method, and a method is what the
    # consumer calls. Assignments are top-level only, because an indented
    # `name = ...` is a local, not a boundary anyone can import. A class
    # attribute is indented too, and is a boundary — that case is answered
    # by `_owner_members`, which can tell the two apart; this regex cannot.
    # Nor can it see a Rust field or a TS member, `name: Type` with no
    # keyword before it (#675): those are found through their owner too.
    #
    # Contracts land TypeScript and Rust too (mnemo-desktop), so their
    # declaration keywords count as well (#446): `export function`,
    # `export const`, `interface`, `type` in TS; `pub fn`, `struct`, `trait`
    # in Rust. Every form starts with a keyword before the name, which is
    # what keeps an import (`import { name }`) and a call (`name(x)`) out.
    n = re.escape(name)
    ts = (r"(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
          r"(?:function\s*\*?|const|let|var|class|interface|type|enum)")
    rust = (r"(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
            r'(?:extern\s+"[^"]*"\s+)?(?:fn|struct|enum|trait|type|static|mod|const)')
    return re.compile(
        rf"^(?:\s*(?:async\s+)?def\s+{n}\s*\("
        rf"|\s*class\s+{n}\b"
        rf"|{n}\s*(?::|=(?!=))"
        rf"|\s*{ts}\s+{n}\b"
        rf"|\s*{rust}\s+{n}\b)",
        re.MULTILINE,
    )


def _class_members(source: str, owner: str) -> set[str] | None:
    """Every name a class called *owner* defines in *source*, or ``None``.

    ``None`` when *source* has no such class — the qualifier may be a module
    (`briefing_select.pick`) — or does not parse, so the caller falls back to
    the bare-name lookup. A member is what the class body binds directly: a
    def, a nested class, an assignment or annotation (a dataclass field, a
    constant, an enum member), and ``self.<name> = ...`` in one of its
    methods. A method's own locals are not members, which is the distinction
    an indentation-anchored regex cannot draw (#305).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    classes = [node for node in ast.walk(tree)
               if isinstance(node, ast.ClassDef) and node.name == owner]
    if not classes:
        return None

    def bound(target: ast.AST, self_name: str | None) -> list[str]:
        if isinstance(target, ast.Name) and self_name is None:
            return [target.id]
        if (isinstance(target, ast.Attribute) and self_name is not None
                and isinstance(target.value, ast.Name)
                and target.value.id == self_name):
            return [target.attr]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [n for elt in target.elts for n in bound(elt, self_name)]
        return []

    members: set[str] = set()
    for cls in classes:
        for stmt in cls.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                members.add(stmt.name)
            elif isinstance(stmt, ast.Assign):
                members.update(n for t in stmt.targets for n in bound(t, None))
            elif isinstance(stmt, ast.AnnAssign):
                members.update(bound(stmt.target, None))
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = stmt.args.posonlyargs + stmt.args.args
            if not params:
                continue
            for node in ast.walk(stmt):
                if isinstance(node, ast.Assign):
                    members.update(n for t in node.targets
                                   for n in bound(t, params[0].arg))
                elif isinstance(node, ast.AnnAssign):
                    members.update(bound(node.target, params[0].arg))
    return members


# --- members of a Rust or TypeScript owner (#675) ---------------------------
#
# The same question `_class_members` answers for Python, for the two braced
# languages contracts also land (mnemo-desktop): what does the type called
# *owner* declare? There is no parser for them in the standard library, so
# the source is read in three passes, each simple enough to be right:
#
# 1. `_code_only` blanks every comment and the inside of every literal, so a
#    brace in a string or a doc comment cannot unbalance anything after it.
# 2. The owner's body is found from its declaration keyword and cut out by
#    bracket matching (`_body_after`, `_close`).
# 3. `_flat` drops everything nested inside that body — method bodies, call
#    arguments, generic arguments — so what is left is the body's own
#    declarations, each read by its leading identifier.
#
# Wherever a step cannot follow the source, the answer is `None` and the
# caller falls back to the bare-name lookup, as it does for a Python file
# that does not parse: an unreadable owner never fails a landing on its own.

_RUST_SUFFIXES = (".rs",)
_TS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")

_RUST_RAW = re.compile(r'b?r(#*)"')
_RUST_CHAR = re.compile(
    r"b?'(?:[^\\'\n]|\\(?:x[0-9A-Fa-f]{2}|u\{[0-9A-Fa-f]{1,6}\}|[^\n]))'")
# After one of these a TS `/` opens a regular expression rather than divides.
# Not `<`: in TSX that is `</div>`, a closing tag.
_TS_REGEX_AFTER = frozenset("(,=:[!&|?{};+-*%>~^")
_TS_REGEX_WORDS = frozenset(
    "return typeof case do else in of new delete void throw yield await".split())
_PAIRS = {"{": "}", "(": ")", "[": "]", "<": ">"}


def _blank(out: list[str], start: int, end: int) -> None:
    for k in range(start, end):
        if out[k] != "\n":
            out[k] = " "


def _ident_char(ch: str) -> bool:
    return ch.isalnum() or ch in "_$"


def _scan_code(src: str, i: int, out: list[str], *, rust: bool,
               nested: bool) -> int | None:
    """Blank comments and literal contents from *i* on; the index scanning ended at.

    With *nested*, *i* is inside a TS template's `${…}`, and the scan ends
    after the `}` that closes it. ``None`` when a block comment, a Rust
    string or a TS template never closes.
    """
    n = len(src)
    depth = 0
    while i < n:
        c, two = src[i], src[i:i + 2]
        if two == "//":
            end = src.find("\n", i)
            end = n if end < 0 else end
            _blank(out, i, end)
            i = end
            continue
        if two == "/*":
            j, level = i + 2, 1
            while level:
                if j >= n:
                    return None
                if src.startswith("*/", j):
                    level, j = level - 1, j + 2
                elif rust and src.startswith("/*", j):  # Rust nests them
                    level, j = level + 1, j + 2
                else:
                    j += 1
            _blank(out, i, j)
            i = j
            continue
        after_ident = i > 0 and _ident_char(src[i - 1])
        if rust and c in "br" and not after_ident:
            raw = _RUST_RAW.match(src, i)
            if raw:
                close = '"' + raw.group(1)
                end = src.find(close, raw.end())
                if end < 0:
                    return None
                _blank(out, raw.end(), end)
                i = end + len(close)
                continue
        if rust and (c == "'" or (c == "b" and src[i + 1:i + 2] == "'" and not after_ident)):
            char = _RUST_CHAR.match(src, i)
            if char:  # otherwise a lifetime, `'a`, which is code
                _blank(out, src.index("'", i) + 1, char.end() - 1)
                i = char.end()
                continue
            i += 1
            continue
        if c == '"' or (c == "'" and not rust):
            j = i + 1
            while j < n and src[j] != c and (rust or src[j] != "\n"):
                j += 2 if src[j] == "\\" else 1
            if j >= n or src[j] != c:
                if rust:
                    return None
                i += 1  # a TS string never spans a line: a stray quote (JSX text)
                continue
            _blank(out, i + 1, j)
            i = j + 1
            continue
        if not rust and c == "`":
            j = i + 1
            while True:
                if j >= n:
                    return None
                if src[j] == "\\":
                    j += 2
                elif src[j] == "`":
                    break
                elif src.startswith("${", j):
                    j = _scan_code(src, j + 2, out, rust=False, nested=True)
                    if j is None:
                        return None
                else:
                    j += 1
            _blank(out, i + 1, j)
            i = j + 1
            continue
        if not rust and c == "/" and _regex_may_start(out, i):
            j, in_class = i + 1, False
            while j < n and src[j] != "\n" and (in_class or src[j] != "/"):
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                j += 1
            if j < n and src[j] == "/":
                _blank(out, i + 1, j)
                i = j + 1
                continue
        if nested:
            if c == "{":
                depth += 1
            elif c == "}":
                if not depth:
                    return i + 1
                depth -= 1
        i += 1
    return None if nested else n


def _regex_may_start(out: list[str], i: int) -> bool:
    k = i - 1
    while k >= 0 and out[k].isspace():
        k -= 1
    if k < 0 or out[k] in _TS_REGEX_AFTER:
        return True
    start = k
    while start > 0 and _ident_char(out[start - 1]):
        start -= 1
    return "".join(out[start:k + 1]) in _TS_REGEX_WORDS


def _code_only(source: str, *, rust: bool) -> str | None:
    """*source* with comments blanked and literals emptied, or ``None``.

    Line breaks and every other offset are kept; a literal keeps its quotes,
    so `"a" / 2` still reads as a division. ``None`` when a comment or a
    literal never closes — the file is then not read for members at all.
    """
    out = list(source)
    if _scan_code(source, 0, out, rust=rust, nested=False) is None:
        return None
    return "".join(out)


def _skip_angles(code: str, i: int) -> int | None:
    """Past the `>` that closes the `<` at *i*; `->` and `=>` close nothing.

    Brackets inside are stepped over whole: `<T extends { id: string }>`.
    """
    angles = brackets = 0
    for j in range(i, len(code)):
        ch = code[j]
        if ch in "{([":
            brackets += 1
        elif ch in "})]":
            if not brackets:
                return None
            brackets -= 1
        elif brackets:
            continue
        elif ch == "<":
            angles += 1
        elif ch == ">" and code[j - 1] not in "-=":
            angles -= 1
            if not angles:
                return j + 1
        elif ch == ";":
            return None
    return None


def _body_after(code: str, i: int) -> int | None:
    """The `{` opening the body of the declaration whose header starts at *i*.

    The header may carry generics, bounds, `extends`, a `where` clause:
    the body is the first `{` outside all of them. ``None`` when a `;` or
    an enclosing `}` ends the declaration first — a unit struct, say.
    """
    angles = parens = 0
    for j in range(i, len(code)):
        ch = code[j]
        if ch in "([":
            parens += 1
        elif ch in ")]":
            parens -= 1
        elif parens:
            continue
        elif ch == "<":
            angles += 1
        elif ch == ">" and code[j - 1] not in "-=":
            angles -= 1
        elif angles:
            continue
        elif ch == "{":
            return j
        elif ch in ";}":
            return None
    return None


def _close(code: str, i: int) -> int | None:
    """The index of the bracket closing the one at *i*, or ``None``."""
    want, depth = _PAIRS[code[i]], 0
    for j in range(i, len(code)):
        if code[j] == code[i]:
            depth += 1
        elif code[j] == want:
            depth -= 1
            if not depth:
                return j
    return None


def _flat(body: str, *, angles: bool) -> str | None:
    """*body* with the inside of every bracket pair in it removed.

    `fn load(&self, k: &str) -> R { … }` becomes `fn load() -> R {}`, so
    what is left is the body's own declarations, one level deep. With
    *angles*, generic arguments go too, which a type's members need (the
    comma in `HashMap<K, V>` separates no fields) and code bodies cannot
    afford (`1 << 4` is no bracket). ``None`` on a bracket that does not
    match.
    """
    out: list[str] = []
    stack: list[str] = []
    for j, ch in enumerate(body):
        opens = ch in "{([" or (angles and ch == "<")
        closes = ch in "})]" or (angles and ch == ">" and body[j - 1:j] not in ("-", "="))
        if opens:
            if not stack:
                out.append(ch)
            stack.append(ch)
        elif closes:
            if not stack or _PAIRS[stack.pop()] != ch:
                return None
            if not stack:
                out.append(ch)
        elif not stack:
            out.append(ch)
    return None if stack else "".join(out)


_RUST_FIELD = re.compile(
    r"^\s*(?:#\[\]\s*)*(?:pub(?:\(\))?\s+)?(?:r#)?([A-Za-z_]\w*)\s*:(?!:)")
_RUST_VARIANT = re.compile(r"^\s*(?:#\[\]\s*)*([A-Za-z_]\w*)\s*(?:[({=]|$)")
_RUST_ITEM = re.compile(
    r"\bfn\s+(?:r#)?([A-Za-z_]\w*)"
    r"|(?:^|[;}\]\n])\s*(?:pub(?:\(\))?\s+)?(?:const|static)\s+([A-Za-z_]\w*)\s*:"
    r"|\btype\s+([A-Za-z_]\w*)")
_RUST_IMPL_SELF = re.compile(
    r"\s*(?:&\s*(?:'\w+\s+)?(?:mut\s+)?)?(?:dyn\s+)?"
    r"(?:(?:::)?\s*[A-Za-z_]\w*\s*::\s*)*([A-Za-z_]\w*)")


def _rust_impl_self(header: str) -> str | None:
    """The type an `impl` header implements for: `impl<T> Tr for a::Owner<T>` -> `Owner`."""
    head = header.strip()
    if head.startswith("<"):
        past = _skip_angles(head, 0)
        if past is None:
            return None
        head = head[past:]
    head = re.split(r"\bwhere\b", head)[0]
    head = re.split(r"\bfor\b(?!\s*<)", head)[-1]
    match = _RUST_IMPL_SELF.match(head)
    return match.group(1) if match else None


def _rust_members(source: str, owner: str) -> set[str] | None:
    """What a Rust type called *owner* declares: fields, variants, and the
    `fn`/`const`/`type` items of its `impl` and `trait` blocks."""
    code = _code_only(source, rust=True)
    if code is None:
        return None
    o = re.escape(owner)
    members: set[str] = set()
    found = False
    for match in re.finditer(rf"\b(struct|union|enum|trait)\s+{o}\b", code):
        found = True
        rest = match.end()
        if code[rest:].lstrip().startswith("<"):
            rest = _skip_angles(code, code.index("<", rest)) or rest
        if match.group(1) != "enum" and code[rest:].lstrip().startswith("("):
            continue  # a tuple struct: its fields are `.0`, which no contract names
        open_ = _body_after(code, rest)
        if open_ is None:
            continue  # a unit struct
        close = _close(code, open_)
        flat = None if close is None else _flat(
            code[open_ + 1:close], angles=match.group(1) in ("struct", "union"))
        if flat is None:
            return None
        if match.group(1) == "trait":
            members.update(n for m in _RUST_ITEM.finditer(flat) for n in m.groups() if n)
        else:
            shape = _RUST_VARIANT if match.group(1) == "enum" else _RUST_FIELD
            members.update(m.group(1) for m in map(shape.match, flat.split(",")) if m)
    # At an item's start only: in `fn f() -> impl Iterator {` it is a return type.
    for match in re.finditer(r"(?:^|[;{}\]])\s*(?:unsafe\s+)?impl\b", code):
        open_ = _body_after(code, match.end())
        if open_ is None or _rust_impl_self(code[match.end():open_]) != owner:
            continue
        found = True
        close = _close(code, open_)
        flat = None if close is None else _flat(code[open_ + 1:close], angles=False)
        if flat is None:
            return None
        members.update(n for m in _RUST_ITEM.finditer(flat) for n in m.groups() if n)
    return members if found else None


_TS_MEMBER = re.compile(  # interface / object type: `a?: T`, `m(x): T`, `m<T>(x)`
    r"^\s*(?:readonly\s+)?(?:[gs]et\s+(?=[A-Za-z_$]))?"
    r"(?!new\s*[(<])"  # `new (…): T` is a construct signature, not a member
    r"([A-Za-z_$][\w$]*)\s*\??\s*(?:[:(]|<>\s*\()")
# Decorators, `@ApiProperty()`: the lookahead keeps the name whole, or
# `@IsArray()` alone on its line would read as `@IsArra` and a member `y`.
_TS_DECORATORS = r"(?:@[\w$.]+(?![\w$.])\s*(?:\(\))?\s*)*"
_TS_CLASS_MEMBER = re.compile(
    r"^\s*" + _TS_DECORATORS +
    r"(?:(?:public|private|protected|readonly|static|abstract|override|declare|"
    r"async|accessor)\s+)*(?:[gs]et\s+(?=[#A-Za-z_$]))?\*?\s*"
    # A generic method is `m<…>(` on its line, or a line ending in `m<` that
    # opens a long parameter list; a line `Array<{…}>` continues a type.
    r"#?([A-Za-z_$][\w$]*)\s*[?!]?\s*(?:[:(=;]|<(?:.*?>\s*\(|\s*$)|$)")
_TS_PARAMETER_PROPERTY = re.compile(
    r"^\s*" + _TS_DECORATORS + r"(?:public|private|protected|readonly|override)\s+"
    r"(?:readonly\s+)?([A-Za-z_$][\w$]*)")
_TS_ENUM_MEMBER = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*(?:=|$)")
# A type alias written without a semicolon goes on past a line break only
# when the type so far is unfinished — it ends in an operator or a keyword
# that wants an operand — or the next line opens with an operator.
_TS_TYPE_GOES_ON = re.compile(
    r"(?:[=|&?:,]|=>|\b(?:extends|keyof|typeof|infer|is|readonly|unique|new|asserts))\s*$")
_TS_TYPE_CONTINUES = re.compile(r"\s*(?:[|&?:.]|=>|extends\b)")


def _ts_object_members(body: str) -> set[str] | None:
    flat = _flat(body, angles=True)
    if flat is None:
        return None
    return {m.group(1) for m in map(_TS_MEMBER.match, re.split(r"[;,\n]", flat)) if m}


def _ts_alias_members(code: str, i: int) -> set[str] | None:
    """Members of every object literal on the right of a `type X =` at *i*.

    `{ a } & Base` declares `a`; `{ kind: 'x'; a } | { kind: 'y'; b }`
    declares `a` and `b`. ``None`` for an alias with no literal in it
    (`type X = Base & Extra`): what it declares lives elsewhere.
    """
    end = len(code)
    depth = 0
    line_start = i
    for j in range(i, len(code)):
        ch = code[j]
        if ch in "{([<":
            depth += 1
        elif ch in "})]" or (ch == ">" and code[j - 1] != "="):
            if not depth:
                end = j
                break
            depth -= 1
        elif depth:
            continue
        elif ch == ";":
            end = j
            break
        elif ch == "\n":
            if code[line_start:j].strip() and not _TS_TYPE_GOES_ON.search(
                    code, line_start, j) and not _TS_TYPE_CONTINUES.match(code, j):
                end = j
                break
            if code[line_start:j].strip():
                line_start = j
    members: set[str] = set()
    literal = False
    j = i
    while j < end:
        if code[j] == "{":
            close = _close(code, j)
            got = None if close is None else _ts_object_members(code[j + 1:close])
            if got is None:
                return None
            members |= got
            literal = True
            j = close
        j += 1
    return members if literal else None


def _ts_members(source: str, owner: str) -> set[str] | None:
    """What a TS/JS `interface`, `type`, `class` or `enum` called *owner* declares."""
    if owner in ("extends", "implements"):
        return None  # `class extends Base {` is an anonymous class, not one named so
    code = _code_only(source, rust=False)
    if code is None:
        return None
    o = re.escape(owner)
    members: set[str] = set()
    found = False
    for match in re.finditer(rf"\b(interface|class|enum|type)\s+{o}(?![\w$])", code):
        kind, rest = match.group(1), match.end()
        if kind == "type":
            after = code[rest:].lstrip()
            if after.startswith("<"):
                past = _skip_angles(code, code.index("<", rest))
                after = code[past:].lstrip() if past else ""
            if not after.startswith("=") or after.startswith("=="):
                continue  # `import type Owner from …`
            got = _ts_alias_members(code, len(code) - len(after) + 1)
            if got is not None:
                found = True
                members |= got
            continue
        open_ = _body_after(code, rest)
        close = None if open_ is None else _close(code, open_)
        if close is None:
            return None
        found = True
        body = code[open_ + 1:close]
        if kind == "interface":
            got = _ts_object_members(body)
        elif kind == "enum":
            flat = _flat(body, angles=False)
            got = None if flat is None else {
                m.group(1) for m in map(_TS_ENUM_MEMBER.match, flat.split(",")) if m}
        else:
            got = _ts_class_members(body)
        if got is None:
            return None
        members |= got
    return members if found else None


def _ts_class_members(body: str) -> set[str] | None:
    """Fields, methods and accessors of a class body, and its parameter properties."""
    flat = _flat(body, angles=False)
    if flat is None:
        return None
    # A member ends at `;`, a line break, or a body's `}` with another member
    # after it on the line — not the `}` of `m<T extends {}>()`.
    pieces = re.split(r"[;\n]|(?<=\})(?=[ \t]*[@#*A-Za-z_$])", flat)
    members = {m.group(1) for m in map(_TS_CLASS_MEMBER.match, pieces) if m}
    members.discard("constructor")
    ctor = re.search(r"\bconstructor\s*\(", body)
    if ctor:
        close = _close(body, ctor.end() - 1)
        params = None if close is None else _flat(body[ctor.end():close], angles=False)
        if params is not None:
            members.update(m.group(1) for m in map(_TS_PARAMETER_PROPERTY.match,
                                                   params.split(",")) if m)
    return members


def _owner_members(path: str, source: str, owner: str) -> set[str] | None:
    """What the type called *owner* declares in the file at *path*, or ``None``.

    Read by the file's language: a Rust `struct`/`enum`/`trait` and its
    `impl` blocks, a TS `interface`/`type`/`class`/`enum`, a Python class
    (#305) for anything else. ``None`` when the file declares no such type
    or cannot be read, so the caller falls back to the bare-name lookup.
    """
    suffix = os.path.splitext(path)[1].lower()
    if suffix in _RUST_SUFFIXES:
        return _rust_members(source, owner)
    if suffix in _TS_SUFFIXES:
        return _ts_members(source, owner)
    return _class_members(source, owner)


def _git(args: Sequence[str], *, cwd: Path | str):
    import subprocess

    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, OSError) as exc:  # pragma: no cover - platform
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def _run_gh(args, **kwargs):
    """Indirection so the check reader can be faked in tests."""
    import subprocess

    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60, **kwargs)


def failing_checks(pr: str) -> list[str]:
    """The names of *pr*'s failing checks, judged check by check.

    One ``gh`` call; the per-check verdict is what it is read for. Reads
    ``gh pr checks --json name,bucket`` rather than the run's conclusion
    or the PR's rollup. A repository may mark a job non-blocking, and such a
    job fails while both aggregates report success — so a gate that trusted
    the aggregate would be reading a proxy of the thing it is gating on,
    immediately before the one irreversible step.

    Only ``bucket == "fail"`` counts. Pending is not failure (the landing is
    simply not ready yet, which the rehearsal will say), and skipped or
    cancelled checks are not results. An unreadable answer — ``gh`` missing, a
    PR with no checks, malformed output — returns ``[]``: this refuses a
    landing on evidence, never on the absence of it.
    """
    import json
    import subprocess

    try:
        result = _run_gh(["gh", "pr", "checks", pr, "--json", "name,bucket"])
    # `gh` absent or unrunnable is an OSError; `timeout=60` expiring is a
    # SubprocessError, which is not one. Both mean "no answer", not "green".
    # Deliberately not `except Exception`: a bug in here would then read as an
    # empty verdict and silently disarm the gate.
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode not in (0, 8):  # 8 == checks pending
        return []
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return []
    if not isinstance(rows, list):
        return []
    return [
        str(row.get("name") or "?")
        for row in rows
        if isinstance(row, dict) and row.get("bucket") == "fail"
    ]


def _boundary_files(piece: contracts.Piece, *, ref: str, repo_root: Path | str) -> list[str]:
    """The piece's ``files`` as they exist at *ref*, globs expanded.

    Expanded against ``git ls-tree`` rather than the working tree, so the
    answer is about the branch and not about whatever checkout the command
    happens to run in. A literal path that does not exist at *ref* is
    simply absent from the result — the piece may not have created it.
    """
    listed = _git(["ls-tree", "-r", "--name-only", ref], cwd=repo_root)
    if listed.returncode != 0:
        return []
    tracked = listed.stdout.splitlines()
    out: list[str] = []
    for entry in piece.files:
        pattern = entry.rstrip("/")
        # A literal reading wins over a glob one: an entry spelled exactly as
        # a tracked file or directory names it even when it reads as a glob.
        # `[id]` is a Next.js dynamic segment, and as an fnmatch class it
        # would match only `i` or `d` (#516).
        under = [t for t in tracked if t.startswith(pattern + "/")]
        if pattern in tracked:
            matched = [pattern]
        elif entry.endswith("/") and under:
            matched = under
        elif any(ch in pattern for ch in "*?["):
            # `**` is not special to fnmatch, but `*` already crosses `/`
            # there, so `src/**/*.py` and `src/*.py` both reach nested files.
            matched = [t for t in tracked if fnmatch.fnmatchcase(t, pattern)]
        else:
            matched = []
        out.extend(t for t in matched if t not in out)
    return out


def present(
    signature: str, *, piece: contracts.Piece, ref: str, repo_root: Path | str
) -> bool | None:
    """Whether *signature*'s name is defined in *piece*'s files at *ref*.

    ``True``/``False`` when the signature names something; ``None`` when it
    does not (see :func:`signature_name`). Looked up only inside the piece's
    own ``files`` boundary: the contract's promise is that *this* piece
    delivers it *there*, and a same-named function elsewhere is someone
    else's.

    A dotted signature whose owner is a type in the boundary is looked up
    among that type's members only: `ConsumeReport.retired` is found as a
    dataclass field, and is not satisfied by a `retired` bound anywhere else
    (#305). The type may be a Python class, a Rust `struct`/`enum`/`trait`
    with its `impl` blocks, or a TS `interface`/`type`/`class`/`enum` —
    `ChildSession.notice` is a struct field (#675). An owner that is no type
    there — a module qualifier — falls back to the bare-name lookup.
    """
    parts = _signature_parts(signature)
    if parts is None:
        return None
    owner, name = parts
    sources = []
    for path in _boundary_files(piece, ref=ref, repo_root=repo_root):
        shown = _git(["show", f"{ref}:{path}"], cwd=repo_root)
        if shown.returncode == 0:
            sources.append((path, shown.stdout))
    if owner is not None:
        found = [m for m in (_owner_members(path, src, owner) for path, src in sources)
                 if m is not None]
        if found:
            return any(name in members for members in found)
    pattern = _definition_re(name)
    return any(pattern.search(src) for _path, src in sources)


# --- inspect: the read-only view --------------------------------------------


@dataclass(frozen=True)
class PieceState:
    """One piece as it stands: where it is, whether it landed, what it carries.

    ``reason`` is filled **only** when the piece cannot be landed, and is the
    sentence the view prints under it. ``exposes`` holds ``(signature,
    present)`` where ``present`` is ``None`` for a signature that names no
    identifier; ``consumes`` holds ``(signature, owner, owner_exposes_it)``,
    the static check that the contract agrees with itself.
    """

    piece: contracts.Piece
    branch: str
    ref: str | None
    pr: str | None
    pr_state: str | None
    exposes: list[tuple[str, bool | None]] = field(default_factory=list)
    consumes: list[tuple[str, str, bool]] = field(default_factory=list)
    reason: str = ""

    @property
    def merged(self) -> bool:
        return self.pr_state == "MERGED"

    @property
    def landable(self) -> bool:
        return not self.reason


def _ref_for(branch: str, *, repo_root: Path | str) -> str | None:
    """The ref that carries *branch*: local first, then ``origin/``, else ``None``.

    Local first because it is what the dispatch created and what a
    ``deliver`` pushed from; ``origin/`` second because a maintainer who
    pruned the worktree after delivering still has the PR's branch on the
    remote. Neither means the piece is unreachable from here — nothing is
    fetched, because the read-only view must not touch the network.
    """
    for candidate, name in (
        (f"refs/heads/{branch}", branch),
        (f"refs/remotes/origin/{branch}", f"origin/{branch}"),
    ):
        if _git(["rev-parse", "--verify", "--quiet", candidate],
                cwd=repo_root).returncode == 0:
            return name
    return None


def base_ref(*, repo_root: Path | str) -> str:
    """``origin/<base>`` when the repo has one, else local ``<base>``.

    *base* is :func:`delivery.base_branch` — what a contract lands onto is
    the same branch ``delivery`` measures against, and on a ``main`` repo a
    hardcoded ``master`` had no tree to rehearse in (#287).

    The remote-tracking ref when there is one, because that is what the PRs
    will be merged onto; the local branch is the only choice in a repo with
    no remote.
    """
    from mnemo.core.sessions import delivery

    base = delivery.base_branch(repo_root=repo_root)
    remote = f"refs/remotes/origin/{base}"
    if _git(["rev-parse", "--verify", "--quiet", remote], cwd=repo_root).returncode == 0:
        return f"origin/{base}"
    return base


def inspect(
    contract: contracts.Contract, *, repo_root: Path | str
) -> list[PieceState]:
    """Every piece in landing order, with what git and ``gh`` say about it.

    Read-only: nothing is fetched, merged or written. Raises
    :class:`LandingError` only for a cycle, which has no order to inspect in;
    every other problem is a ``reason`` on the row it belongs to, so a
    contract with one undelivered piece still shows the other three.
    """
    from mnemo.core.dispatch import branch_name
    from mnemo.core.sessions import delivery

    by_slug = {p.slug: p for p in contract.pieces}
    base = base_ref(repo_root=repo_root)
    out: list[PieceState] = []
    for piece in order(contract):
        branch = branch_name(piece.slug, feature=contract.feature)
        info = delivery.pr_info(branch, repo_root=repo_root)
        pr_url = info.url if info else None
        pr_state = info.state if info else None
        merged = pr_state == "MERGED"
        ref = _ref_for(branch, repo_root=repo_root)

        checked_at = ref if ref is not None else (base if merged else None)
        exposes = [
            (sig, present(sig, piece=piece, ref=checked_at, repo_root=repo_root)
             if checked_at else None)
            for sig in piece.exposes
        ]
        consumes = [
            (sig, owner, _owner_exposes(by_slug.get(owner), sig))
            for sig, owner in piece.consumes
        ]

        # Every `reason` built here is English: it is data, carried by
        # `PieceState` and spliced into `LandingError` for callers, not a line
        # written for a reader. The `land` command's own presentation layer
        # (`cli/commands/land.py`) prints Portuguese around it, and that
        # boundary is deliberate — do not match its language here.
        reason = ""
        if merged:
            pass  # already landed; its signatures are checked on the base
        elif info is None:
            reason = (f"no PR — not delivered yet: run `mnemo deliver {piece.slug}`")
        elif pr_state == "CLOSED":
            reason = f"PR closed without merging: {pr_url}"
        elif ref is None:
            reason = (f"branch {branch} not found locally or on origin — "
                      "nothing to rehearse the merge from")
        if not reason:
            missing = [sig for sig, ok in exposes if ok is False]
            if missing:
                reason = (f"exposes {', '.join(missing)} but no such definition "
                          f"in {', '.join(piece.files)} at {checked_at}")
        if not reason:
            unowned = [(sig, owner) for sig, owner, ok in consumes if not ok]
            if unowned:
                sig, owner = unowned[0]
                reason = (f"consumes {sig} from {owner}, but {owner} exposes "
                          "no signature by that name")
        if not reason and pr_url and pr_state == "OPEN":
            # Last, and only for a piece that would otherwise land: the child
            # stops in seconds and CI takes minutes, so nothing else stands
            # between a red PR and the merge. One `gh` call per open piece.
            red = failing_checks(pr_url)
            if red:
                reason = (f"CI red on {', '.join(red[:3])}"
                          + (f" (+{len(red) - 3})" if len(red) > 3 else ""))
        # For a merged piece, ref reads as the base it was checked on.
        out.append(PieceState(
            piece=piece, branch=branch, ref=checked_at if merged else ref,
            pr=pr_url, pr_state=pr_state, exposes=exposes, consumes=consumes,
            reason=reason,
        ))
    return out


def _owner_exposes(owner: contracts.Piece | None, signature: str) -> bool:
    """Whether *owner*'s ``exposes`` carries *signature*'s name.

    Name equality, not string equality — the granularity
    ``contracts._validate`` argues for. A consumed signature with no name
    (a CLI shape) is taken on trust: there is nothing to compare.
    """
    if owner is None:
        return False
    name = signature_name(signature)
    if name is None:
        return True
    return any(signature_name(sig) == name for sig in owner.exposes)


# --- rehearse: the merge, somewhere it can be thrown away -------------------


@dataclass(frozen=True)
class Step:
    """One piece's outcome in a rehearsal or a merge."""

    slug: str
    ok: bool
    skipped: bool = False
    detail: str = ""


@dataclass(frozen=True)
class Rehearsal:
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    @property
    def failed(self) -> Step | None:
        return next((s for s in self.steps if not s.ok), None)


# How much of a red suite's output to keep: enough for the failing test names
# and the summary line, which is what the maintainer needs to decide.
_TAIL_LINES = 30


def _suite_env(tree: Path) -> dict[str, str]:
    """The environment the suite runs in: the rehearsal tree first on the path.

    An editable install resolves ``import mnemo`` to the checkout it was
    installed from — never a temporary worktree — so a suite run there would
    test the wrong code and pass for the wrong reason. A tree with a ``src``
    layout gets its own ``src`` prepended to ``PYTHONPATH``; any other layout
    is left to the command the maintainer passed.
    """
    env = dict(os.environ)
    src = tree / "src"
    if src.is_dir():
        prior = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(src) + (os.pathsep + prior if prior else "")
    return env


def rehearse(
    states: Sequence[PieceState],
    *,
    repo_root: Path | str,
    suite: Sequence[str],
    force: bool = False,
) -> Rehearsal:
    """Merge the pieces in order in a throwaway worktree, checking at each step.

    For each piece that is not already merged: merge its ref, confirm its
    ``exposes`` are defined in its files in the merged tree, confirm every
    ``consumes`` is defined in the *owner's* files in the merged tree — the
    owner landed earlier, so this is the first moment the consumed signature
    either exists or does not — then run *suite* in the tree. The first
    conflict, missing name or non-zero suite stops the rehearsal, and the
    step it stopped on carries the detail.

    The worktree is created under the system temp dir, not beside the repo:
    a sibling named ``<repo>-wt-c-land`` would be read by ``deliver
    --review`` as a dispatch child while the rehearsal ran. It is removed on
    every path, including an exception in the suite.

    Refuses a list with a piece :func:`inspect` did not find landable, unless
    *force* — the rehearsal's own checks are then what stands between a bad
    contract and the merge. Touches nothing but the temporary tree.
    """
    import shutil
    import subprocess
    import tempfile

    blocked = [s for s in states if not s.landable]
    if blocked and not force:
        raise LandingError(
            "not landable: " + "; ".join(f"{s.piece.slug}: {s.reason}" for s in blocked)
        )

    by_slug = {s.piece.slug: s.piece for s in states}
    root = Path(repo_root)
    base = base_ref(repo_root=root)
    tree = Path(tempfile.mkdtemp(prefix="mnemo-land-"))
    steps: list[Step] = []
    try:
        # mkdtemp made the directory; `worktree add` wants to create it.
        tree.rmdir()
        made = _git(["worktree", "add", "--detach", str(tree), base], cwd=root)
        if made.returncode != 0:
            raise LandingError(
                f"could not create a rehearsal worktree from {base}: "
                f"{made.stderr.strip() or made.stdout.strip()}"
            )
        for state in states:
            piece = state.piece
            if state.merged:
                steps.append(Step(piece.slug, ok=True, skipped=True,
                                  detail=f"already merged: {state.pr}"))
                continue
            merged = _git(["merge", "--no-ff", "--no-edit", state.ref], cwd=tree)
            if merged.returncode != 0:
                _git(["merge", "--abort"], cwd=tree)
                steps.append(Step(piece.slug, ok=False, detail=(
                    f"merge conflict merging {state.ref} onto the pieces before it:\n"
                    + (merged.stdout.strip() or merged.stderr.strip())
                )))
                break

            missing = [sig for sig in piece.exposes
                       if present(sig, piece=piece, ref="HEAD", repo_root=tree) is False]
            if missing:
                steps.append(Step(piece.slug, ok=False, detail=(
                    f"after merging, {piece.slug} does not define "
                    f"{', '.join(missing)} in {', '.join(piece.files)}"
                )))
                break
            unmet = [
                (sig, owner) for sig, owner in piece.consumes
                if owner in by_slug
                and present(sig, piece=by_slug[owner], ref="HEAD", repo_root=tree) is False
            ]
            if unmet:
                sig, owner = unmet[0]
                steps.append(Step(piece.slug, ok=False, detail=(
                    f"{piece.slug} consumes {sig} from {owner}, and the merged "
                    f"tree has no such definition in {', '.join(by_slug[owner].files)}"
                )))
                break

            try:
                ran = subprocess.run(
                    list(suite), cwd=str(tree), capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    env=_suite_env(tree),
                )
            except (FileNotFoundError, OSError) as exc:
                steps.append(Step(piece.slug, ok=False,
                                  detail=f"suite could not run: {exc}"))
                break
            if ran.returncode != 0:
                tail = "\n".join((ran.stdout + ran.stderr).splitlines()[-_TAIL_LINES:])
                steps.append(Step(piece.slug, ok=False, detail=(
                    f"suite red after merging {piece.slug} "
                    f"(exit {ran.returncode}):\n{tail}"
                )))
                break
            steps.append(Step(piece.slug, ok=True, detail=f"merged {state.ref}, suite green"))
    finally:
        _git(["worktree", "remove", "--force", str(tree)], cwd=root)
        if tree.exists():
            shutil.rmtree(tree, ignore_errors=True)
        _git(["worktree", "prune"], cwd=root)
    return Rehearsal(steps=steps)


# --- merge: for real, in the same order -------------------------------------


def merge_prs(
    states: Sequence[PieceState],
    *,
    repo_root: Path | str,
    method: str = "squash",
    admin: bool = False,
) -> list[Step]:
    """``gh pr merge`` each open piece in order. Stops at the first refusal.

    Not ``--admin`` by default: a branch protection that refuses the merge is
    a rule the maintainer set, and this command is not the place to override
    it on its own. ``admin=True`` passes ``--admin`` through, for the
    maintainer who typed ``--admin``. On this repo master used to require a
    code-owner approval no one else could give, so every merge was one; the
    maintainer is replacing that rule with required status checks (#638),
    which a green PR passes without it.
    The refusal's stderr is the step's detail, and a rerun skips whatever did
    land — ``gh`` reports it as ``MERGED`` — so a landing that stopped
    part-way is resumed by running it again.
    """
    import subprocess

    steps: list[Step] = []
    for state in states:
        if state.merged:
            steps.append(Step(state.piece.slug, ok=True, skipped=True,
                              detail=f"already merged: {state.pr}"))
            continue
        try:
            result = subprocess.run(
                ["gh", "pr", "merge", str(state.pr), f"--{method}"]
                + (["--admin"] if admin else []),
                cwd=str(repo_root), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
        except (FileNotFoundError, OSError) as exc:
            steps.append(Step(state.piece.slug, ok=False, detail=f"gh unavailable: {exc}"))
            break
        if result.returncode != 0:
            steps.append(Step(state.piece.slug, ok=False, detail=(
                result.stderr.strip() or result.stdout.strip() or "gh pr merge failed"
            )))
            break
        steps.append(Step(state.piece.slug, ok=True, detail=f"merged {state.pr}"))
    return steps
