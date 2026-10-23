"""OpenAI-compatible AI provider — stateless, in-memory API key only.

Never persists API keys to disk. Never sends unsanitized config.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from http.client import HTTPException
from urllib.parse import urlsplit

# ── Data types ────────────────────────────────────────────────


@dataclass
class AIModel:
    id: str
    provider: str = ""
    context_length: int = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "provider": self.provider}


@dataclass
class ProviderConfig:
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    provider_name: str = "custom"

    _api_key_set: bool = field(default=False, repr=False)

    def __post_init__(self):
        if self.api_key:
            self._api_key_set = True


@dataclass
class AIResult:
    status: str  # "ok" | "warn" | "attention" | "error"
    severity: str  # "low" | "medium" | "high" | "critical"
    summary: str
    possible_causes: list[str] = field(default_factory=list)
    recommended_checks: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    analyzed_at: str = ""
    raw_error: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "severity": self.severity,
            "summary": self.summary,
            "possible_causes": self.possible_causes,
            "recommended_checks": self.recommended_checks,
            "evidence": self.evidence,
            "model": self.model,
            "provider": self.provider,
            "analyzed_at": self.analyzed_at,
        }


# ── Presets ────────────────────────────────────────────────────

PROVIDER_PRESETS: dict[str, dict] = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "description": "DeepSeek API (OpenAI-compatible)",
    },
    "openai_compatible": {
        "name": "OpenAI-Compatible",
        "base_url": "",
        "description": "Any OpenAI-compatible API endpoint",
    },
    "custom": {
        "name": "Custom",
        "base_url": "",
        "description": "Manual base URL",
    },
}


# ── Provider ──────────────────────────────────────────────────


MAX_RESPONSE_BYTES = 1_048_576


class AIProviderError(RuntimeError):
    """A fixed provider failure code; upstream bodies and credentials are excluded."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AIProvider(ABC):
    @abstractmethod
    def test_connection(self) -> dict: ...

    @abstractmethod
    def list_models(self) -> list[AIModel]: ...

    @abstractmethod
    def analyze(self, context: dict) -> AIResult: ...


