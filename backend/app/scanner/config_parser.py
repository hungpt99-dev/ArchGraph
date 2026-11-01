"""Configuration-file intelligence: build system, Spring config, gateway routes, infra.

Configuration is evidence too. This parser feeds the extractors with context
(application name, port, datasource, kafka bootstrap) and emits its own nodes
for infrastructure that is only visible in configuration.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import yaml

from .base import FileResult, RepoContext, SEdge, SNode
from ..security import redact_secrets

POM_NS = "{http://maven.apache.org/POM/4.0.0}"


def _txt(el, tag: str, default: str = "") -> str:
    found = el.find(f"{POM_NS}{tag}") if el.find(f"{POM_NS}{tag}") is not None else el.find(tag)
    return (found.text or default).strip() if found is not None else default


def parse_db_url(url: str) -> dict | None:
    """Parse jdbc:postgresql://host:5432/db or postgres://... into a database identity."""
    if not url:
        return None
    m = re.match(r"(?:jdbc:)?([a-z0-9]+)://([^:/?]+)(?::(\d+))?/([^?;]+)", url.strip())
    if not m:
        return None
    engine = m.group(1).lower()
    if engine in ("postgresql", "postgres"):
        engine = "postgres"
    return {
        "engine": engine,
        "host": m.group(2),
        "port": int(m.group(3) or (5432 if engine == "postgres" else 3306)),
        "database": m.group(4),
        "url_redacted": redact_secrets(url.strip()),
    }


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(flatten(v, key))
            elif isinstance(v, list):
                out[key] = v
                for i, item in enumerate(v):
                    if isinstance(item, dict):
                        out.update(flatten(item, f"{key}[{i}]"))
            else:
                out[key] = v
    return out


