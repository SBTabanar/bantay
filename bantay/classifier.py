from __future__ import annotations
import copy, threading
import mlx.core as mx
from mlx_lm.models.cache import make_prompt_cache
from .types import ToolCall, LABELS
from . import prompts

DEFAULT_MODEL = "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"

class Classifier:
    """One forward pass → logits over allow/ask/block. Reason generated only when asked."""
    def __init__(self, model_path: str = DEFAULT_MODEL, adapter_path: str | None = None):
        from mlx_lm import load
        self.model, self.tokenizer = load(model_path, adapter_path=adapter_path)
        self.name = model_path.split("/")[-1] + (f" + {adapter_path.rstrip('/').split('/')[-1]}" if adapter_path else "")
        self.label_ids = {}
        for lab in LABELS:
            ids = self.tokenizer.encode(lab, add_special_tokens=False)
            self.label_ids[lab] = ids[0]
        assert len(set(self.label_ids.values())) == 3, self.label_ids
        self._lock = threading.Lock()
        # KV cache over the system-prompt tokens every call shares; classify() prefills only the suffix.
        sys_text = self.tokenizer.apply_chat_template([{"role": "system", "content": prompts.SYSTEM}], tokenize=False)
        self._prefix_ids = self.tokenizer.encode(sys_text, add_special_tokens=False)
        self._prefix_len = len(self._prefix_ids)
        self._prefix_cache = make_prompt_cache(self.model)
        self.model(mx.array([self._prefix_ids]), cache=self._prefix_cache)
        mx.eval([c.state for c in self._prefix_cache])
        warm = ToolCall("Bash", {"command": "ls"})
        _, cached = self.classify(warm)  # warm-up / compile
        full = self._full_probs(warm)
        assert all(abs(cached[l] - full[l]) < 0.05 for l in LABELS), (cached, full)

    def _prompt_ids(self, call: ToolCall, suffix: str = "") -> list[int]:
        text = self.tokenizer.apply_chat_template(prompts.messages_for(call), add_generation_prompt=True, tokenize=False)
        return self.tokenizer.encode(text + suffix, add_special_tokens=False)

    def _label_probs(self, ids: list[int], cache=None) -> dict[str, float]:
        logits = self.model(mx.array([ids]), cache=cache)[0, -1]
        sel = mx.array([logits[self.label_ids[l]] for l in LABELS])
        probs = mx.softmax(sel.astype(mx.float32))
        mx.eval(probs)
        return {l: float(probs[i]) for i, l in enumerate(LABELS)}

    def _full_probs(self, call: ToolCall) -> dict[str, float]:
        """Uncached full forward pass; reference for the prefix-cache path."""
        with self._lock:
            return self._label_probs(self._prompt_ids(call))

    def classify(self, call: ToolCall) -> tuple[str, dict[str, float]]:
        ids = self._prompt_ids(call)
        with self._lock:
            if ids[:self._prefix_len] == self._prefix_ids and len(ids) > self._prefix_len:
                p = self._label_probs(ids[self._prefix_len:], cache=copy.deepcopy(self._prefix_cache))
            else:  # template did not start with the cached prefix; stay correct
                p = self._label_probs(ids)
        return max(p, key=p.get), p

    def reason(self, call: ToolCall, label: str, max_tokens: int = 40) -> str:
        from mlx_lm import generate
        text = self.tokenizer.apply_chat_template(prompts.messages_for(call), add_generation_prompt=True, tokenize=False)
        with self._lock:
            out = generate(self.model, self.tokenizer, prompt=text + f"{label}\nReason:", max_tokens=max_tokens, verbose=False)
        return out.strip().split("\n")[0].strip() or f"{label} by local model"

class FakeClassifier:
    def __init__(self, label: str = "allow", probs: dict | None = None, reason_text: str = "fake reason"):
        self.label, self.name, self.reason_text = label, "fake", reason_text
        self.probs = probs or {l: (0.8 if l == label else 0.1) for l in LABELS}
        self.calls: list[ToolCall] = []
    def classify(self, call):
        self.calls.append(call); return self.label, dict(self.probs)
    def reason(self, call, label):
        return self.reason_text
