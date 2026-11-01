"""Java / Spring Boot extractor.

A deterministic, dependency-free structural parser: it masks comments, then
walks type declarations, members and method bodies with brace matching. It
extracts Spring stereotypes, REST mappings, Kafka producers/consumers, JPA
entities/repositories, scheduled jobs, Feign clients, HTTP clients and cache
usage, emitting nodes/edges plus *unresolved* references that the workspace
resolver later binds across repositories.

It intentionally does not claim a runtime call graph: edges carry
``detection`` (STATIC_AST / FRAMEWORK_INFERRED / CONFIGURED_CONNECTION) and a
confidence, and anything ambiguous stays unresolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .base import (
    FileResult,
    HTTP_METHOD_ANNOTATIONS,
    RepoContext,
    SECRET_FIELD_HINTS,
    SEdge,
    SNode,
    brace_match,
    line_of,
    mask_comments,
    paren_match,
    parse_java_annotation,
    split_top_level,
    stable_hash,
    string_literals,
    unique,
)

TYPE_DECL_RE = re.compile(r"\b(class|interface|enum|record|@interface)\s+([A-Z]\w*)")
ANNOTATION_RE = re.compile(r"@([A-Za-z_][\w.]*)")
METHOD_NAME_RE = re.compile(r"([A-Za-z_]\w*)\s*\(")
CONTROL_WORDS = {
    "if", "for", "while", "switch", "catch", "return", "new", "throw", "else",
    "do", "try", "synchronized", "assert", "super", "this", "case", "instanceof",
}
MODIFIERS = {
    "public", "private", "protected", "static", "final", "abstract", "synchronized",
    "native", "default", "strictfp", "transient", "volatile", "sealed", "non-sealed",
}


@dataclass
class JavaMethod:
    name: str
    return_type: str
    params: list[tuple[str, str, list[str]]]  # (type, name, annotations)
    annotations: dict[str, dict]
    start: int
    end: int
    body_start: int
    body_end: int
    is_abstract: bool = False
    line: int = 0


@dataclass
class JavaType:
    kind: str
    name: str
    fqcn: str
    start: int
    end: int
    body_start: int
    body_end: int
    annotations: dict[str, dict] = field(default_factory=dict)
    extends: list[str] = field(default_factory=list)
    implements: list[str] = field(default_factory=list)
    methods: list[JavaMethod] = field(default_factory=list)
    fields: list[dict] = field(default_factory=list)
    line: int = 0


class JavaExtractor:
    language = "java"
    extensions = (".java",)

    def extract(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        res = FileResult(path=path, language="java")
        try:
            code = mask_comments(text)
            pkg_match = re.search(r"\bpackage\s+([\w.]+)\s*;", code)
            pkg = pkg_match.group(1) if pkg_match else ""
            imports = re.findall(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", code, re.M)
            res.imports = imports
            constants = self._collect_constants(code)

            annotations = self._collect_annotations(code)
            types = self._collect_types(code, pkg, annotations, text)
            res.metadata["package"] = pkg
            res.metadata["types"] = [t.name for t in types]

            if pkg:
                pkg_key = ctx.scope(f"pkg:{pkg}")
                res.add(
                    SNode(
                        key=pkg_key,
                        type="PACKAGE",
                        name=pkg,
                        qualified_name=pkg,
                        file_path=path,
                    )
                )

            service_name = self._service_for(path, ctx)
            ctx.current_service = service_name  # type: ignore[attr-defined]
            hint_port = next((h.get("port") for h in getattr(ctx, "service_hints", [])
                              if h.get("name") == service_name and h.get("port")), ctx.port)
            service_key = ctx.scope(f"service:{service_name}")
            boot_app = None
            for t in types:
                if "SpringBootApplication" in t.annotations:
                    boot_app = t

            if boot_app is not None:
                svc_name = getattr(ctx, 'current_service', None) or ctx.app_name or ctx.repository_name
                res.add(
                    SNode(
                        key=ctx.scope(f"service:{svc_name}"),
                        type="SERVICE",
                        name=svc_name,
                        qualified_name=svc_name,
                        properties={
                            "framework": "spring-boot",
                            "artifact": ctx.artifact,
                            "language": "java",
                            "port": hint_port,
                            "package": pkg,
                            "main_class": boot_app.fqcn,
                        },
                        detection="FRAMEWORK_INFERRED",
                        confidence=0.98,
                        file_path=path,
                        start_line=boot_app.line,
                        end_line=line_of(text, boot_app.end),
                    )
                )
                res.link(
                    type="CONTAINS",
                    src_key=service_key,
                    dst_key=ctx.scope(f"class:{boot_app.fqcn}"),
                    properties={"role": "entrypoint"},
                )

            for t in types:
                self._emit_type(res, t, ctx, pkg, path, text, constants, service_key)

            return res
        except Exception as exc:  # pragma: no cover - defensive
            res.errors.append(f"{type(exc).__name__}: {exc}")
            return res

    # ------------------------------------------------------------------ #
    # Structure parsing
    # ------------------------------------------------------------------ #
    def _collect_constants(self, code: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in re.finditer(
            r"(?:static\s+final|final\s+static)\s+String\s+(\w+)\s*=\s*([^;]+);", code
        ):
            vals = string_literals(m.group(2))
            if vals:
                out[m.group(1)] = vals[0]
        return out

    def _collect_annotations(self, code: str) -> list[tuple[int, int, str, str]]:
        found = []
        for m in ANNOTATION_RE.finditer(code):
            start = m.start()
            end = m.end()
            if end < len(code) and code[end] == "(":
                close = paren_match(code, end)
                if close > 0:
                    end = close + 1
            raw = code[start:end]
            if raw.startswith("@interface"):
                continue
            name, _ = parse_java_annotation(raw)
            found.append((start, end, name, raw))
        return found

    def _collect_types(
        self, code: str, pkg: str, annotations: list[tuple[int, int, str, str]], text: str = ""
    ) -> list[JavaType]:
        types: list[JavaType] = []
        consumed: set[int] = set()
        for m in TYPE_DECL_RE.finditer(code):
            if m.start() in consumed:
                continue
            kind, name = m.group(1), m.group(2)
            brace = code.find("{", m.end())
            if brace == -1:
                continue
            close = brace_match(code, brace)
            if close == -1:
                continue
            header = code[m.end() : brace]
            fqcn = f"{pkg}.{name}" if pkg else name
            t = JavaType(
                kind=kind,
                name=name,
                fqcn=fqcn,
                start=m.start(),
                end=close,
                body_start=brace + 1,
                body_end=close,
                line=line_of(code, m.start()),
            )
            ext = re.search(r"\bextends\s+([\w.$<>,\s]+?)(?:\bimplements\b|$)", header)
            if ext:
                t.extends = [x.strip() for x in ext.group(1).split(",") if x.strip()]
            imp = re.search(r"\bimplements\s+([\w.$<>,\s]+)$", header)
            if imp:
                t.implements = [x.strip() for x in imp.group(1).split(",") if x.strip()]
            # annotations directly preceding the declaration (no statement boundary between)
            for (a_start, a_end, a_name, raw) in annotations:
                if a_end <= m.start() and m.start() - a_end < 600:
                    between = code[a_end : m.start()]
                    if self._only_annotations_between(between):
                        _, attrs = parse_java_annotation(raw)
                        t.annotations[a_name.split(".")[-1]] = attrs
            body = code[brace + 1 : close]
            self._parse_members(t, body, brace + 1, text)
            types.append(t)
            # nested types will be picked up by the main regex too; avoid duplicate emit
            for nested in TYPE_DECL_RE.finditer(body):
                consumed.add(nested.start() + brace + 1)
        return types

    def _service_for(self, path: str, ctx: RepoContext) -> str:
        """Resolve the owning service for a file using module-level config hints."""
        best = None
        best_len = -1
        for hint in getattr(ctx, "service_hints", []) or []:
            prefix = hint.get("prefix") or ""
            if prefix and (path == prefix or path.startswith(prefix.rstrip("/") + "/")):
                if len(prefix) > best_len:
                    best, best_len = hint.get("name"), len(prefix)
        if best:
            return best
        return ctx.app_name or ctx.repository_name

    def _only_annotations_between(self, between: str) -> bool:
        """True when the text between an annotation and a declaration holds only
        annotations and modifiers (annotation arguments may contain any char)."""
        i, n = 0, len(between)
        while i < n:
            ch = between[i]
            if ch.isspace():
                i += 1
                continue
            if ch == "@":
                m = re.match(r"@[\w.]+", between[i:])
                if not m:
                    return False
                i += len(m.group(0))
                if i < n and between[i] == "(":
                    close = paren_match(between, i)
                    if close < 0:
                        return False
                    i = close + 1
                continue
            m = re.match(
                r"(public|private|protected|abstract|final|static|sealed|non-sealed|"
                r"strictfp|native|synchronized|default)\b",
                between[i:],
            )
            if m:
                i += len(m.group(0))
                continue
            return False
        return True

    def _parse_members(self, t: JavaType, body: str, offset: int, text: str) -> None:
        i, n = 0, len(body)
        header_start = 0
        while i < n:
            ch = body[i]
            if ch == "(":
                close = paren_match(body, i)
                i = close + 1 if close > 0 else i + 1
                continue
            if ch == ";":
                header = body[header_start:i].strip()
                if header:
                    f = self._parse_field(header)
                    if f:
                        f["line"] = line_of(text, header_start + offset)
                        t.fields.append(f)
                header_start = i + 1
            elif ch == "{":
                header = body[header_start:i].strip()
                close = brace_match(body, i)
                if close == -1:
                    break
                if re.search(r"\b(class|interface|enum|record)\s+[A-Z]\w*", header):
                    header_start = close + 1
                    i = close + 1
                    continue
                if not header:
                    header_start = close + 1
                    i = close + 1
                    continue
                m = self._parse_method(header)
                if m:
                    m.start = header_start + offset
                    m.body_start = i + 1 + offset
                    m.body_end = close + offset
                    m.end = close + offset
                    m.line = line_of(text, m.start)
                    t.methods.append(m)
                header_start = close + 1
                i = close + 1
                continue
            i += 1

    def _parse_field(self, header: str) -> dict | None:
        annotations, rest = self._split_annotations(header)
        rest = re.sub(r"^\s*(?:public|private|protected|static|final|transient|volatile)\s+", "", rest)
        m = re.match(r"([\w.$<>\[\],?\s]+?)\s+(\w+)\s*(?:=.*)?$", rest, re.S)
        if not m:
            return None
        ftype = m.group(1).strip()
        name = m.group(2)
        if "(" in ftype or ftype in MODIFIERS:
            return None
        return {"type": ftype, "name": name, "annotations": annotations}

    def _parse_method(self, header: str) -> JavaMethod | None:
        # Locate the method name first so parameter annotations are preserved.
        m_name = None
        for m in METHOD_NAME_RE.finditer(header):
            idx = m.start(1)
            prefix = header[:idx].strip()
            if not prefix:
                continue
            tokens = prefix.split()
            if tokens[-1] in CONTROL_WORDS or tokens[-1] in MODIFIERS:
                continue
            m_name = m
        if not m_name:
            return None
        name = m_name.group(1)
        if name in CONTROL_WORDS:
            return None
        name_start = m_name.start(1)
        annotations, clean_prefix = self._split_annotations(header[:name_start])
        rest = clean_prefix + header[name_start:]
        open_paren = rest.find("(", len(name))
        close = paren_match(rest, open_paren)
        params_raw = rest[open_paren + 1 : close] if close > 0 else ""
        prefix = header[:name_start].rstrip()
        prefix = re.sub(
            r"\b(?:public|private|protected|static|final|abstract|synchronized|native|default|strictfp)\b",
            " ",
            prefix,
        )
        prefix = re.sub(r"@[\w.]+(?:\([^)]*\))?", " ", prefix)
        prefix = re.sub(r"<[^<>]*>", " ", prefix).strip()
        return_type = prefix.split()[-1] if prefix.split() else "void"
        if return_type in MODIFIERS or return_type == "":
            return_type = "void"
        params: list[tuple[str, str, list[str]]] = []
        for p in split_top_level(params_raw):
            p = p.strip()
            if not p:
                continue
            p_anns = re.findall(r"@(\w+)", p)
            p_clean = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", p).strip()
            p_clean = re.sub(r"\bfinal\b", "", p_clean).strip()
            bits = p_clean.split()
            if len(bits) >= 2:
                params.append((bits[0], bits[-1].replace("...", ""), p_anns))
        return JavaMethod(
            name=name,
            return_type=return_type,
            params=params,
            annotations=annotations,
            start=0,
            end=0,
            body_start=0,
            body_end=0,
            is_abstract=header.rstrip().endswith(";"),
        )

    def _split_annotations(self, header: str) -> tuple[dict[str, dict], str]:
        annotations: dict[str, dict] = {}
        rest = header
        for m in re.finditer(r"@([A-Za-z_][\w.]*)", header):
            if m.start() > 0 and header[m.start() - 1] == "@":
                continue
        # remove annotated segments from the left, in order
        pos = 0
        clean_parts: list[str] = []
        for m in ANNOTATION_RE.finditer(header):
            if m.start() < pos:
                continue
            end = m.end()
            if end < len(header) and header[end] == "(":
                close = paren_match(header, end)
                if close > 0:
                    end = close + 1
            raw = header[m.start() : end]
            name, attrs = parse_java_annotation(raw)
            annotations[name.split(".")[-1]] = attrs
            clean_parts.append(header[pos : m.start()])
            pos = end
        clean_parts.append(header[pos:])
        rest = "".join(clean_parts)
        return annotations, rest

    # ------------------------------------------------------------------ #
    # Emission
    # ------------------------------------------------------------------ #
    def _emit_type(
        self,
        res: FileResult,
        t: JavaType,
        ctx: RepoContext,
        pkg: str,
        path: str,
        text: str,
        constants: dict[str, str],
        service_key: str,
    ) -> None:
        class_key = ctx.scope(f"class:{t.fqcn}")
        node_type = "INTERFACE" if t.kind == "interface" else "CLASS"
        stereotype = None
        for s in ("RestController", "Controller", "Service", "Component", "Repository",
                  "Configuration", "FeignClient", "Entity", "SpringBootApplication"):
            if s in t.annotations:
                stereotype = s
                break
        props = {
            "package": pkg,
            "kind": t.kind,
            "stereotype": stereotype,
            "extends": t.extends,
            "implements": t.implements,
            "annotations": list(t.annotations.keys()),
            "is_controller": "RestController" in t.annotations or "Controller" in t.annotations,
        }
        if "Table" in t.annotations:
            props["table"] = t.annotations["Table"].get("name") or t.name
        if "FeignClient" in t.annotations:
            fc = t.annotations["FeignClient"]
            props["feign_name"] = fc.get("name") or fc.get("value")
            props["feign_url"] = fc.get("url")
        if "RequestMapping" in t.annotations:
            props["base_path"] = t.annotations["RequestMapping"].get("value", "")

        res.add(
            SNode(
                key=class_key,
                type=node_type,
                name=t.name,
                qualified_name=t.fqcn,
                parent_key=ctx.scope(f"pkg:{pkg}") if pkg else service_key,
                file_path=path,
                start_line=t.line,
                end_line=line_of(text, t.end),
                properties=props,
                detection="STATIC_AST",
                confidence=1.0,
            )
        )
        if pkg:
            res.link(type="CONTAINS", src_key=ctx.scope(f"pkg:{pkg}"), dst_key=class_key)
        # class extends / implements -> deterministic inheritance edges
        for parent in t.extends + t.implements:
            parent_simple = re.sub(r"<.*>", "", parent).strip()
            res.link(
                type="IMPLEMENTS",
                src_key=class_key,
                dst_ref=f"class:{parent_simple}",
                properties={"relation": "extends" if parent in t.extends else "implements"},
                confidence=0.95,
            )
            if parent_simple.startswith("JpaRepository") or "Repository" in parent_simple:
                pass

        # JPA entity -> table
        table_name = props.get("table")
        if "Entity" in t.annotations and table_name:
            table_key = f"table:{ctx.repository_name}:{table_name}"
            res.add(
                SNode(
                    key=table_key,
                    type="TABLE",
                    name=table_name,
                    qualified_name=table_name,
                    properties={"source": "jpa-entity", "entity": t.fqcn},
                    detection="FRAMEWORK_INFERRED",
                    confidence=0.95,
                    file_path=path,
                    start_line=t.line,
                )
            )
            res.link(
                type="RELATED_TO",
                src_key=class_key,
                dst_key=table_key,
                properties={"role": "entity-mapping"},
                detection="FRAMEWORK_INFERRED",
                confidence=0.95,
                file_path=path,
                line=t.line,
            )
            for f in t.fields:
                if f["name"] in ("serialVersionUID", "id") or SECRET_FIELD_HINTS.search(f["name"]):
                    continue
                res.add(
                    SNode(
                        key=f"{table_key}:col:{f['name']}",
                        type="COLUMN",
                        name=f["name"],
                        qualified_name=f"{table_name}.{f['name']}",
                        parent_key=table_key,
                        properties={"java_type": f["type"]},
                        detection="FRAMEWORK_INFERRED",
                        confidence=0.85,
                    )
                )

        # repositories read/write their entity table
        if t.kind == "interface" and t.extends:
            for parent in t.extends:
                pm = re.search(r"Jpa(?:Crud)?Repository\s*<\s*([\w.]+)", parent)
                if pm:
                    entity = pm.group(1).split(".")[-1]
                    res.link(
                        type="READS",
                        src_key=class_key,
                        dst_ref=f"table-by-entity:{entity}",
                        properties={"role": "jpa-repository"},
                        detection="FRAMEWORK_INFERRED",
                        confidence=0.9,
                        file_path=path,
                        line=t.line,
                    )
                    res.link(
                        type="WRITES",
                        src_key=class_key,
                        dst_ref=f"table-by-entity:{entity}",
                        properties={"role": "jpa-repository"},
                        detection="FRAMEWORK_INFERRED",
                        confidence=0.9,
                        file_path=path,
                        line=t.line,
                    )

        # Feign client -> target service
        feign = props.get("feign_name")
        if feign:
            res.link(
                type="DEPENDS_ON",
                src_key=service_key,
                dst_ref=f"service:{feign}",
                properties={"protocol": "http", "mechanism": "feign", "client": t.fqcn},
                detection="FRAMEWORK_INFERRED",
                confidence=0.95,
                file_path=path,
                line=t.line,
            )

        # fields -> declared dependencies + kafka/redis detection
        for f in t.fields:
            ftype = re.sub(r"<.*>", "", f["type"]).strip()
            simple_type = ftype.split(".")[-1]
            if simple_type[:1].isupper() and simple_type not in ("String", "Integer", "Long", "Boolean", "Double", "Object"):
                res.link(
                    type="DEPENDS_ON",
                    src_key=class_key,
                    dst_ref=f"class:{simple_type}",
                    properties={"mechanism": "field-injection", "field": f["name"],
                                "annotations": list(f["annotations"].keys())},
                    detection="DECLARED_DEPENDENCY",
                    confidence=0.8,
                    file_path=path,
                    line=f.get("line"),
                )
            if "Value" in f["annotations"]:
                res.metadata.setdefault("config_refs", []).append(f["annotations"]["Value"].get("value"))

        # methods
        for m in t.methods:
            self._emit_method(res, t, m, class_key, ctx, path, text, constants, service_key)

    def _emit_method(
        self,
        res: FileResult,
        t: JavaType,
        m: JavaMethod,
        class_key: str,
        ctx: RepoContext,
        path: str,
        text: str,
        constants: dict[str, str],
        service_key: str,
    ) -> None:
        arity = len(m.params)
        method_key = ctx.scope(f"method:{t.fqcn}#{m.name}/{arity}")
        body = text[m.body_start : m.body_end] if m.body_end > m.body_start else ""
        body_code = mask_comments(body)
        props = {
            "return_type": m.return_type,
            "parameters": [{"type": p[0], "name": p[1]} for p in m.params],
            "annotations": list(m.annotations.keys()),
            "is_public": "public" in m.annotations.get("_modifiers", {}),
            "class": t.fqcn,
        }
        if "Transactional" in m.annotations:
            props["transactional"] = True

        res.add(
            SNode(
                key=method_key,
                type="METHOD",
                name=m.name,
                qualified_name=f"{t.fqcn}#{m.name}",
                parent_key=class_key,
                file_path=path,
                start_line=m.line,
                end_line=line_of(text, m.body_end),
                properties=props,
                detection="STATIC_AST",
                confidence=1.0,
            )
        )
        res.link(type="CONTAINS", src_key=class_key, dst_key=method_key, file_path=path, line=m.line)

        # ---- calls (statically resolvable symbols only) ----
        calls: set[tuple[str, str]] = set()
        for cm in re.finditer(r"(?<![\w.])(?:this\.)?([a-z_]\w*)\s*\(", body_code):
            name = cm.group(1)
            if name in CONTROL_WORDS or name == m.name:
                continue
            if re.search(rf"\b{re.escape(name)}\s*=\s*.*$", body_code[: cm.start()], re.M):
                continue
            calls.add(("self", name))
        field_types = {f["name"]: re.sub(r"<.*>", "", f["type"]).split(".")[-1] for f in t.fields}
        repo_fields = {
            f["name"] for f in t.fields
            if "Repository" in f["type"] or f["type"].endswith("Repository")
        }
        for cm in re.finditer(r"(?<![\w.])([a-zA-Z_]\w*)\s*\.\s*([a-zA-Z_]\w*)\s*\(", body_code):
            var, meth = cm.group(1), cm.group(2)
            if var in ("this", "super"):
                continue
            if var in field_types:
                calls.add((field_types[var], meth))
            elif var[:1].isupper():
                calls.add((var, meth))
        local_methods = {x.name for x in t.methods}
        for owner, meth in sorted(calls):
            line = line_of(text, m.body_start + max(0, body_code.find(meth)))
            if owner == "self" and meth in local_methods:
                target = next((x for x in t.methods if x.name == meth), None)
                res.link(
                    type="CALLS",
                    src_key=method_key,
                    dst_key=ctx.scope(f"method:{t.fqcn}#{meth}/{len(target.params) if target else 0}"),
                    properties={"resolution": "same-class"},
                    detection="STATIC_AST",
                    confidence=0.9,
                    file_path=path,
                    line=line,
                )
            elif owner not in ("self",):
                res.link(
                    type="CALLS",
                    src_key=method_key,
                    dst_ref=f"call:{owner}.{meth}",
                    properties={
                        "resolution": "cross-class",
                        "via": "field" if owner in field_types.values() else "type",
                        "is_repository": any(field_types.get(n) == owner for n in repo_fields),
                    },
                    detection="STATIC_AST",
                    confidence=0.7,
                    file_path=path,
                    line=line,
                )

        # ---- HTTP endpoints on controller methods ----
        if "RestController" in t.annotations or "Controller" in t.annotations:
            for ann, http in HTTP_METHOD_ANNOTATIONS.items():
                if ann in m.annotations:
                    base = t.annotations.get("RequestMapping", {}).get("value", "")
                    paths = m.annotations[ann].get("value_list") or [m.annotations[ann].get("value", "")]
                    for sub in paths:
                        full = self._join_paths(base, sub or "")
                        ep_key = ctx.scope(f"api:{http}:{full}")
                        auth = [
                            a for a in ("PreAuthorize", "Secured", "RolesAllowed", "PermitAll", "Authenticated")
                            if a in m.annotations
                        ]
                        res.add(
                            SNode(
                                key=ep_key,
                                type="API_ENDPOINT",
                                name=f"{http} {full}",
                                qualified_name=f"{http} {full}",
                                parent_key=service_key,
                                file_path=path,
                                start_line=m.line,
                                end_line=line_of(text, m.body_end),
                                properties={
                                    "http_method": http,
                                    "path": full,
                                    "controller": t.fqcn,
                                    "handler": m.name,
                                    "request_dto": m.params[0][0] if m.params else None,
                                    "response_type": m.return_type,
                                    "auth": auth,
                                    "validation": [a for p in m.params for a in p[2] if a == "Valid"],
                                    "service": (getattr(ctx, "current_service", None) or ctx.app_name or ctx.repository_name),
                                },
                                detection="FRAMEWORK_INFERRED",
                                confidence=0.98,
                            )
                        )
                        res.link(
                            type="EXPOSES",
                            src_key=service_key,
                            dst_key=ep_key,
                            detection="FRAMEWORK_INFERRED",
                            confidence=0.98,
                            file_path=path,
                            line=m.line,
                        )
                        res.link(
                            type="IMPLEMENTS",
                            src_key=ep_key,
                            dst_key=method_key,
                            properties={"role": "handler"},
                            detection="FRAMEWORK_INFERRED",
                            confidence=0.98,
                            file_path=path,
                            line=m.line,
                        )

        # ---- Kafka consumers ----
        if "KafkaListener" in m.annotations:
            attrs = m.annotations["KafkaListener"]
            topics = attrs.get("topics_list") or []
            for key in ("topics", "topicPattern", "value"):
                if attrs.get(key):
                    topics.append(attrs[key])
            for raw_topic in unique(topics):
                topic = constants.get(raw_topic, raw_topic)
                if "${" in topic or "}" in topic:
                    res.link(
                        type="CONSUMES",
                        src_key=method_key,
                        dst_ref=f"unresolved-topic:{topic}",
                        properties={"unresolved": True, "raw": raw_topic,
                                    "group": attrs.get("groupId") or attrs.get("containerFactory")},
                        detection="STATIC_AST",
                        confidence=0.4,
                        file_path=path,
                        line=m.line,
                    )
                    continue
                self._emit_topic(res, ctx, topic, path, m.line)
                consumer_key = ctx.scope(f"consumer:{t.fqcn}#{m.name}:{topic}")
                res.add(
                    SNode(
                        key=consumer_key,
                        type="CONSUMER",
                        name=f"{t.name}.{m.name}",
                        qualified_name=f"{t.fqcn}#{m.name}",
                        parent_key=method_key,
                        properties={"topic": topic, "group": attrs.get("groupId"),
                                    "event_class": m.params[0][0] if m.params else None},
                        detection="FRAMEWORK_INFERRED",
                        confidence=0.95,
                        file_path=path,
                        start_line=m.line,
                    )
                )
                res.link(
                    type="CONSUMES",
                    src_key=service_key,
                    dst_key=f"topic:{topic}",
                    properties={"consumer": f"{t.fqcn}#{m.name}", "group": attrs.get("groupId")},
                    detection="FRAMEWORK_INFERRED",
                    confidence=0.95,
                    file_path=path,
                    line=m.line,
                )
                res.link(
                    type="CONSUMES",
                    src_key=consumer_key,
                    dst_key=f"topic:{topic}",
                    detection="FRAMEWORK_INFERRED",
                    confidence=0.95,
                )
                res.link(type="CONTAINS", src_key=method_key, dst_key=consumer_key)

        # ---- Kafka producers (KafkaTemplate.send / StreamBridge / kafkaClient) ----
        producer_pattern = re.compile(r"(?:kafkaTemplate|streamBridge)\s*\.\s*send\s*\(")
        for pm in producer_pattern.finditer(body_code):
            open_idx = pm.end() - 1
            close_paren = paren_match(body_code, open_idx)
            if close_paren < 0:
                continue
            args = split_top_level(body_code[open_idx + 1 : close_paren])
            val = args[0].strip() if args else ""
            topic = None
            if re.fullmatch(r'"[^"]*"', val):
                topic = val[1:-1]
            elif val in constants:
                topic = constants[val]
            if not topic:
                res.link(
                    type="PUBLISHES",
                    src_key=method_key,
                    dst_ref=f"unresolved-topic:{val}",
                    properties={"unresolved": True, "raw": val},
                    detection="STATIC_AST",
                    confidence=0.4,
                    file_path=path,
                    line=m.line,
                )
                continue
            self._emit_topic(res, ctx, topic, path, m.line)
            producer_key = ctx.scope(f"producer:{t.fqcn}#{m.name}:{topic}")
            res.add(
                SNode(
                    key=producer_key,
                    type="PRODUCER",
                    name=f"{t.name}.{m.name}",
                    qualified_name=f"{t.fqcn}#{m.name}",
                    parent_key=method_key,
                    properties={"topic": topic},
                    detection="FRAMEWORK_INFERRED",
                    confidence=0.95,
                    file_path=path,
                    start_line=m.line,
                )
            )
            res.link(
                type="PUBLISHES",
                src_key=service_key,
                dst_key=f"topic:{topic}",
                properties={"producer": f"{t.fqcn}#{m.name}"},
                detection="FRAMEWORK_INFERRED",
                confidence=0.95,
                file_path=path,
                line=m.line,
            )
            res.link(type="PUBLISHES", src_key=producer_key, dst_key=f"topic:{topic}")
            res.link(type="CONTAINS", src_key=method_key, dst_key=producer_key)

        # ---- scheduled / background jobs ----
        if "Scheduled" in m.annotations or "KafkaListener" not in m.annotations and "SchedulerLock" in m.annotations:
            attrs = m.annotations.get("Scheduled", {})
            job_key = ctx.scope(f"job:{t.fqcn}#{m.name}")
            res.add(
                SNode(
                    key=job_key,
                    type="BACKGROUND_JOB",
                    name=f"{t.name}.{m.name}",
                    qualified_name=f"{t.fqcn}#{m.name}",
                    parent_key=method_key,
                    properties={"cron": attrs.get("cron"), "fixed_rate": attrs.get("fixedRate"),
                                "fixed_delay": attrs.get("fixedDelay"), "zone": attrs.get("zone")},
                    detection="FRAMEWORK_INFERRED",
                    confidence=0.95,
                    file_path=path,
                    start_line=m.line,
                )
            )
            res.link(type="SCHEDULES", src_key=service_key, dst_key=job_key,
                     properties={"cron": attrs.get("cron")}, detection="FRAMEWORK_INFERRED", confidence=0.95)
            res.link(type="IMPLEMENTS", src_key=job_key, dst_key=method_key)

        # ---- outbound HTTP ----
        for url in string_literals(body):
            if not re.match(r"https?://", url):
                continue
            host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
            if host in ("localhost", "127.0.0.1"):
                res.link(
                    type="DEPENDS_ON",
                    src_key=service_key,
                    dst_ref=f"service:{host}",
                    properties={"protocol": "http", "url": url, "resolution": "ambiguous"},
                    detection="CONFIGURED_CONNECTION",
                    confidence=0.3,
                    file_path=path,
                    line=m.line,
                )
                continue
            res.link(
                type="DEPENDS_ON",
                src_key=service_key,
                dst_ref=f"service:{host}",
                properties={"protocol": "http", "url": url, "caller": f"{t.fqcn}#{m.name}"},
                detection="CONFIGURED_CONNECTION",
                confidence=0.8,
                file_path=path,
                line=m.line,
            )

        # ---- cache usage ----
        for cm in re.finditer(r"@(Cacheable|CachePut|CacheEvict)\b", text[max(0, m.start - 400): m.start]):
            _ = cm
        for ann in ("Cacheable", "CachePut", "CacheEvict"):
            if ann in m.annotations:
                cache_name = m.annotations[ann].get("value") or m.annotations[ann].get("cacheNames") or "default"
                cache_key = f"redis:{cache_name}"
                res.add(
                    SNode(
                        key=cache_key,
                        type="REDIS_CACHE",
                        name=cache_name,
                        qualified_name=cache_name,
                        properties={"annotation": ann, "key": m.annotations[ann].get("key")},
                        detection="FRAMEWORK_INFERRED",
                        confidence=0.9,
                    )
                )
                res.link(
                    type="USES_CACHE",
                    src_key=service_key,
                    dst_key=cache_key,
                    properties={"annotation": ann, "method": f"{t.fqcn}#{m.name}"},
                    detection="FRAMEWORK_INFERRED",
                    confidence=0.9,
                    file_path=path,
                    line=m.line,
                )

        # ---- SQL in @Query ----
        for q in re.findall(r"@Query\s*\(\s*(?:value\s*=\s*)?\"([^\"]+)\"", text):
            for table in re.findall(r"(?i)\b(?:from|join|into|update)\s+([a-z_][\w]*)", q):
                if table.lower() in ("select", "where", "set", "values"):
                    continue
                table_key = f"table:{ctx.repository_name}:{table}"
                res.add(
                    SNode(key=table_key, type="TABLE", name=table,
                          qualified_name=table, properties={"source": "sql"},
                          detection="STATIC_AST", confidence=0.85)
                )
                for verb, etype in (("select", "READS"), ("insert", "WRITES"),
                                    ("update", "WRITES"), ("delete", "WRITES")):
                    if verb in q.lower():
                        res.link(type=etype, src_key=method_key, dst_key=table_key,
                                 properties={"sql": q[:300]}, detection="STATIC_AST",
                                 confidence=0.85, file_path=path, line=m.line)
                        break

    def _emit_topic(self, res: FileResult, ctx: RepoContext, topic: str, path: str, line: int) -> None:
        name = topic.strip()
        if not name:
            return
        res.add(
            SNode(
                key=f"topic:{name}",
                type="KAFKA_TOPIC",
                name=name,
                qualified_name=name,
                properties={
                    "declared_by": ctx.repository_name,
                    "bootstrap": ctx.kafka_bootstrap,
                },
                detection="FRAMEWORK_INFERRED",
                confidence=0.9,
                file_path=path,
                start_line=line,
            )
        )

    @staticmethod
    def _join_paths(base: str, sub: str) -> str:
        base = (base or "").strip()
        sub = (sub or "").strip()
        if not base.startswith("/") and base:
            base = "/" + base
        path = (base.rstrip("/") + "/" + sub.lstrip("/")).rstrip("/")
        return path or "/"
