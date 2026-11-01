"""TypeScript / JavaScript extractor (NestJS, Express, Next.js API routes, KafkaJS)."""

from __future__ import annotations

import re

from .base import (
    FileResult,
    RepoContext,
    SNode,
    brace_match,
    line_of,
    mask_comments,
    paren_match,
    split_top_level,
    string_literals,
)

CLASS_RE = re.compile(r"\b(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Z]\w*)")
FUNC_RE = re.compile(r"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\(")
ARROW_RE = re.compile(r"\b(?:export\s+)?const\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?\(")
EXPORT_ARROW_RE = re.compile(r"\bexport\s+(?:default\s+)?(?:async\s+)?(?:function\s+)?(\w+)?\s*\(")
NEST_HTTP = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "All": "ANY"}
CONTROL = {"if", "for", "while", "switch", "catch", "return", "function", "constructor", "new", "typeof"}


class TypeScriptExtractor:
    language = "typescript"
    extensions = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

    def extract(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        lang = "typescript" if path.endswith((".ts", ".tsx")) else "javascript"
        res = FileResult(path=path, language=lang)
        try:
            code = mask_comments(text)
            imports = re.findall(r"import\s+(?:[^'\"]+from\s+)?['\"]([^'\"]+)['\"]", code)
            imports += re.findall(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", code)
            res.imports = imports
            service_name = ctx.app_name or self._package_name(ctx) or ctx.repository_name
            service_key = ctx.scope(f"service:{service_name}")

            router_prefixes = self._router_prefixes(code)
            classes = self._classes(code)
            if classes:
                res.add(
                    SNode(
                        key=service_key,
                        type="SERVICE",
                        name=service_name,
                        qualified_name=service_name,
                        properties={"framework": ctx.npm_deps.get("framework") or "node",
                                    "language": lang, "port": ctx.port},
                        detection="FRAMEWORK_INFERRED",
                        confidence=0.85,
                    )
                )
            for cname, c_start, c_end, c_body_start, c_body_end, base in classes:
                class_key = ctx.scope(f"class:{path}:{cname}")
                res.add(
                    SNode(
                        key=class_key, type="CLASS", name=cname,
                        qualified_name=f"{path}#{cname}", parent_key=service_key,
                        file_path=path, start_line=line_of(text, c_start),
                        end_line=line_of(text, c_end),
                        properties={"extends": base, "controller": path.endswith(".controller.ts")},
                    )
                )
                body = code[c_body_start:c_body_end]
                self._emit_methods(res, ctx, path, text, body, c_body_start, class_key,
                                   service_key, cname)
                # NestJS class-level controller prefix
                if "@Controller" in text[max(0, c_start - 200): c_start]:
                    m = re.search(r"@Controller\(\s*['\"]([^'\"]*)['\"]", text[max(0, c_start - 300): c_start])
                    router_prefixes.setdefault(cname, m.group(1) if m else "")

            for fm in list(FUNC_RE.finditer(code)) + list(ARROW_RE.finditer(code)):
                name = fm.group(1)
                if name in CONTROL:
                    continue
                brace = code.find("{", fm.end())
                if brace == -1:
                    continue
                close = brace_match(code, brace)
                if close == -1:
                    continue
                fn_key = ctx.scope(f"fn:{path}#{name}")
                res.add(
                    SNode(
                        key=fn_key, type="FUNCTION", name=name,
                        qualified_name=f"{path}#{name}", parent_key=service_key,
                        file_path=path, start_line=line_of(text, fm.start()),
                        end_line=line_of(text, close),
                        properties={"exported": "export" in code[max(0, fm.start() - 20): fm.start()]},
                    )
                )

            self._emit_routes(res, ctx, path, text, code, service_key, router_prefixes)
            self._emit_kafka(res, ctx, path, text, code, service_key)
            self._emit_http_calls(res, ctx, path, text, code, service_key)
            self._emit_orm(res, ctx, path, text, code, service_key)
            self._emit_cache(res, ctx, path, text, code, service_key)
            return res
        except Exception as exc:  # pragma: no cover
            res.errors.append(f"{type(exc).__name__}: {exc}")
            return res

    # ------------------------------------------------------------------ #
    def _package_name(self, ctx: RepoContext) -> str | None:
        return ctx.npm_deps.get("_name")

    def _classes(self, code: str):
        out = []
        for m in CLASS_RE.finditer(code):
            brace = code.find("{", m.end())
            if brace == -1:
                continue
            close = brace_match(code, brace)
            if close == -1:
                continue
            header = code[m.end():brace]
            base = None
            bm = re.search(r"extends\s+([\w.]+)", header)
            if bm:
                base = bm.group(1)
            out.append((m.group(1), m.start(), close, brace + 1, close, base))
        return out

    def _emit_methods(self, res, ctx, path, text, body, offset, class_key, service_key, cname):
        for m in re.finditer(r"(?:^|\n)\s*(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*([a-zA-Z_]\w*)\s*\(", body):
            name = m.group(1)
            if name in CONTROL:
                continue
            close_paren = paren_match(body, m.end() - 1)
            if close_paren < 0:
                continue
            brace = body.find("{", close_paren)
            if brace == -1 or brace - close_paren > 200:
                continue
            close = brace_match(body, brace)
            if close == -1:
                continue
            prefix = body[max(0, m.start() - 400): m.start()]
            anns = re.findall(r"@(\w+)\(?\s*['\"]?([^'\")\n]*)['\"]?\)?", prefix)
            method_key = ctx.scope(f"method:{path}#{cname}.{name}")
            res.add(
                SNode(key=method_key, type="METHOD", name=name,
                      qualified_name=f"{path}#{cname}.{name}", parent_key=class_key,
                      file_path=path, start_line=line_of(text, offset + m.start()),
                      end_line=line_of(text, offset + close),
                      properties={"annotations": [a[0] for a in anns]})
            )
            res.link(type="CONTAINS", src_key=class_key, dst_key=method_key)
            for ann, arg in anns:
                if ann in NEST_HTTP:
                    route = arg.strip().strip("/")
                    prefix = self._controller_prefix(text, offset, cname)
                    full = "/" + "/".join(x for x in (prefix.strip("/"), route) if x)
                    self._emit_endpoint(res, ctx, path, text, offset + m.start(), method_key,
                                        service_key, NEST_HTTP[ann], full, cname, name)
                if ann in ("Cron", "Interval", "Timeout"):
                    job_key = ctx.scope(f"job:{path}#{cname}.{name}")
                    res.add(SNode(key=job_key, type="BACKGROUND_JOB", name=f"{cname}.{name}",
                                  qualified_name=f"{path}#{cname}.{name}", parent_key=method_key,
                                  properties={"schedule": arg, "framework": "nestjs-schedule"},
                                  detection="FRAMEWORK_INFERRED", confidence=0.9,
                                  file_path=path, start_line=line_of(text, offset + m.start())))
                    res.link(type="SCHEDULES", src_key=service_key, dst_key=job_key,
                             detection="FRAMEWORK_INFERRED", confidence=0.9)

    def _controller_prefix(self, text: str, offset: int, cname: str) -> str:
        window = text[:offset]
        idx = window.rfind("@Controller")
        if idx == -1:
            return ""
        seg = window[idx: idx + 300]
        m = re.match(r"@Controller\(\s*['\"]([^'\"]*)['\"]", seg)
        return m.group(1) if m else ""

    def _router_prefixes(self, code: str) -> dict:
        out = {}
        for m in re.finditer(r"(?:const|let)\s+(\w+)\s*=\s*(?:express\.)?Router\(\s*\{?\s*(?:prefix:\s*)?['\"]?([^'\")\n]*)", code):
            if m.group(2):
                out[m.group(1)] = m.group(2)
        return out

    def _emit_routes(self, res, ctx, path, text, code, service_key, prefixes):
        pattern = re.compile(
            r"\b(app|router|server|fastify|api)\s*\.\s*(get|post|put|patch|delete|all)\s*\(\s*['\"]([^'\"]+)['\"]"
        )
        for m in pattern.finditer(code):
            var, verb, route = m.group(1), m.group(2).upper(), m.group(3)
            base = prefixes.get(var, "")
            full = "/" + "/".join(x for x in (base.strip("/"), route.strip("/")) if x)
            ep_key = ctx.scope(f"api:{verb}:{full}")
            res.add(SNode(key=ep_key, type="API_ENDPOINT", name=f"{verb} {full}",
                          qualified_name=f"{verb} {full}", parent_key=service_key,
                          file_path=path, start_line=line_of(text, m.start()),
                          properties={"http_method": verb, "path": full,
                                      "framework": "express", "handler_file": path},
                          detection="FRAMEWORK_INFERRED", confidence=0.9))
            res.link(type="EXPOSES", src_key=service_key, dst_key=ep_key,
                     detection="FRAMEWORK_INFERRED", confidence=0.9,
                     file_path=path, line=line_of(text, m.start()))

    def _emit_endpoint(self, res, ctx, path, text, offset, method_key, service_key, verb, full, cname, mname):
        ep_key = ctx.scope(f"api:{verb}:{full}")
        res.add(SNode(key=ep_key, type="API_ENDPOINT", name=f"{verb} {full}",
                      qualified_name=f"{verb} {full}", parent_key=service_key,
                      file_path=path, start_line=line_of(text, offset),
                      properties={"http_method": verb, "path": full, "controller": cname,
                                  "handler": mname, "framework": "nestjs"},
                      detection="FRAMEWORK_INFERRED", confidence=0.95))
        res.link(type="EXPOSES", src_key=service_key, dst_key=ep_key,
                 detection="FRAMEWORK_INFERRED", confidence=0.95, file_path=path,
                 line=line_of(text, offset))
        res.link(type="IMPLEMENTS", src_key=ep_key, dst_key=method_key,
                 properties={"role": "handler"}, detection="FRAMEWORK_INFERRED", confidence=0.95)

    def _emit_kafka(self, res, ctx, path, text, code, service_key):
        for m in re.finditer(r"\.send\s*\(\s*\{[^}]*?topic\s*:\s*['\"]([^'\"]+)['\"]", code, re.S):
            topic = m.group(1)
            self._topic(res, ctx, topic, path, line_of(text, m.start()))
            res.link(type="PUBLISHES", src_key=service_key, dst_key=f"topic:{topic}",
                     properties={"framework": "kafkajs"}, detection="FRAMEWORK_INFERRED",
                     confidence=0.9, file_path=path, line=line_of(text, m.start()))
        for m in re.finditer(r"\.subscribe\s*\(\s*\{[^}]*?topic\s*:\s*['\"]([^'\"]+)['\"]", code, re.S):
            topic = m.group(1)
            self._topic(res, ctx, topic, path, line_of(text, m.start()))
            res.link(type="CONSUMES", src_key=service_key, dst_key=f"topic:{topic}",
                     properties={"framework": "kafkajs"}, detection="FRAMEWORK_INFERRED",
                     confidence=0.9, file_path=path, line=line_of(text, m.start()))

    def _emit_http_calls(self, res, ctx, path, text, code, service_key):
        for m in re.finditer(r"(?:fetch|axios(?:\.\w+)?|got|ky)\s*\(\s*[`'\"](https?://[^`'\"]+)", code):
            url = m.group(1)
            host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
            res.link(type="DEPENDS_ON", src_key=service_key, dst_ref=f"service:{host}",
                     properties={"protocol": "http", "url": url, "mechanism": "http-client"},
                     detection="CONFIGURED_CONNECTION", confidence=0.8,
                     file_path=path, line=line_of(text, m.start()))

    def _emit_orm(self, res, ctx, path, text, code, service_key):
        for m in re.finditer(r"prisma\.(\w+)\.(findMany|findUnique|findFirst|create|update|delete|upsert)", code):
            table = m.group(1)
            table_key = f"table:{ctx.repository_name}:{table}"
            res.add(SNode(key=table_key, type="TABLE", name=table, qualified_name=table,
                          properties={"source": "prisma"}, detection="FRAMEWORK_INFERRED",
                          confidence=0.85, file_path=path, start_line=line_of(text, m.start())))
            etype = "WRITES" if m.group(2) in ("create", "update", "delete", "upsert") else "READS"
            res.link(type=etype, src_key=service_key, dst_key=table_key,
                     properties={"orm": "prisma"}, detection="FRAMEWORK_INFERRED",
                     confidence=0.85, file_path=path, line=line_of(text, m.start()))
        for m in re.finditer(r"@Entity\(\s*\{?\s*(?:name\s*:\s*)?['\"]([^'\"]+)['\"]", code):
            table = m.group(1)
            res.add(SNode(key=f"table:{ctx.repository_name}:{table}", type="TABLE", name=table,
                          qualified_name=table, properties={"source": "typeorm"},
                          detection="FRAMEWORK_INFERRED", confidence=0.9))

    def _emit_cache(self, res, ctx, path, text, code, service_key):
        for m in re.finditer(r"(?:redis|redisClient|cache)\s*\.\s*(?:get|set|del|hget|hset)\s*\(\s*[`'\"]([^`'\"]+)", code):
            name = m.group(1).split(":")[0] or "cache"
            cache_key = f"redis:{name}"
            res.add(SNode(key=cache_key, type="REDIS_CACHE", name=name, qualified_name=name,
                          properties={"client": "redis"}, detection="FRAMEWORK_INFERRED",
                          confidence=0.8))
            res.link(type="USES_CACHE", src_key=service_key, dst_key=cache_key,
                     detection="FRAMEWORK_INFERRED", confidence=0.8,
                     file_path=path, line=line_of(text, m.start()))

    def _topic(self, res, ctx, topic, path, line):
        res.add(SNode(key=f"topic:{topic}", type="KAFKA_TOPIC", name=topic,
                      qualified_name=topic, properties={"declared_by": ctx.repository_name},
                      detection="FRAMEWORK_INFERRED", confidence=0.9,
                      file_path=path, start_line=line))
