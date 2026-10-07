"""Log redaction. Every log line passes through here BEFORE it is stored."""
import re

_PATTERNS = [
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)\S+"), r"\1***"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "***"),
    (re.compile(r"(?i)\b([A-Z0-9_.-]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|STOREPASS|KEYPASS)[A-Z0-9_.-]*\s*[=:]\s*)\S+"), r"\1***"),
    (re.compile(r"(https?://[^/\s:@]+:)[^@\s]+(@)"), r"\1***\2"),
]


class Redactor:
    def __init__(self, secret_values: list[str] | None = None) -> None:
        # longest first so overlapping secrets are fully masked
        self._values = sorted({v for v in (secret_values or []) if v and len(v) >= 4}, key=len, reverse=True)

    def __call__(self, line: str) -> str:
        for v in self._values:
            line = line.replace(v, "***")
        for pat, repl in _PATTERNS:
            line = pat.sub(repl, line)
        return line