class OpenAICompatibleProvider(AIProvider):
    """Standard OpenAI-compatible chat completions provider.

    API Key is held in memory only. Never persisted to disk.
    Context is sanitized before sending.
    """

    def __init__(self, config: ProviderConfig, timeout: int = 30):
        self._validate_config(config)
        self._config = config
        self._timeout = timeout

    @staticmethod
    def _validate_config(config: ProviderConfig) -> None:
        fields = ((config.base_url, 4096), (config.api_key, 8192),
                  (config.model, 256), (config.provider_name, 128))
        if any(not isinstance(value, str) or len(value) > limit or
               any(ord(char) < 32 or ord(char) == 127 for char in value)
               for value, limit in fields):
            raise AIProviderError("AI_PROVIDER_CONFIG_INVALID")
        try:
            url = urlsplit(config.base_url)
            valid = (url.scheme in {"http", "https"} and bool(url.hostname)
                     and url.username is None and url.password is None
                     and not url.query and not url.fragment
                     and not any(char.isspace() for char in config.base_url))
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise AIProviderError("AI_PROVIDER_CONFIG_INVALID")

    @property
    def model(self) -> str:
        """Expose the configured model for the existing R4 assessment cache key."""
        return self._config.model

    @property
    def provider_name(self) -> str:
        """Expose only the non-secret provider label for product DTOs."""
        return self._config.provider_name

    def _base(self) -> str:
        return self._config.base_url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
        }

    def _request_json(self, request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise AIProviderError("AI_RESPONSE_TOO_LARGE")
            data = json.loads(body)
            if not isinstance(data, dict):
                raise AIProviderError("AI_RESPONSE_INVALID")
            return data
        except AIProviderError:
            raise
        except urllib.error.HTTPError as error:
            error.close()
            raise AIProviderError(f"AI_HTTP_{error.code}") from None
        except (urllib.error.URLError, HTTPException, OSError, TimeoutError):
            raise AIProviderError("AI_CONNECTION_FAILED") from None
        except (ValueError, TypeError, UnicodeError):
            raise AIProviderError("AI_RESPONSE_INVALID") from None

    def test_connection(self) -> dict:
        if not self._config.model.strip():
            return {"ok": False, "error": "AI_MODEL_REQUIRED"}
        start = time.monotonic()
        try:
            models = self.list_models()
            if not models:
                raise AIProviderError("AI_MODELS_EMPTY")
            if self._config.model not in {model.id for model in models}:
                raise AIProviderError("AI_MODEL_UNAVAILABLE")
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {
                "ok": True,
                "latency_ms": elapsed_ms,
                "models_available": len(models),
                "message": "Connection and selected model validated; generation was not tested.",
            }
        except AIProviderError as error:
            return {"ok": False, "error": error.code}

    def list_models(self) -> list[AIModel]:
        request = urllib.request.Request(
            f"{self._base()}/models", headers=self._headers(), method="GET"
        )
        data = self._request_json(request).get("data")
        if not isinstance(data, list) or len(data) > 1000:
            raise AIProviderError("AI_MODELS_INVALID")
        models: dict[str, AIModel] = {}
        for item in data:
            model_id = item.get("id") if isinstance(item, dict) else None
            if (not isinstance(model_id, str) or not model_id.strip()
                    or len(model_id) > 256
                    or any(ord(char) < 32 or ord(char) == 127 for char in model_id)
                    or self._config.api_key and self._config.api_key in model_id):
                raise AIProviderError("AI_MODELS_INVALID")
            models[model_id] = AIModel(id=model_id, provider=self._config.provider_name)
        return list(models.values())

    def analyze(self, context: dict) -> AIResult:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        system_prompt = """You are an environment analysis assistant for AgentState Guard.
Analyze the provided structured environment data and return your analysis in strict JSON format.

Rules:
1. Only use the provided data — do not fabricate facts
2. If the data is insufficient, say so
3. The 'status' field must be one of: ok, warn, attention, error
4. The 'severity' field must be one of: low, medium, high, critical
5. Keep 'summary' under 200 characters
6. 'possible_causes' is a list of strings, maximum 5
7. 'recommended_checks' is a list of strings, maximum 5
8. 'evidence' is a list of key observations from the data, maximum 5
9. Return ONLY valid JSON, no markdown, no commentary

Return format:
{"status":"...","severity":"...","summary":"...","possible_causes":["..."],"recommended_checks":["..."],"evidence":["..."]}
"""
        def failed(code: str) -> AIResult:
            return AIResult(status="error", severity="low", summary=code,
                            model=self.model, provider=self.provider_name,
                            analyzed_at=now, raw_error=code)

        if not self.model.strip():
            return failed("AI_MODEL_REQUIRED")
        try:
            user_content = json.dumps(context, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return failed("AI_CONTEXT_INVALID")
        if len(user_content.encode("utf-8")) > 262_144:
            return failed("AI_CONTEXT_TOO_LARGE")

        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
            "max_tokens": 600,
        }

        url = f"{self._base()}/chat/completions"
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=body, headers=self._headers(), method="POST"
        )
        try:
            data = self._request_json(req)
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or len(content) > 65_536:
                raise AIProviderError("AI_ANALYSIS_INVALID")
            result = json.loads(content)
            if (not isinstance(result, dict)
                    or result.get("status") not in {"ok", "warn", "attention", "error"}
                    or result.get("severity") not in {"low", "medium", "high", "critical"}
                    or not isinstance(result.get("summary"), str)
                    or not result["summary"].strip() or len(result["summary"]) > 200):
                raise AIProviderError("AI_ANALYSIS_INVALID")
            lists = [result.get(name, []) for name in
                     ("possible_causes", "recommended_checks", "evidence")]
            if any(not isinstance(items, list) or len(items) > 5 or
                   any(not isinstance(item, str) or len(item) > 500 for item in items)
                   for items in lists):
                raise AIProviderError("AI_ANALYSIS_INVALID")
            # Even a hostile upstream cannot echo the submitted credential in advice.
            def safe(text: str) -> str:
                return text.replace(self._config.api_key, "[REDACTED]") if self._config.api_key else text

            return AIResult(
                status=result["status"], severity=result["severity"],
                summary=safe(result["summary"]),
                possible_causes=[safe(item) for item in lists[0]],
                recommended_checks=[safe(item) for item in lists[1]],
                evidence=[safe(item) for item in lists[2]],
                model=self.model, provider=self.provider_name, analyzed_at=now,
            )
        except AIProviderError as error:
            return failed(error.code)
        except (ValueError, KeyError, IndexError, TypeError):
            return failed("AI_ANALYSIS_INVALID")

    def assess(self, authority_package: dict):
        """Conservatively adapt legacy analysis to the existing R4 P5 contract."""
        from .supervisor import AIAssessment

        result = self.analyze(authority_package)
        if result.status not in {"ok", "warn", "attention"}:
            raise RuntimeError("AI_ASSESSMENT_UNAVAILABLE")
        if not isinstance(result.summary, str) or not result.summary:
            raise RuntimeError("AI_ASSESSMENT_UNAVAILABLE")
        refs = authority_package.get("evidence_refs")
        if not isinstance(refs, (list, tuple)) or not all(
            isinstance(item, str) and item for item in refs
        ):
            raise RuntimeError("AI_ASSESSMENT_UNAVAILABLE")
        severity = {
            "low": "LOW",
            "medium": "MEDIUM",
            "high": "HIGH",
            "critical": "HIGH",
        }.get(str(result.severity).casefold(), "UNKNOWN")
        return AIAssessment(
            decision="REVIEW",
            severity=severity,
            summary=result.summary,
            evidence_refs=tuple(refs),
            uncertainties=(),
            required_checks=("human_review",),
            requires_checkpoint=authority_package.get("requires_checkpoint") is True,
            requires_manual_approval=True,
        )


# ── Environment context builder ──────────────────────────────


def build_analysis_context(
    health: str = "unknown",
    versions: dict | None = None,
    changes: list | None = None,
    checkpoints: list | None = None,
    sanitized_diff: list | None = None,
    doctor: list | None = None,
) -> dict:
    """Build sanitized, structured context for AI analysis.

    NEVER includes: API keys, tokens, private keys, .env content,
    un-sanitized config, full file contents.
    """
    return {
        "environment": {
            "health": health,
            "versions": versions or {},
        },
        "changes": changes or [],
        "checkpoints": [
            {
                "id": c.get("id"),
                "label": c.get("label"),
                "created_at": c.get("created_at", "")[:19],
            }
            for c in (checkpoints or [])
        ],
        "sanitized_diff": sanitized_diff or [],
        "doctor_summary": [
            {
                "check": d.get("check"),
                "status": d.get("status"),
                "message": d.get("message"),
            }
            for d in (doctor or [])
        ],
    }
