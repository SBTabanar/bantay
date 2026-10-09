import time, pytest
from bantay.redact import Redactor, StreamRestorer

REC = "Customer: Maria Clara Santos, email maria.santos@gmail.com, GCash 0917-555-1234, TIN 123-456-789-000. Key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"

def test_regex_redaction_without_gliner():
    r = Redactor(use_gliner=False)
    out = r.redact(REC)
    assert "maria.santos@gmail.com" not in out.text and "<EMAIL_1>" in out.text
    assert "0917-555-1234" not in out.text and "<PHONE_1>" in out.text
    assert "123-456-789-000" not in out.text and "<TIN_1>" in out.text
    assert "sk-ant-" not in out.text and "<API_KEY_1>" in out.text
    assert out.counts["EMAIL"] == 1 and out.counts["API_KEY"] == 1
    assert r.restore(out.text) == REC

def test_same_value_same_placeholder_across_calls():
    r = Redactor(use_gliner=False)
    a = r.redact("mail x@y.com now").text; b = r.redact("again x@y.com and z@w.com").text
    assert "<EMAIL_1>" in a and "<EMAIL_1>" in b and "<EMAIL_2>" in b

def test_large_input_bounded_time():
    r = Redactor(use_gliner=False)
    big = ("line with a@b.com and 0917 123 4567 and filler text " * 4000)  # ~200KB
    t0 = time.time(); out = r.redact(big); dt = time.time() - t0
    assert dt < 3 and out.text.count("<EMAIL_1>") == 4000 and len(out.text) > 100_000

def test_stream_restorer_handles_split_placeholder():
    r = Redactor(use_gliner=False); red = r.redact("call 0917-555-1234")
    s = StreamRestorer(r)
    out = s.feed("Dial <PHO") + s.feed("NE_1> now") + s.flush()
    assert out == "Dial 0917-555-1234 now"

def test_stream_restorer_passes_through_lt():
    r = Redactor(use_gliner=False); s = StreamRestorer(r)
    assert s.feed("if a < b and c > d") + s.flush() == "if a < b and c > d"

def test_fixture_records_all_redacted_and_restored():
    from pathlib import Path
    text = (Path(__file__).parent / "fixtures" / "ph_records.txt").read_text()
    r = Redactor(use_gliner=False)
    out = r.redact(text)
    assert "@example.ph" not in out.text and out.counts["EMAIL"] == 50
    assert out.counts["PHONE"] >= 50 and out.counts["TIN"] >= 1 and out.counts["SSS"] >= 1
    assert out.counts["PHILHEALTH"] >= 1 and out.counts["API_KEY"] >= 1
    assert r.restore(out.text) == text

def test_assignment_secret_keeps_key_name():
    r = Redactor(use_gliner=False)
    out = r.redact("password = hunter2hunter2hunter2xx")
    assert out.text == "password = <SECRET_1>" and r.restore(out.text) == "password = hunter2hunter2hunter2xx"

def test_gliner_unavailable_degrades_to_regex(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "gliner", None)  # import gliner -> ImportError
    r = Redactor(use_gliner=True)
    out = r.redact("mail x@y.com")
    assert out.text == "mail <EMAIL_1>" and "unavailable" in r.name

class _FakeNER:
    """Stands in for GLiNER: tags given substrings as the given label."""
    def __init__(self, tags): self.tags = tags
    def inference(self, texts, labels, threshold=0.5, batch_size=4):
        out = []
        for t in texts:
            ents = []
            for sub, label in self.tags:
                i = t.find(sub)
                if i >= 0: ents.append({"start": i, "end": i + len(sub), "label": label, "text": sub})
            out.append(ents)
        return out

def _with_fake_ner(tags):
    r = Redactor(use_gliner=True); r._gliner = _FakeNER(tags); return r

def test_regex_span_beats_overlapping_ner_span():
    r = _with_fake_ner([("hi juan@x.ph", "person")])
    assert r.redact("reply with exactly: hi juan@x.ph").text == "reply with exactly: hi <EMAIL_1>"

def test_ner_role_words_are_not_people():
    r = _with_fake_ner([("user", "person"), ("Juan Dela Cruz", "person")])
    assert r.redact("The user asked Juan Dela Cruz").text == "The user asked <PERSON_1>"

@pytest.mark.slow
def test_gliner_finds_names():
    r = Redactor(use_gliner=True)
    out = r.redact("Please refund Juan Dela Cruz, he lives at 12 Mabini St, Kalibo, Aklan.")
    assert "Juan Dela Cruz" not in out.text and "<PERSON_1>" in out.text

def test_reapply_respects_word_boundaries_and_large_texts_are_cached():
    r = _with_fake_ner([("Ana", "person")])
    r.redact("ask Ana")
    assert r.reapply("Ana likes Banana") == "<PERSON_1> likes Banana"
    big = "Ana " + "x" * 120_000
    first = r.redact(big)
    assert r.redact(big) is first
