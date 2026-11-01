"""Python extractor built on the standard-library ``ast`` module (full AST, no LLM)."""

from __future__ import annotations

import ast
import re

from .base import FileResult, RepoContext, SNode, line_of

HTTP_DECORATORS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE"}


class PythonExtractor:
    language = "python"
    extensions = (".py",)

    def extract(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        res = FileResult(path=path, language="python")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            res.errors.append(f"SyntaxError: {exc}")
            return res

        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        res.imports = imports

        service_name = ctx.app_name or ctx.repository_name
        service_key = ctx.scope(f"service:{service_name}")
        is_service = any(
            (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("FastAPI", "Flask", "Celery"))
            or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("FastAPI", "Flask"))
            for n in ast.walk(tree)
        )
        if is_service:
            res.add(SNode(key=service_key, type="SERVICE", name=service_name,
                          qualified_name=service_name,
                          properties={"framework": "python", "language": "python", "port": ctx.port},
                          detection="FRAMEWORK_INFERRED", confidence=0.9))

        router_prefixes = self._router_prefixes(tree)
        res.metadata["router_prefixes"] = router_prefixes

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._emit_class(res, node, path, ctx, text, service_key, router_prefixes)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._emit_function(res, node, path, ctx, text, service_key, None, "")

        self._emit_module_usage(res, tree, path, ctx, text, service_key)
        return res

    def _router_prefixes(self, tree: ast.Module) -> dict[str, str]:
        out: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                name = node.targets[0].id if isinstance(node.targets[0], ast.Name) else None
                func = node.value.func
                fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if fname == "APIRouter":
                    for kw in node.value.keywords:
                        if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                            out[name or ""] = str(kw.value.value)
        return out

    def _decorators(self, node) -> list[tuple[str, list, dict]]:
        out = []
        for d in node.decorator_list:
            if isinstance(d, ast.Call):
                func = d.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                owner = (
                    func.value.id
                    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    else ""
                )
                out.append((name, list(d.args),
                            {kw.arg: kw.value for kw in d.keywords if kw.arg}, owner))
            elif isinstance(d, ast.Attribute):
                out.append((d.attr, [], {}, ""))
            elif isinstance(d, ast.Name):
                out.append((d.id, [], {}, ""))
        return out

    def _emit_class(self, res, node: ast.ClassDef, path, ctx, text, service_key, prefixes):
        bases = [b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in node.bases]
        class_key = ctx.scope(f"class:{path}::{node.name}")
        table = None
        for item in node.body:
            if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
                if item.targets[0].id == "__tablename__" and isinstance(item.value, ast.Constant):
                    table = item.value.value
        res.add(SNode(key=class_key, type="CLASS", name=node.name,
                      qualified_name=f"{path}::{node.name}", parent_key=service_key,
                      file_path=path, start_line=node.lineno, end_line=node.end_lineno,
                      properties={"bases": bases, "decorators": [d.attr if isinstance(d, ast.Attribute) else d.id for d in node.decorator_list if isinstance(d, (ast.Attribute, ast.Name))],
                                  "table": table}))
        if table:
            table_key = f"table:{ctx.repository_name}:{table}"
            res.add(
                SNode(
                    key=table_key,
                    type="TABLE",
                    name=table,
                    qualified_name=table,
                    properties={"source": "sqlalchemy-model"},
                    detection="FRAMEWORK_INFERRED",
                    confidence=0.9,
                    file_path=path,
                    start_line=node.lineno,
                )
            )
            res.link(type="RELATED_TO", src_key=class_key, dst_key=table_key,
                     properties={"role": "orm-model"}, detection="FRAMEWORK_INFERRED",
                     confidence=0.9, file_path=path, line=node.lineno)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._emit_function(res, item, path, ctx, text, service_key, class_key, node.name)

    def _emit_function(self, res, node, path, ctx, text, service_key, class_key, class_name):
        decorators = self._decorators(node)
        fn_qname = f"{path}::{class_name + '.' if class_name else ''}{node.name}"
        fn_key = ctx.scope(f"fn:{fn_qname}")
        res.add(SNode(key=fn_key, type="METHOD" if class_key else "FUNCTION", name=node.name,
                      qualified_name=fn_qname, parent_key=class_key or service_key,
                      file_path=path, start_line=node.lineno, end_line=node.end_lineno,
                      properties={"decorators": [d[0] for d in decorators],
                                  "args": [a.arg for a in node.args.args],
                                  "async": isinstance(node, ast.AsyncFunctionDef)}))
        if class_key:
            res.link(type="CONTAINS", src_key=class_key, dst_key=fn_key)

        for name, args, kwargs, owner in decorators:
            if name in HTTP_DECORATORS or name == "route":
                route = ""
                if args and isinstance(args[0], ast.Constant):
                    route = str(args[0].value)
                prefix = ""
                if not class_key:
                    prefix = (res.metadata.get("router_prefixes") or {}).get(owner, "")
                full = "/" + "/".join(x for x in (prefix.strip("/"), route.strip("/")) if x)
                if name == "route":
                    methods = kwargs.get("methods")
                    verbs = []
                    if isinstance(methods, (ast.List, ast.Tuple)):
                        verbs = [str(e.value).upper() for e in methods.elts if isinstance(e, ast.Constant)]
                    verb = verbs[0] if verbs else "GET"
                else:
                    verb = HTTP_DECORATORS[name]
                ep_key = ctx.scope(f"api:{verb}:{full}")
                res.add(SNode(key=ep_key, type="API_ENDPOINT", name=f"{verb} {full}",
                              qualified_name=f"{verb} {full}", parent_key=service_key,
                              file_path=path, start_line=node.lineno,
                              properties={"http_method": verb, "path": full,
                                          "handler": node.name, "framework": "python",
                                          "auth": [d[0] for d in decorators if d[0] in ("login_required", "requires_auth")]},
                              detection="FRAMEWORK_INFERRED", confidence=0.95))
                res.link(type="EXPOSES", src_key=service_key, dst_key=ep_key,
                         detection="FRAMEWORK_INFERRED", confidence=0.95,
                         file_path=path, line=node.lineno)
                res.link(type="IMPLEMENTS", src_key=ep_key, dst_key=fn_key,
                         properties={"role": "handler"}, detection="FRAMEWORK_INFERRED", confidence=0.95)
            if name in ("task", "shared_task", "periodic_task"):
                job_key = ctx.scope(f"job:{fn_qname}")
                res.add(SNode(key=job_key, type="BACKGROUND_JOB", name=node.name,
                              qualified_name=fn_qname, parent_key=fn_key,
                              properties={"framework": "celery",
                                          "schedule": self._const_str(kwargs.get("run_every"))},
                              detection="FRAMEWORK_INFERRED", confidence=0.9,
                              file_path=path, start_line=node.lineno))
                res.link(type="SCHEDULES", src_key=service_key, dst_key=job_key,
                         detection="FRAMEWORK_INFERRED", confidence=0.9)

        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                self._emit_call(res, sub, fn_key, path, ctx, text, service_key)

    def _emit_call(self, res, call: ast.Call, fn_key, path, ctx, text, service_key):
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        line = getattr(call, "lineno", None)
        literals = [a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        for url in literals:
            if re.match(r"https?://", url):
                host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
                res.link(type="DEPENDS_ON", src_key=service_key, dst_ref=f"service:{host}",
                         properties={"protocol": "http", "url": url, "mechanism": "http-client",
                                     "caller": fn_key},
                         detection="CONFIGURED_CONNECTION", confidence=0.8,
                         file_path=path, line=line)
        topics: list[str] = []
        for kw in call.keywords:
            if kw.arg in ("topic", "topic_name") and isinstance(kw.value, ast.Constant):
                topics.append(str(kw.value.value))
            if kw.arg == "topics" and isinstance(kw.value, (ast.List, ast.Tuple)):
                topics += [str(e.value) for e in kw.value.elts if isinstance(e, ast.Constant)]
        if name in ("send", "publish", "send_and_wait", "produce") or name.startswith("send_"):
            for t in topics:
                self._topic(res, ctx, t, path, line)
                res.link(type="PUBLISHES", src_key=service_key, dst_key=f"topic:{t}",
                         detection="FRAMEWORK_INFERRED", confidence=0.85,
                         file_path=path, line=line)
        if name in ("subscribe", "start_consuming"):
            for t in topics:
                self._topic(res, ctx, t, path, line)
                res.link(type="CONSUMES", src_key=service_key, dst_key=f"topic:{t}",
                         detection="FRAMEWORK_INFERRED", confidence=0.85,
                         file_path=path, line=line)
        if isinstance(func, ast.Attribute) and name in ("get", "set", "delete", "hget", "hset"):
            if isinstance(func.value, ast.Name) and func.value.id in ("redis", "cache", "redis_client", "r"):
                if literals:
                    cache_name = literals[0].split(":")[0]
                    cache_key = f"redis:{cache_name}"
                    res.add(SNode(key=cache_key, type="REDIS_CACHE", name=cache_name,
                                  qualified_name=cache_name, properties={"client": "redis"},
                                  detection="FRAMEWORK_INFERRED", confidence=0.85))
                    res.link(type="USES_CACHE", src_key=service_key, dst_key=cache_key,
                             detection="FRAMEWORK_INFERRED", confidence=0.85,
                             file_path=path, line=line)
        if isinstance(func, ast.Attribute) and name in ("execute", "executemany", "fetch_all", "fetchone"):
            for lit in literals:
                for table in re.findall(r"(?i)\b(?:from|join|into|update)\s+([a-z_][\w]*)", lit):
                    if table.lower() in ("select", "where", "set"):
                        continue
                    table_key = f"table:{ctx.repository_name}:{table}"
                    res.add(SNode(key=table_key, type="TABLE", name=table, qualified_name=table,
                                  properties={"source": "sql"}, detection="STATIC_AST",
                                  confidence=0.8, file_path=path, start_line=line))

    def _emit_module_usage(self, res, tree, path, ctx, text, service_key):
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.match(r"https?://", node.value):
                url = node.value
                host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
                res.link(type="DEPENDS_ON", src_key=service_key, dst_ref=f"service:{host}",
                         properties={"protocol": "http", "url": url, "mechanism": "config-literal"},
                         detection="CONFIGURED_CONNECTION", confidence=0.6,
                         file_path=path, line=line_of(text, 0))

    def _const_str(self, node) -> str | None:
        return node.value if isinstance(node, ast.Constant) else None

    def _topic(self, res, ctx, topic, path, line):
        res.add(SNode(key=f"topic:{topic}", type="KAFKA_TOPIC", name=topic,
                      qualified_name=topic, properties={"declared_by": ctx.repository_name},
                      detection="FRAMEWORK_INFERRED", confidence=0.9,
                      file_path=path, start_line=line))
