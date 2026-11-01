"""Provider-agnostic LLM adapters.

All providers implement the same JSON-returning interface. AI is entirely
optional: when no provider is enabled, ``get_provider`` returns None and the
enrichment API reports AI as disabled instead of failing the product.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import AiProvider
from ..security import decrypt_secret, validate_outbound_url


class AIError(RuntimeError):
    pass


@dataclass
class LLMResult:
    data: dict
    tokens_in: int
    tokens_out: int
    raw: str


class BaseProvider:
    kind = "base"

    def __init__(self, record: AiProvider) -> None:
        self.record = record
        self.model = record.model
        self.name = record.name
        self.api_key = decrypt_secret(record.api_key_enc) if record.api_key_enc else None
        self.base_url = (record.base_url or "").rstrip("/")

    def complete_json(self, system: str, user: str, schema_hint: dict) -> LLMResult:
        raise NotImplementedError


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise AIError(f"Provider returned invalid JSON: {exc}") from exc
        raise AIError("Provider did not return JSON")


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI, Azure-compatible gateways, vLLM, LM Studio, DeepSeek, etc."""

    kind = "openai"

    def complete_json(self, system: str, user: str, schema_hint: dict) -> LLMResult:
        if not self.base_url:
            raise AIError("base_url is required for OpenAI-compatible providers")
        if not self.api_key:
            raise AIError("api_key is required for this provider")
        validate_outbound_url(self.base_url)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        if resp.status_code >= 400:
            raise AIError(f"Provider HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        return LLMResult(
            data=_extract_json(content),
            tokens_in=int(usage.get("prompt_tokens") or len(user) // 4),
            tokens_out=int(usage.get("completion_tokens") or len(content) // 4),
            raw=content,
        )


class OllamaProvider(BaseProvider):
    kind = "ollama"

    def complete_json(self, system: str, user: str, schema_hint: dict) -> LLMResult:
        base = self.base_url or "http://localhost:11434"
        payload = {
            "model": self.model or "llama3.1",
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=300) as client:
            resp = client.post(f"{base}/api/chat", json=payload)
        if resp.status_code >= 400:
            raise AIError(f"Ollama HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        content = body.get("message", {}).get("content", "")
        return LLMResult(
            data=_extract_json(content),
            tokens_in=int(body.get("prompt_eval_count") or len(user) // 4),
            tokens_out=int(body.get("eval_count") or len(content) // 4),
            raw=content,
        )


class AnthropicProvider(BaseProvider):
    kind = "anthropic"

    def complete_json(self, system: str, user: str, schema_hint: dict) -> LLMResult:
        if not self.api_key:
            raise AIError("api_key is required for Anthropic")
        base = self.base_url or "https://api.anthropic.com"
        validate_outbound_url(base)
        payload = {
            "model": self.model or "claude-3-5-sonnet-latest",
            "max_tokens": 2000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{base}/v1/messages", json=payload, headers=headers)
        if resp.status_code >= 400:
            raise AIError(f"Anthropic HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        content = "".join(b.get("text", "") for b in body.get("content", []))
        usage = body.get("usage") or {}
        return LLMResult(
            data=_extract_json(content),
            tokens_in=int(usage.get("input_tokens") or len(user) // 4),
            tokens_out=int(usage.get("output_tokens") or len(content) // 4),
            raw=content,
        )


class MockProvider(BaseProvider):
    """Deterministic, local, offline provider used for demos and tests.

    It never leaves the machine and is always labelled ``mock`` in the UI and in
    every enrichment record, so its output can never be mistaken for a real
    model's analysis.
    """

    kind = "mock"

    def complete_json(self, system: str, user: str, schema_hint: dict) -> LLMResult:
        payload: dict = {}
        start = user.find("{")
        if start >= 0:
            try:
                payload = json.loads(user[start:])
            except json.JSONDecodeError:
                payload = {}
        target = payload.get("target") or {}
        name = target.get("name") or target.get("title") or "target"
        node_type = target.get("type") or "NODE"
        facts = [str(f) for f in (payload.get("deterministic_facts") or [])]
        relationships = payload.get("relationships") or []
        excerpts = payload.get("source_excerpts") or []
        category = payload.get("category") or "OVERVIEW"
        endpoints = [f.split("path=", 1)[1] for f in facts if "path=" in f]
        deps_out = [r for r in relationships if r.get("direction") == "out"]
        deps_in = [r for r in relationships if r.get("direction") == "in"]
        summary = (
            f"{node_type.title()} {name}: {len(facts)} deterministic fact(s), "
            f"{len(deps_out)} outbound and {len(deps_in)} inbound relationship(s)."
        )
        if endpoints:
            summary += f" Exposes {', '.join(endpoints[:4])}."
        responsibilities = facts[:8] or [f"No indexed facts were available for {name}."]
        capabilities = [
            f"{r.get('type')} → {r.get('other')} ({r.get('other_type')})" for r in deps_out[:6]
        ]
        risks = [
            "Mock provider: this text is generated locally from deterministic evidence and is "
            "not a real model analysis.",
        ]
        if not excerpts:
            risks.append("No source excerpt was available for this target; confidence is low by design.")
        data = {
            "summary": summary[:600],
            "responsibilities": responsibilities,
            "capabilities": capabilities,
            "business_rules": [],
            "steps": [
                {"name": name, "action": f"entry point of {category.lower()}"}
            ] if category in ("BUSINESS_FLOW", "CROSS_SERVICE_FLOW") else [],
            "risks": risks,
            "documentation": summary,
            "claims": [{"claim": f, "basis": "scanner-evidence"} for f in facts[:6]],
            "confidence": 0.42,
        }
        return LLMResult(data=data, tokens_in=len(user) // 4, tokens_out=len(json.dumps(data)) // 4, raw=json.dumps(data))


PROVIDERS = {
    "openai": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
    "mock": MockProvider,
}


def provider_for(record: AiProvider) -> BaseProvider:
    cls = PROVIDERS.get(record.kind)
    if cls is None:
        raise AIError(f"Unknown provider kind {record.kind}")
    return cls(record)


def get_provider(db: Session, workspace_id: str | None) -> BaseProvider | None:
    stmt = select(AiProvider).where(AiProvider.enabled.is_(True))
    if workspace_id:
        stmt = stmt.where(or_(AiProvider.workspace_id == workspace_id, AiProvider.workspace_id.is_(None)))
        stmt = stmt.order_by(AiProvider.workspace_id.desc().nullslast(), AiProvider.is_default.desc())
    else:
        stmt = stmt.where(AiProvider.workspace_id.is_(None)).order_by(AiProvider.is_default.desc())
    record = db.scalar(stmt)
    return provider_for(record) if record else None


def test_provider(record: AiProvider) -> dict:
    try:
        provider = provider_for(record)
        result = provider.complete_json(
            "You are a connectivity probe. Reply with JSON only.",
            "Reply with {\"ok\": true} and one short sentence.",
            {"ok": "boolean"},
        )
        return {"ok": True, "tokens_in": result.tokens_in, "tokens_out": result.tokens_out}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:400]}
