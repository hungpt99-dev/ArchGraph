"""Go extractor: packages, structs, methods, HTTP routes and Kafka usage."""

from __future__ import annotations

import re

from .base import FileResult, RepoContext, SNode, brace_match, line_of, mask_go, string_literals

ROUTE_RE = re.compile(
    r"\b(\w+)\s*\.\s*(GET|POST|PUT|PATCH|DELETE|HandleFunc|Handle)\s*\(\s*[\"`]([^\"`]+)[\"`]"
)
KAFKA_TOPIC_RE = re.compile(r"Topic(?:s)?\s*:\s*(?:\[\]string\{)?\s*[\"`]([^\"`]+)[\"`]")


class GoExtractor:
    language = "go"
    extensions = (".go",)

    def extract(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        res = FileResult(path=path, language="go")
        try:
            code = mask_go(text)
            pkg = re.search(r"^\s*package\s+(\w+)", code, re.M)
            pkg_name = pkg.group(1) if pkg else ""
            imports = re.findall(r"^\s*\"([\w./-]+)\"\s*$", code, re.M)
            res.imports = imports
            service_name = ctx.app_name or ctx.repository_name
            service_key = ctx.scope(f"service:{service_name}")
            if pkg_name == "main":
                res.add(SNode(key=service_key, type="SERVICE", name=service_name,
                              qualified_name=service_name,
                              properties={"framework": "go", "language": "go", "port": ctx.port},
                              detection="FRAMEWORK_INFERRED", confidence=0.85))

            for m in re.finditer(r"type\s+([A-Z]\w*)\s+(struct|interface)\s*\{", code):
                brace = code.find("{", m.start())
                close = brace_match(code, brace)
                if close < 0:
                    continue
                res.add(SNode(key=ctx.scope(f"class:{pkg_name}.{m.group(1)}"),
                              type="CLASS" if m.group(2) == "struct" else "INTERFACE",
                              name=m.group(1), qualified_name=f"{pkg_name}.{m.group(1)}",
                              parent_key=service_key, file_path=path,
                              start_line=line_of(text, m.start()), end_line=line_of(text, close),
                              properties={"kind": m.group(2), "package": pkg_name}))

            for m in re.finditer(r"^func\s+(?:\(\s*\w+\s+\*?(\w+)\s*\)\s*)?(\w+)\s*\(([^)]*)\)([^{]*)\{", code, re.M):
                recv, name, params, ret = m.group(1), m.group(2), m.group(3), m.group(4).strip()
                brace = code.find("{", m.end() - 1)
                close = brace_match(code, brace)
                key = ctx.scope(f"fn:{pkg_name}.{recv + '.' if recv else ''}{name}")
                res.add(SNode(key=key, type="METHOD" if recv else "FUNCTION", name=name,
                              qualified_name=f"{pkg_name}.{(recv + '.') if recv else ''}{name}",
                              parent_key=service_key, file_path=path,
                              start_line=line_of(text, m.start()),
                              end_line=line_of(text, close if close > 0 else m.end()),
                              properties={"receiver": recv, "returns": ret, "package": pkg_name,
                                          "params": params.strip()}))

            for m in ROUTE_RE.finditer(code):
                verb = m.group(2).upper()
                if verb in ("HANDLEFUNC", "HANDLE"):
                    verb = "GET"
                route = m.group(3)
                if not route.startswith("/"):
                    continue
                ep_key = ctx.scope(f"api:{verb}:{route}")
                res.add(SNode(key=ep_key, type="API_ENDPOINT", name=f"{verb} {route}",
                              qualified_name=f"{verb} {route}", parent_key=service_key,
                              file_path=path, start_line=line_of(text, m.start()),
                              properties={"http_method": verb, "path": route, "framework": "go"},
                              detection="FRAMEWORK_INFERRED", confidence=0.9))
                res.link(type="EXPOSES", src_key=service_key, dst_key=ep_key,
                         detection="FRAMEWORK_INFERRED", confidence=0.9,
                         file_path=path, line=line_of(text, m.start()))

            for chunk in re.finditer(r"kafka\.(?:NewReader|NewWriter)\([^)]*\)", code):
                for topic in KAFKA_TOPIC_RE.findall(chunk.group(0)):
                    res.add(SNode(key=f"topic:{topic}", type="KAFKA_TOPIC", name=topic,
                                  qualified_name=topic, properties={"declared_by": ctx.repository_name},
                                  detection="FRAMEWORK_INFERRED", confidence=0.85,
                                  file_path=path, start_line=line_of(text, chunk.start())))
                    etype = "CONSUMES" if "Reader" in chunk.group(0) else "PUBLISHES"
                    res.link(type=etype, src_key=service_key, dst_key=f"topic:{topic}",
                             properties={"framework": "segmentio/kafka-go"},
                             detection="FRAMEWORK_INFERRED", confidence=0.85,
                             file_path=path, line=line_of(text, chunk.start()))

            for url in string_literals(code):
                if re.match(r"https?://", url) and "localhost" not in url:
                    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
                    if "." in host and not host.endswith(".svc"):
                        continue
                    res.link(type="DEPENDS_ON", src_key=service_key, dst_ref=f"service:{host}",
                             properties={"protocol": "http", "url": url},
                             detection="CONFIGURED_CONNECTION", confidence=0.7,
                             file_path=path, line=None)
            return res
        except Exception as exc:  # pragma: no cover
            res.errors.append(f"{type(exc).__name__}: {exc}")
            return res
