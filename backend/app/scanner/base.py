"""Scanner intermediate representation and language-agnostic helpers.

Extractors are deliberately deterministic: AST/regex + brace matching over the
checked-out revision. They never call an LLM and never guess values: anything
that cannot be resolved statically is emitted as an *unresolved* reference.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class SNode:
    key: str
    type: str
    name: str
    qualified_name: str = ""
    parent_key: str | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    properties: dict = field(default_factory=dict)
    detection: str = "STATIC_AST"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.qualified_name:
            self.qualified_name = self.name


@dataclass
class SEdge:
    type: str
    src_key: str
    dst_key: str | None = None
    dst_ref: str | None = None
    properties: dict = field(default_factory=dict)
    detection: str = "STATIC_AST"
    confidence: float = 1.0
    file_path: str | None = None
    line: int | None = None

    def __post_init__(self) -> None:
        if not self.dst_key and not self.dst_ref:
            raise ValueError(f"edge {self.type} needs dst_key or dst_ref")


@dataclass
class FileResult:
    path: str
    language: str
    nodes: list[SNode] = field(default_factory=list)
    edges: list[SEdge] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def add(self, node: SNode) -> SNode:
        self.nodes.append(node)
        return node

    def link(self, **kwargs) -> SEdge:
        edge = SEdge(**kwargs)
        self.edges.append(edge)
        return edge


@dataclass
class RepoContext:
    """Facts discovered from configuration files before source parsing."""

    repository_id: str
    repository_name: str
    commit: str
    root: str
    app_name: str | None = None
    artifact: str | None = None
    group_id: str | None = None
    port: int | None = None
    language: str | None = None
    jdbc_urls: list[str] = field(default_factory=list)
    redis_hosts: list[str] = field(default_factory=list)
    kafka_bootstrap: list[str] = field(default_factory=list)
    gateway_routes: list[dict] = field(default_factory=list)
    declared_topics: dict[str, str] = field(default_factory=dict)
    maven_deps: list[dict] = field(default_factory=list)
    npm_deps: dict[str, str] = field(default_factory=dict)
    go_module: str | None = None
    service_names: list[str] = field(default_factory=list)
    declared_databases: list[str] = field(default_factory=list)
    modules: list[dict] = field(default_factory=list)
    service_hints: list[dict] = field(default_factory=list)

    def scope(self, key: str) -> str:
        """Namespace a natural key to this repository."""
        return f"repo:{self.repository_id}:{key}"


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
_STR_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`', re.S)


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def mask_comments(text: str, line_comment: str = "//", block: bool = True) -> str:
    """Blank out comments while preserving offsets and newlines.

    String literals are preserved so annotation/argument parsing still works;
    extractors that need literal contents use :func:`string_literals`.
    """
    out = list(text)
    i, n = 0, len(text)
    in_str: str | None = None
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue
        if line_comment and text.startswith(line_comment, i):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if block and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def mask_comments_hash(text: str) -> str:
    return mask_comments(text, line_comment="#", block=False)


def mask_python(text: str) -> str:
    """Blank comments and docstrings, preserving offsets."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if text.startswith('"""', i) or text.startswith("'''", i):
            quote = text[i : i + 3]
            end = text.find(quote, i + 3)
            end = n if end == -1 else end + 3
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
            continue
        if ch in ("'", '"'):
            q = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    i += 1
                    break
                i += 1
            continue
        i += 1
    return "".join(out)


def mask_go(text: str) -> str:
    return mask_comments(text, line_comment="//")


def brace_match(code: str, open_idx: int) -> int:
    """Index of the matching closing brace, or -1."""
    depth = 0
    i = open_idx
    n = len(code)
    in_str: str | None = None
    while i < n:
        ch = code[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def paren_match(code: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    n = len(code)
    in_str: str | None = None
    while i < n:
        ch = code[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def string_literals(segment: str) -> list[str]:
    return [m.group(0)[1:-1] for m in _STR_RE.finditer(segment)]


def normalise_path(path: str) -> str:
    return "/" + path.strip("/")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def stable_hash(*parts: str, length: int = 12) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:length]


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_java_annotation(raw: str) -> tuple[str, dict]:
    """Parse ``@Name(k1=v1, k2={"a","b"})`` into (name, attrs)."""
    raw = raw.strip()
    if not raw.startswith("@"):
        return raw, {}
    name_match = re.match(r"@([\w.]+)", raw)
    if not name_match:
        return raw, {}
    name = name_match.group(1)
    attrs: dict = {}
    rest = raw[name_match.end() :].strip()
    if rest.startswith("("):
        end = paren_match(rest, 0)
        inside = rest[1:end] if end > 0 else rest[1:]
        for part in split_top_level(inside):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                k, v = part.split("=", 1)
                vals = string_literals(v)
                attrs[k.strip()] = vals[0] if vals else v.strip()
                if len(vals) > 1:
                    attrs[k.strip() + "_list"] = vals
            else:
                vals = string_literals(part)
                attrs["value"] = vals[0] if vals else part.strip()
    return name, attrs


def split_top_level(text: str, sep: str = ",") -> list[str]:
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    in_str: str | None = None
    for ch in text:
        if in_str:
            buf.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            buf.append(ch)
            continue
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


HTTP_METHOD_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
    "RequestMapping": "ANY",
}

SECRET_FIELD_HINTS = re.compile(
    r"(?i)(password|secret|token|apiKey|api_key|credential|privateKey|accessKey)"
)
