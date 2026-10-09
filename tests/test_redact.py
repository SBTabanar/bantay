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

@pytest.mark.slow
def test_gliner_finds_names():
    r = Redactor(use_gliner=True)
    out = r.redact("Please refund Juan Dela Cruz, he lives at 12 Mabini St, Kalibo, Aklan.")
    assert "Juan Dela Cruz" not in out.text and "<PERSON_1>" in out.text
