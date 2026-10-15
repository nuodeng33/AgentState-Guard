"""One exact-match signature source for process and installed application metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .agents import (
    AgentAdapterRegistry,
    AgentRole,
    AgentSignatureRule,
    ProcessFact,
    classify_executable,
    classify_process,
)
from .agents.classifier import package_name_from_entry


def _rule(
    kind: str,
    role: AgentRole,
    basenames: tuple[str, ...],
    *,
    packages: tuple[str, ...] = (),
) -> tuple[str, AgentSignatureRule]:
    suffix = "package" if packages else "executable"
    return kind, AgentSignatureRule(
        rule_id=f"product-{kind.casefold()}-{suffix}",
        role=role,
        executable_basenames=basenames,
        package_names=packages,
        package_only=bool(packages),
        signature_evidence_ref=f"signature:product:{kind.casefold()}:{suffix}",
    )


PRODUCT_SIGNATURES: tuple[tuple[str, AgentSignatureRule], ...] = (
    _rule("CODEX", AgentRole.EXECUTION_AGENT, ("node", "node.exe"),
          packages=("@openai/codex",)),
    _rule("KIMI_CODE", AgentRole.EXECUTION_AGENT, ("node", "node.exe"),
          packages=("@moonshot-ai/kimi-code", "kimi-code")),
    _rule("CLAUDE", AgentRole.EXECUTION_AGENT, ("node", "node.exe"),
          packages=("@anthropic-ai/claude-code",)),
    _rule("CLOUDCLI", AgentRole.AGENT_HOST, ("node", "node.exe"),
          packages=("@cloudcli-ai/cloudcli", "cloudcli")),
    _rule("CCR", AgentRole.MODEL_ROUTER, ("node", "node.exe"),
          packages=("@musistudio/claude-code-router",)),
    _rule("ZCODE", AgentRole.AGENT_HOST, ("zcode", "zcode.exe")),
    _rule("CODEX", AgentRole.EXECUTION_AGENT, ("codex", "codex.exe")),
    _rule("KIMI_CODE", AgentRole.EXECUTION_AGENT,
          ("kimi", "kimi.exe", "kimi-code", "kimi-code.exe")),
    _rule("CLAUDE", AgentRole.EXECUTION_AGENT, ("claude", "claude.exe")),
    _rule("CLOUDCLI", AgentRole.AGENT_HOST, ("cloudcli", "cloudcli.exe")),
    _rule("CCR", AgentRole.MODEL_ROUTER, ("ccr", "ccr.exe")),
)
PRODUCT_RULES = tuple(rule for _, rule in PRODUCT_SIGNATURES)
_KIND_BY_REF = {rule.signature_evidence_ref: kind for kind, rule in PRODUCT_SIGNATURES}
COMMAND_NAMES = tuple(dict.fromkeys(
    basename for _, rule in PRODUCT_SIGNATURES if not rule.package_names
    for basename in rule.executable_basenames if "." not in basename
))


def classify_metadata(
    record: Mapping[str, Any], domain_id: str
) -> tuple[str, AgentRole] | None:
    """Recognize known programs from structured facts, never arbitrary interpreters."""
    path = record.get("path")
    basenames = tuple(dict.fromkeys(
        value for value in (
            record.get("executable_basename"),
            record.get("name"),
            path.replace("\\", "/").rsplit("/", 1)[-1] if isinstance(path, str) else None,
        ) if isinstance(value, str) and value
    ))
    if not basenames:
        return None
    package_name = record.get("package_name")
    if not isinstance(package_name, str) or not package_name:
        entry = record.get("entry_path")
        package_name = package_name_from_entry(entry if isinstance(entry, str) else None)
    raw_facts = record.get("fixed_facts")
    fixed_facts = (
        {str(key): value for key, value in raw_facts.items() if isinstance(value, bool)}
        if isinstance(raw_facts, Mapping) else {}
    )
    for basename in basenames:
        for kind, rule in PRODUCT_SIGNATURES:
            classification = classify_executable(
                executable_basename=basename,
                execution_domain_id=domain_id,
                evidence_refs=(),
                rules=(rule,),
                package_name=package_name,
                fixed_facts=fixed_facts,
            )
            if classification.role not in {AgentRole.UNKNOWN, AgentRole.TOOL_PROCESS}:
                return kind, classification.role
    return None


def kind_for_classification(evidence_refs: tuple[str, ...]) -> str | None:
    return next((_KIND_BY_REF[ref] for ref in evidence_refs if ref in _KIND_BY_REF), None)


class _ProductSignatureAdapter:
    adapter_id = "product-exact-signatures"

    def discover(self, facts: tuple[ProcessFact, ...]):
        return tuple(
            classification
            for fact in facts
            if (classification := classify_process(fact, PRODUCT_RULES)).role
            not in {AgentRole.UNKNOWN, AgentRole.TOOL_PROCESS}
        )


PRODUCT_AGENT_REGISTRY = AgentAdapterRegistry((_ProductSignatureAdapter(),))


__all__ = [
    "COMMAND_NAMES", "PRODUCT_AGENT_REGISTRY", "PRODUCT_RULES", "PRODUCT_SIGNATURES",
    "classify_metadata", "kind_for_classification", "package_name_from_entry",
]
