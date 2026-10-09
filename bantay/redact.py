"""PII + secret redactor with consistent <TYPE_N> placeholders.

The placeholder -> original mapping lives only in this process's memory. It is never
written to disk or logged; only placeholder names and counts may leave this module.
"""
from __future__ import annotations
import bisect, contextlib, hashlib, json, logging, os, re, threading
from dataclasses import dataclass, field
from .secrets import SECRET_PATTERNS

log = logging.getLogger("bantay.redact")

def _luhn(s: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", s)]
    if len(d) < 13: return False
    tot = 0
    for i, x in enumerate(reversed(d)):
        if i % 2:
            x *= 2
            if x > 9: x -= 9
        tot += x
    return tot % 10 == 0

PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+63|0)9\d{2}[-\s.]?\d{3}[-\s.]?\d{4}(?!\d)")),
    ("TIN", re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}(?:-\d{3})?(?!\d)")),
    ("SSS", re.compile(r"(?<!\d)\d{2}-\d{7}-\d(?!\d)")),
    ("PHILHEALTH", re.compile(r"(?<!\d)\d{2}-\d{9}-\d(?!\d)")),
    ("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
]
# Default every secret pattern to API_KEY so a newly added pattern never raises KeyError.
SECRET_TYPE = {k: "API_KEY" for k in SECRET_PATTERNS}
SECRET_TYPE.update({"jwt": "TOKEN", "private_key_block": "PRIVATE_KEY", "db_url_password": "DB_URL", "generic_assignment": "SECRET"})
SECRET_KINDS = {"API_KEY", "TOKEN", "SECRET", "PRIVATE_KEY", "DB_URL"}
GLINER_LABELS = ["person", "address", "date of birth", "government id number", "bank account number", "credit card number"]
GLINER_TYPE = {"person": "PERSON", "address": "ADDRESS", "date of birth": "DOB", "government id number": "GOV_ID",
               "bank account number": "BANK_ACCOUNT", "credit card number": "CARD"}
PLACEHOLDER_RE = re.compile(r"<([A-Z_]+)_(\d+)>")
# Words GLiNER tends to tag as people in agent transcripts; never worth a placeholder.
NER_STOPWORDS = {"user", "users", "assistant", "human", "claude", "you", "me", "i", "we", "they", "he", "she",
                 "customer", "developer", "admin", "owner", "agent", "model"}
_CACHE_MAX_CHARS = 64_000_000   # total redacted text kept in the per-block cache

def _chunks(text: str, size: int):
    """Yield (offset, piece) windows of <= size chars, cut at the last newline/space so entities stay whole.
    GLiNER silently truncates input beyond ~384 tokens, so windows must stay small."""
    off, n = 0, len(text)
    while off < n:
        end = min(off + size, n)
        if end < n:
            cut = max(text.rfind("\n", off, end), text.rfind(" ", off, end))
            if cut > off + size // 2: end = cut + 1
        yield off, text[off:end]
        off = end

@contextlib.contextmanager
def _hf_offline():
    """Load from the local Hugging Face cache only: no network once the model is downloaded."""
    keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys: os.environ[k] = "1"
    const = None
    try:
        import huggingface_hub.constants as const
        old = const.HF_HUB_OFFLINE; const.HF_HUB_OFFLINE = True
    except Exception:
        const = None
    try:
        yield
    finally:
        if const is not None: const.HF_HUB_OFFLINE = old
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

@dataclass
class RedactResult:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

def _bounded(original: str) -> str:
    """Regex for a known original that will not match inside a longer word."""
    pre = r"(?<!\w)" if original[:1].isalnum() or original[:1] == "_" else ""
    post = r"(?!\w)" if original[-1:].isalnum() or original[-1:] == "_" else ""
    return pre + re.escape(original) + post

