"""OpenAI-compatible AI provider — stateless, in-memory API key only.

Never persists API keys to disk. Never sends unsanitized config.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


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
    api_key: str = ""
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
    possible_causes: List[str] = field(default_factory=list)
    recommended_checks: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
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

PROVIDER_PRESETS: Dict[str, dict] = {
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


class AIProvider(ABC):
    @abstractmethod
    def test_connection(self) -> dict: ...

    @abstractmethod
    def list_models(self) -> List[AIModel]: ...

    @abstractmethod
    def analyze(self, context: dict) -> AIResult: ...


class OpenAICompatibleProvider(AIProvider):
    """Standard OpenAI-compatible chat completions provider.

    API Key is held in memory only. Never persisted to disk.
    Context is sanitized before sending.
    """

    def __init__(self, config: ProviderConfig, timeout: int = 30):
        self._config = config
        self._timeout = timeout

    @property
    def model(self) -> str:
        """Expose the configured model for the existing R4 assessment cache key."""
        return self._config.model

    def _base(self) -> str:
        return self._config.base_url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
        }

    def test_connection(self) -> dict:
        url = f"{self._base()}/models"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        start = time.monotonic()
        try:
            resp = urllib.request.urlopen(req, timeout=self._timeout)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            data = json.loads(resp.read())
            model_count = len(data.get("data", []))
            return {
                "ok": True,
                "latency_ms": elapsed_ms,
                "models_available": model_count,
                "message": f"Connected ({model_count} models, {elapsed_ms}ms)",
            }
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            return {"ok": False, "error": f"HTTP {e.code}", "detail": body[:200]}
        except urllib.error.URLError as e:
            return {"ok": False, "error": "Connection failed", "detail": str(e.reason)}
        except Exception as e:
            return {"ok": False, "error": str(type(e).__name__), "detail": str(e)[:200]}

    def list_models(self) -> List[AIModel]:
        url = f"{self._base()}/models"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            resp = urllib.request.urlopen(req, timeout=self._timeout)
            data = json.loads(resp.read())
            models = []
            for m in data.get("data", []):
                mid = m.get("id", "")
                if mid:
                    models.append(AIModel(id=mid, provider=self._config.provider_name))
            return models
        except Exception:
            return []

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
        user_content = json.dumps(context, default=str, ensure_ascii=False)

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
        req = urllib.request.Request(url, data=body,
                                      headers=self._headers(), method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self._timeout)
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)
            return AIResult(
                status=result.get("status", "error"),
                severity=result.get("severity", "low"),
                summary=result.get("summary", "Analysis produced no summary"),
                possible_causes=result.get("possible_causes", []),
                recommended_checks=result.get("recommended_checks", []),
                evidence=result.get("evidence", []),
                model=self._config.model,
                provider=self._config.provider_name,
                analyzed_at=now,
            )
        except (json.JSONDecodeError, KeyError) as e:
            return AIResult(
                status="error", severity="low",
                summary=f"AI response could not be parsed: {e}",
                analyzed_at=now,
                raw_error=str(e),
            )
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            return AIResult(
                status="error", severity="low",
                summary=f"API error (HTTP {e.code})",
                analyzed_at=now,
                raw_error=body[:200],
            )
        except Exception as e:
            return AIResult(
                status="error", severity="low",
                summary=f"AI request failed: {e}",
                analyzed_at=now,
                raw_error=str(e)[:200],
            )

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
    versions: dict = None,
    changes: list = None,
    checkpoints: list = None,
    sanitized_diff: list = None,
    doctor: list = None,
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
            {"id": c.get("id"), "label": c.get("label"),
             "created_at": c.get("created_at", "")[:19]}
            for c in (checkpoints or [])
        ],
        "sanitized_diff": sanitized_diff or [],
        "doctor_summary": [
            {"check": d.get("check"), "status": d.get("status"),
             "message": d.get("message")}
            for d in (doctor or [])
        ],
    }