class ConfigParser:
    def __init__(self) -> None:
        self.config_nodes: dict[str, tuple[str, int]] = {}

    # ------------------------------------------------------------------ #
    def parse(self, path: str, text: str, ctx: RepoContext) -> FileResult | None:
        name = path.rsplit("/", 1)[-1]
        if name == "pom.xml":
            return self._pom(path, text, ctx)
        if name in ("build.gradle", "build.gradle.kts"):
            return self._gradle(path, text, ctx)
        if name == "package.json":
            return self._package_json(path, text, ctx)
        if name == "go.mod":
            return self._go_mod(path, text, ctx)
        if name in ("application.yml", "application.yaml", "bootstrap.yml", "bootstrap.yaml") or re.match(
            r"application-[\w-]+\.ya?ml$", name
        ):
            return self._yaml_config(path, text, ctx)
        if re.match(r"(application|bootstrap)(-[\w-]+)?\.properties$", name):
            return self._properties(path, text, ctx)
        return None

    # ------------------------------------------------------------------ #
    def _pom(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        res = FileResult(path=path, language="xml")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            res.errors.append(str(exc))
            return res
        artifact = _txt(root, "artifactId")
        group = _txt(root, "groupId") or _txt(root.find(POM_NS + "parent") or root, "groupId")
        if root.tag.endswith("project"):
            if artifact:
                ctx.artifact = ctx.artifact or artifact
                ctx.group_id = ctx.group_id or group
        parent = root.find(POM_NS + "parent")
        if parent is not None and not ctx.group_id:
            ctx.group_id = _txt(parent, "groupId")
        modules = [_txt(m, "artifactId") or (m.text or "") for m in root.findall(f"{POM_NS}modules/{POM_NS}module")]
        modules = [m for m in modules if m]
        deps_el = root.find(f"{POM_NS}dependencies")
        deps = []
        if deps_el is not None:
            for d in deps_el.findall(f"{POM_NS}dependency"):
                deps.append({
                    "group": _txt(d, "groupId"),
                    "artifact": _txt(d, "artifactId"),
                    "scope": _txt(d, "scope", "compile"),
                })
        ctx.maven_deps.extend(deps)
        fq = f"{ctx.group_id or ''}:{ctx.artifact or ''}".strip(":")
        if fq:
            res.add(SNode(key=ctx.scope(f"module:{fq}"), type="MODULE", name=ctx.artifact or fq,
                          qualified_name=fq, parent_key=ctx.scope("module:root"),
                          file_path=path, properties={"build": "maven", "group": ctx.group_id,
                                                      "artifact": ctx.artifact,
                                                      "modules": modules,
                                                      "framework": self._detect_java_framework(deps)},
                          detection="CONFIG_FILE", confidence=1.0))
            for d in deps:
                res.link(type="DECLARED_DEPENDENCY", src_key=ctx.scope(f"module:{fq}"),
                         dst_ref=f"artifact:{d['group']}:{d['artifact']}",
                         properties={"scope": d["scope"], "build": "maven"},
                         detection="DECLARED_DEPENDENCY", confidence=1.0,
                         file_path=path)
            if "spring-boot" in (ctx.artifact or "") and not ctx.app_name:
                ctx.app_name = ctx.artifact
        for mod in modules:
            res.add(SNode(key=ctx.scope(f"module:{mod}"), type="MODULE", name=mod,
                          qualified_name=f"{(ctx.group_id or '')}:{mod}".strip(":"),
                          parent_key=ctx.scope(f"module:{fq}") if fq else None,
                          file_path=path, properties={"build": "maven", "module": True},
                          detection="CONFIG_FILE", confidence=1.0))
            if fq:
                res.link(type="CONTAINS", src_key=ctx.scope(f"module:{fq}"),
                         dst_key=ctx.scope(f"module:{mod}"))
        return res

    def _detect_java_framework(self, deps: list[dict]) -> str | None:
        arts = {d["artifact"] for d in deps}
        if any("spring-boot-starter-web" == a for a in arts):
            return "spring-boot-web"
        if any("spring-boot" in a for a in arts):
            return "spring-boot"
        if any("quarkus" in a for a in arts):
            return "quarkus"
        if any("micronaut" in a for a in arts):
            return "micronaut"
        return None

    def _gradle(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        res = FileResult(path=path, language="groovy")
        deps = [
            {"group": m.group(1), "artifact": m.group(2), "scope": "compile"}
            for m in re.finditer(r"['\"]([\w.\-]+):([\w.\-]+)(?::[\w.\-]+)?['\"]", text)
        ]
        ctx.maven_deps.extend(deps)
        if not ctx.artifact:
            m = re.search(r"rootProject\.name\s*=\s*['\"]([^'\"]+)", text)
            if m:
                ctx.artifact = m.group(1)
        res.metadata["deps"] = len(deps)
        return res

    def _package_json(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        res = FileResult(path=path, language="json")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            res.errors.append(str(exc))
            return res
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        ctx.npm_deps.update({k: str(v) for k, v in deps.items()})
        ctx.npm_deps["_name"] = data.get("name", "")
        ctx.artifact = ctx.artifact or data.get("name")
        framework = None
        for fw, dep in (("nest", "@nestjs/core"), ("express", "express"), ("fastify", "fastify"), ("next", "next")):
            if dep in deps:
                framework = fw
                break
        ctx.npm_deps["framework"] = framework or "node"
        res.add(SNode(key=ctx.scope(f"module:{data.get('name', ctx.repository_name)}"), type="MODULE",
                      name=data.get("name", ctx.repository_name), qualified_name=data.get("name", ""),
                      file_path=path, properties={"build": "npm", "framework": framework,
                                                  "dependencies": len(deps)},
                      detection="CONFIG_FILE", confidence=1.0))
        return res

    def _go_mod(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        res = FileResult(path=path, language="go")
        m = re.search(r"^module\s+(\S+)", text, re.M)
        if m:
            ctx.go_module = m.group(1)
            ctx.artifact = ctx.artifact or m.group(1).split("/")[-1]
            res.add(SNode(key=ctx.scope(f"module:{ctx.artifact}"), type="MODULE", name=ctx.artifact,
                          qualified_name=m.group(1), file_path=path,
                          properties={"build": "go"}, detection="CONFIG_FILE", confidence=1.0))
        return res

    # ------------------------------------------------------------------ #
    def _properties(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        data: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
        return self._apply_spring_config(path, data, ctx)

    def _yaml_config(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            res = FileResult(path=path, language="yaml")
            res.errors.append(str(exc))
            return res
        flat = flatten(data) if isinstance(data, dict) else {}
        return self._apply_spring_config(path, {k: v for k, v in flat.items() if not isinstance(v, (dict, list))},
                                         ctx, raw=data)

    def _apply_spring_config(self, path: str, data: dict, ctx: RepoContext, raw: dict | None = None) -> FileResult:
        res = FileResult(path=path, language="yaml" if path.endswith(("yml", "yaml")) else "properties")
        port = data.get("server.port")
        if port and str(port).isdigit():
            ctx.port = int(port)
        app_name = data.get("spring.application.name") or data.get("application.name")
        if app_name:
            prefix = path.split("/src/main/resources/")[0] if "/src/main/resources/" in path else ""
            ctx.service_names.append(str(app_name))
            ctx.service_hints.append({"prefix": prefix, "name": str(app_name),
                                     "port": ctx.port})
            if not prefix:
                ctx.app_name = str(app_name)
        service_name = str(app_name) if app_name else (ctx.app_name or ctx.repository_name)
        ds_url = (
            data.get("spring.datasource.url")
            or data.get("spring.r2dbc.url")
            or data.get("datasource.url")
        )
        if ds_url and isinstance(ds_url, str):
            ctx.jdbc_urls.append(ds_url)
            db = parse_db_url(ds_url)
            if db:
                db_key = f"db:{db['engine']}:{db['host']}:{db['port']}/{db['database']}"
                res.add(SNode(key=db_key, type="DATABASE", name=db["database"],
                              qualified_name=f"{db['engine']}://{db['host']}:{db['port']}/{db['database']}",
                              properties={**db, "managed": "external", "declared_by": ctx.repository_name},
                              detection="CONFIG_FILE", confidence=1.0, file_path=path))
                res.link(type="DEPENDS_ON", src_key=ctx.scope(f"service:{service_name}"), dst_key=db_key,
                         properties={"mechanism": "jdbc", "config_key": "spring.datasource.url"},
                         detection="CONFIG_FILE", confidence=1.0, file_path=path)
                # table nodes referenced by SQL migrations/resolvers attach to this db
                ctx.declared_databases.append(db_key)
        kafka = data.get("spring.kafka.bootstrap-servers") or data.get("kafka.bootstrap-servers")
        if kafka:
            ctx.kafka_bootstrap = [s.strip() for s in str(kafka).split(",") if s.strip()]
        redis_host = data.get("spring.redis.host") or data.get("spring.data.redis.host")
        if redis_host:
            ctx.redis_hosts.append(str(redis_host))
            cache_key = f"redis:{redis_host}"
            res.add(SNode(key=cache_key, type="REDIS_CACHE", name=str(redis_host),
                          qualified_name=f"redis://{redis_host}",
                          properties={"host": str(redis_host), "port": data.get("spring.redis.port", 6379)},
                          detection="CONFIG_FILE", confidence=0.95, file_path=path))
            res.link(type="USES_CACHE", src_key=ctx.scope(f"service:{service_name}"), dst_key=cache_key,
                     properties={"mechanism": "spring-data-redis"}, detection="CONFIG_FILE",
                     confidence=0.95, file_path=path)
        # gateway routes (list-of-dicts is lost in flatten, so use raw)
        routes = self._gateway_routes(raw)
        for r in routes:
            ctx.gateway_routes.append(r)
        return res

    def _gateway_routes(self, raw: dict | None) -> list[dict]:
        if not isinstance(raw, dict):
            return []
        out: list[dict] = []
        spring = raw.get("spring") or {}
        cloud = (spring.get("cloud") or {}) if isinstance(spring, dict) else {}
        gateway = (cloud.get("gateway") or {}) if isinstance(cloud, dict) else {}
        for r in gateway.get("routes") or []:
            if not isinstance(r, dict):
                continue
            uri = str(r.get("uri", ""))
            paths: list[str] = []
            for pred in r.get("predicates") or []:
                if isinstance(pred, str) and pred.startswith("Path="):
                    paths += [p.strip() for p in pred[5:].split(",")]
                elif isinstance(pred, dict):
                    args = pred.get("args") or {}
                    if isinstance(args, dict):
                        patterns = args.get("patterns") or args.get("pattern")
                        if isinstance(patterns, str):
                            paths += [p.strip() for p in patterns.split(",")]
            out.append({"id": r.get("id"), "uri": uri, "paths": paths})
        return out