def _non_overlapping(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Drop overlaps, keeping the earliest-starting (then longest) span."""
    spans = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
    out, last = [], -1
    for s in spans:
        if s[0] >= last and s[1] > s[0]: out.append(s); last = s[1]
    return out

class Redactor:
    def __init__(self, use_gliner: bool = True, threshold: float = 0.5, gliner_model: str = "urchade/gliner_multi_pii-v1", chunk: int = 800):
        self.forward: dict[str, str] = {}   # original -> placeholder
        self.reverse: dict[str, str] = {}   # placeholder -> original
        self.counters: dict[str, int] = {}
        self.threshold, self.chunk = threshold, chunk
        self._gliner = None; self.gliner_model = gliner_model; self.use_gliner = use_gliner
        self._gliner_failed = False
        self._lock = threading.RLock()
        self._gl_lock = threading.Lock()
        self._known_re: tuple[int, re.Pattern | None] = (0, None)
        self.name = "regex" + (" + gliner_multi_pii-v1" if use_gliner else "")
        self._cache: dict = {}   # text (short) or blake2b digest (long) -> RedactResult
        self._cache_chars = 0

    def _gl(self):
        if self._gliner is None and self.use_gliner and not self._gliner_failed:
            try:
                from gliner import GLiNER
                try:
                    with _hf_offline():
                        m = GLiNER.from_pretrained(self.gliner_model, map_location="cpu", local_files_only=True)
                except Exception:   # not cached yet: the one-time model download (allowed by the spec)
                    log.warning("GLiNER model %s not cached; downloading once", self.gliner_model)
                    m = GLiNER.from_pretrained(self.gliner_model, map_location="cpu")
                try: m = m.to("cpu")   # MPS has misbehaved on this machine; pin to CPU
                except Exception: pass
                try: m.eval()
                except Exception: pass
                self._gliner = m
            except Exception as e:  # degrade to regex-only rather than failing the daemon
                self._gliner_failed = True
                self.name = "regex (gliner unavailable)"
                log.warning("GLiNER unavailable, redacting with regex only: %s", type(e).__name__)
        return self._gliner

    def warm(self):
        """Load the NER model ahead of the first request."""
        spans = _non_overlapping(spans)
        ner = []
        gl = self._gl()
        if gl is not None: gl.predict_entities("warm up", GLINER_LABELS, threshold=self.threshold)

    def _ph(self, kind: str, original: str) -> str:
        with self._lock:
            if original in self.forward: return self.forward[original]
            self.counters[kind] = self.counters.get(kind, 0) + 1
            ph = f"<{kind}_{self.counters[kind]}>"
            self.forward[original] = ph; self.reverse[ph] = original
            return ph

    def _spans(self, text: str) -> list[tuple[int, int, str]]:
        """Non-overlapping (start, end, TYPE) spans. Regex hits win over overlapping NER hits."""
        spans = []
        for kind, pat in SECRET_PATTERNS.items():
            for m in pat.finditer(text):
                # patterns with a capture group (key = value) redact only the value
                s, e = (m.span(1) if pat.groups and m.group(1) else m.span())
                spans.append((s, e, SECRET_TYPE.get(kind, "API_KEY")))
        for kind, pat in PII_PATTERNS:
            for m in pat.finditer(text):
                if kind == "CARD" and not _luhn(m.group(0)): continue
                spans.append((m.start(), m.end(), kind))
        spans = _non_overlapping(spans)
        ner = []
        gl = self._gl()
        if gl is not None:
            windows = [(off, piece) for off, piece in _chunks(text, self.chunk) if piece.strip()]
            with self._gl_lock:
                preds = gl.inference([p for _, p in windows], GLINER_LABELS, threshold=self.threshold, batch_size=4) if windows else []
            for (off, _), ents in zip(windows, preds):
                for e in ents:
                    kind = GLINER_TYPE.get(e.get("label"), "PII")
                    s, t = off + e["start"], off + e["end"]
                    frag = text[s:t].strip()
                    if not frag or PLACEHOLDER_RE.fullmatch(frag) or frag.lower() in NER_STOPWORDS: continue
                    ner.append((s, t, kind))
        if ner:
            starts = [b for b, _, _ in spans]; extra = []
            for b, e, k in _non_overlapping(ner):
                i = bisect.bisect_left(starts, e)   # regex spans starting before this NER span ends
                if i and spans[i - 1][1] > b: continue   # sorted + non-overlapping: only the previous one can overlap
                extra.append((b, e, k))
            spans = sorted(spans + extra)
        return spans

    def redact(self, text: str) -> RedactResult:
        if not text: return RedactResult(text or "")
        key = text if len(text) <= 512 else hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=20).digest()
        hit = self._cache.get(key)
        if hit is not None: return hit
        counts: dict[str, int] = {}; mapping = {}; parts = []; pos = 0
        for start, end, kind in self._spans(text):
            orig = text[start:end]; ph = self._ph(kind, orig)
            parts.append(text[pos:start]); parts.append(ph); pos = end
            counts[kind] = counts.get(kind, 0) + 1; mapping[ph] = orig
        parts.append(text[pos:])
        res = RedactResult("".join(parts), mapping, counts)
        if len(self._cache) > 5000 or self._cache_chars + len(res.text) > _CACHE_MAX_CHARS:
            self._cache.clear(); self._cache_chars = 0
        self._cache[key] = res; self._cache_chars += len(res.text)
        return res

    def restore(self, text: str, json_escape: bool = False) -> str:
        """Swap placeholders back to originals. json_escape=True for text that sits inside a JSON string."""
        def sub(m):
            orig = self.reverse.get(m.group(0))
            if orig is None: return m.group(0)
            return json.dumps(orig)[1:-1] if json_escape else orig
        return PLACEHOLDER_RE.sub(sub, text or "")

    def reapply(self, text: str) -> str:
        """Replace originals this redactor has already seen with their placeholders (no new detection).
        Used on assistant history, which carries values we restored on the way back."""
        if not text or not self.forward: return text
        with self._lock:
            n, pat = self._known_re
            if pat is None or n != len(self.forward):
                keys = sorted(self.forward, key=len, reverse=True)
                pat = re.compile("|".join(_bounded(k) for k in keys))
                self._known_re = (len(self.forward), pat)
            fwd = dict(self.forward)
        return pat.sub(lambda m: fwd[m.group(0)], text)

class StreamRestorer:
    """Restores placeholders across chunk boundaries; holds back a partial '<...' for up to max_hold chars."""
    def __init__(self, redactor: Redactor, max_hold: int = 24, json_escape: bool = False):
        self.r, self.buf, self.max_hold, self.json_escape = redactor, "", max_hold, json_escape

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        i = self.buf.rfind("<")
        if i == -1 or ">" in self.buf[i:] or len(self.buf) - i > self.max_hold or not _could_be_placeholder(self.buf[i:]):
            out, self.buf = self.r.restore(self.buf, self.json_escape), ""
            return out
        out, self.buf = self.r.restore(self.buf[:i], self.json_escape), self.buf[i:]
        return out

    def flush(self) -> str:
        out, self.buf = self.r.restore(self.buf, self.json_escape), ""
        return out

_PARTIAL_RE = re.compile(r"<[A-Z_]*\d*")

def _could_be_placeholder(tail: str) -> bool:
    """True if tail (starting at '<') is a prefix of some <TYPE_N> placeholder."""
    return _PARTIAL_RE.fullmatch(tail) is not None
