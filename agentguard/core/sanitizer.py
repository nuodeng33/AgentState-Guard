"""Secret sanitization — masks API keys, tokens, passwords before output."""

import re
from typing import Dict, List, Optional, Pattern


DEFAULT_PATTERNS: List[Pattern[str]] = [
    re.compile(r"(api[_-]?key|apikey)\s*[=:]\s*['\"]?(sk-[a-zA-Z0-9]{10,})['\"]?", re.IGNORECASE),
    re.compile(r"(api[_-]?key|apikey)\s*[=:]\s*['\"]?([a-zA-Z0-9_-]{20,})['\"]?", re.IGNORECASE),
    re.compile(r"(token|secret|password|passwd)\s*[=:]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?", re.IGNORECASE),
    re.compile(r"(authorization|auth)\s*[=:]\s*['\"]?(bearer\s+[a-zA-Z0-9_\-\.]+)['\"]?", re.IGNORECASE),
    re.compile(r"(sk-ant-[a-zA-Z0-9]{10,})"),
    re.compile(r"(sk-test-[a-zA-Z0-9_-]{10,})"),
    re.compile(r"(ghp_|gho_|ghu_|ghs_|ghr_)[a-zA-Z0-9]{36,}"),
    re.compile(r"(xox[baprs]-)[a-zA-Z0-9-]{10,}"),
    re.compile(r"-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----"),
    re.compile(r"(api[_-]?key|apikey|apiKey)\s*[=:]\s*['\"]?([a-zA-Z0-9_\-\.]{10,})['\"]?", re.IGNORECASE),
]

MASK = "***MASKED***"
KEY_VALUE_PATTERN = re.compile(r'([\'"]?[a-zA-Z_][a-zA-Z0-9_-]*[\'"]?\s*[=:]\s*)([\'"]?[^\s,;\}\]]{8,}[\'"]?)')


def sanitize_text(text: str, extra_patterns: Optional[List[Pattern[str]]] = None) -> str:
    """Replace secrets in text with mask.

    Applies default patterns then any extra patterns provided.
    """
    result = text
    patterns = DEFAULT_PATTERNS + (extra_patterns or [])

    for pat in patterns:
        result = pat.sub(lambda m: _mask_match(m, pat), result)

    return result


def _mask_match(m: re.Match, pat: Pattern[str]) -> str:
    """Replace matched secret groups with mask, preserving structure."""
    groups = m.groups()
    if not groups:
        return MASK

    # If the pattern has named/value captures, reconstruct with value masked
    # Heuristic: last group is the secret value
    before = groups[-2] if len(groups) >= 2 else ""
    if before and not before.endswith("=") and not before.endswith(":"):
        # key=value pattern
        return f"{before}{MASK}"
    return MASK


def sanitize_dict(d: Dict[str, object], keys_to_mask: Optional[List[str]] = None) -> Dict[str, object]:
    """Return a copy of dict with known secret keys masked."""
    sensitive_keys = set(k.lower() for k in (keys_to_mask or []))
    if not sensitive_keys:
        sensitive_keys = {"api_key", "apikey", "token", "secret", "password",
                          "passwd", "authorization", "auth", "access_token",
                          "refresh_token", "client_secret"}

    result: Dict[str, object] = {}
    for k, v in d.items():
        if k.lower() in sensitive_keys:
            if isinstance(v, str) and v:
                result[k] = MASK
            else:
                result[k] = v
        elif isinstance(v, dict):
            result[k] = sanitize_dict(v, list(sensitive_keys))
        elif isinstance(v, str):
            result[k] = sanitize_text(v)
        else:
            result[k] = v
    return result


def sanitize_json_line(line: str) -> str:
    """Sanitize a single line of text (log line, config line)."""
    return sanitize_text(line)


def sanitize_stderr(stderr: str) -> str:
    """Sanitize stderr output before display."""
    return sanitize_text(stderr)


def sanitize_diff(diff_text: str) -> str:
    """Sanitize diff output — masks secrets in +/- lines."""
    lines = diff_text.split("\n")
    result = []
    for line in lines:
        if line.startswith("+") or line.startswith("-"):
            result.append(line[0] + sanitize_text(line[1:]))
        else:
            result.append(sanitize_text(line))
    return "\n".join(result)


def sanitize_markdown(md: str) -> str:
    """Sanitize Markdown content — masks inline code blocks."""
    return sanitize_text(md)


def contains_sensitive_data(text: str) -> bool:
    """Check if text contains any sensitive data patterns.

    Returns True if any default pattern matches.
    """
    for pat in DEFAULT_PATTERNS:
        if pat.search(text):
            # Verify it's a real match, not false positive on short strings
            m = pat.search(text)
            if m:
                matched = m.group(0)
                # Skip very short matches that are likely false positives
                if len(matched) >= 10:
                    return True
    return False
