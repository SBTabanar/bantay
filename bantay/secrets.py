import re

SECRET_PATTERNS: dict[str, re.Pattern] = {k: re.compile(p) for k, p in {
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
    "anthropic_key": r"\bsk-ant-[A-Za-z0-9_\-]{20,}",
    "openai_key": r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}",
    "github_token": r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
    "slack_token": r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b",
    "google_api_key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "stripe_key": r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{20,}\b",
    "supabase_key": r"\bsb_(?:secret|publishable)_[A-Za-z0-9_\-]{20,}\b",
    "jwt": r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b",
    "private_key_block": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
    "db_url_password": r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:\s/]+:[^@\s]+@",
    "generic_assignment": r"(?i)\b(?:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token|password|passwd|client[_\-]?secret|service[_\-]?role[_\-]?key|private[_\-]?key)\b\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{16,})['\"]?",
}.items()}

SECRET_FILE_RE = re.compile(
    r"(?:(?:^|[\s/\"'=])\.env(?!\.?(?:example|sample|template|test)\b)(?:\.[A-Za-z0-9_\-]+)*(?=$|[\s\"'|;&:)<>])"
    r"|\.pem\b|\.p12\b|\.pfx\b|\bid_(?:rsa|dsa|ecdsa|ed25519)\b|/\.ssh/|\.aws/credentials|\.netrc\b|\.npmrc\b|\.pypirc\b"
    r"|\.git-credentials\b|\.docker/config\.json|/\.kube/config|Library/Keychains|\.(?:bash|zsh)_history\b"
    r"|\.claude/\.credentials\.json|\.config/gh/hosts\.yml|/secrets?\.(?:ya?ml|json)\b|service[_\-]?account[^\s]*\.json)"
)

def find_secrets(text: str) -> list[tuple[str, str]]:
    out = []
    for kind, pat in SECRET_PATTERNS.items():
        for m in pat.finditer(text or ""):
            out.append((kind, m.group(0)))
    return out
