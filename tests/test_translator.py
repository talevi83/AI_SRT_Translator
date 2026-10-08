import json
import os
import threading
from types import SimpleNamespace

import pytest

import translator
from srt_utils import parse_srt, parse_srt_text, split_srt_file
from translator import Cancelled, Item, TranslationEngine, build_system_prompt, looks_translated, translate_directory


class FakeModels:
    """Stands in for client.models: answers with a scripted function of the request payload."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []
        self.lock = threading.Lock()

    def generate_content(self, model, contents, config):
        payload = json.loads(contents)
        with self.lock:
            self.calls.append(payload)
            n = len(self.calls)
        items = self.answer(payload, n)
        usage = SimpleNamespace(prompt_token_count=1000, candidates_token_count=500, thoughts_token_count=100)
        return SimpleNamespace(parsed=[Item(**x) for x in items], text=json.dumps(items), usage_metadata=usage)


def make_engine(answer, **kw):
    engine = TranslationEngine(api_key="test-key", **kw)
    fake = FakeModels(answer)
    engine.standard_client = SimpleNamespace(models=fake)
    return engine, fake


def hebrew(payload, n):
    return [{"id": x["id"], "text": "תרגום " + x["text"]} for x in payload["subtitles"]]


SRT = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},500\nLine {i}\n\n" for i in range(1, 8))


def test_only_text_is_sent_and_structure_is_kept():
    engine, fake = make_engine(hebrew)
    blocks = parse_srt_text(SRT)
    out, warnings = engine.translate_blocks(blocks)
    assert [b.timestamp for b in out] == [b.timestamp for b in blocks]
    assert out[0].text == "תרגום Line 1" and not warnings
    sent = fake.calls[0]["subtitles"][0]
    assert set(sent) == {"id", "text"} and "-->" not in json.dumps(fake.calls[0])


def test_missing_items_are_retried_alone(monkeypatch):
    monkeypatch.setattr(translator, "ERROR_DELAY", 0)

    def drop_third(payload, n):
        items = hebrew(payload, n)
        return [x for x in items if x["id"] != "3"] if n == 1 else items

    engine, fake = make_engine(drop_third)
    out, _ = engine.translate_blocks(parse_srt_text(SRT))
    assert len(fake.calls) == 2
    assert [x["id"] for x in fake.calls[1]["subtitles"]] == ["3"]
    assert out[2].text == "תרגום Line 3"


def test_untranslated_items_are_retried():
    def english_first(payload, n):
        return payload["subtitles"] if n == 1 else hebrew(payload, n)

    engine, fake = make_engine(english_first)
    out, _ = engine.translate_blocks(parse_srt_text(SRT))
    assert len(fake.calls) == 2 and all("תרגום" in b.text for b in out)


def test_gives_up_after_max_attempts():
    engine, fake = make_engine(lambda p, n: [])
    with pytest.raises(RuntimeError, match="no translation"):
        engine.translate_blocks(parse_srt_text(SRT))
    assert len(fake.calls) == translator.MAX_ATTEMPTS


def test_empty_blocks_are_not_sent():
    engine, fake = make_engine(hebrew)
    blocks = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\nHi\n")
    out, _ = engine.translate_blocks(blocks)
    assert [x["text"] for x in fake.calls[0]["subtitles"]] == ["Hi"]
    assert out[0].text == "" and out[1].text == "תרגום Hi"


def test_usage_and_cost():
    engine, _ = make_engine(hebrew, model="gemini-3.1-flash-lite")
    engine.translate_blocks(parse_srt_text(SRT))
    price = translator.MODELS["gemini-3.1-flash-lite"]
    assert engine.usage["calls"] == 1
    assert engine.usage["cost"] == pytest.approx((1000 * price["input"] + 600 * price["output"]) / 1e6)


def test_rate_limit_backoff(monkeypatch):
    monkeypatch.setattr(translator, "RATE_LIMIT_BACKOFF", (0, 0))
    engine, fake = make_engine(hebrew)
    real = fake.generate_content
    errors = iter([RuntimeError("429 RESOURCE_EXHAUSTED")])

    def flaky(**kw):
        err = next(errors, None)
        if err:
            raise err
        return real(**kw)

    engine.standard_client = SimpleNamespace(models=SimpleNamespace(generate_content=flaky))
    out, _ = engine.translate_blocks(parse_srt_text(SRT))
    assert out[0].text.startswith("תרגום")


def _split(tmp_path, chunk=3):
    src = tmp_path / "Show.srt"
    src.write_text(SRT, encoding="utf-8")
    split_dir, merge_dir = tmp_path / "split", tmp_path / "merge"
    split_dir.mkdir()
    split_srt_file(str(src), str(split_dir), chunk)
    return str(split_dir), str(merge_dir)


def test_directory_context_and_resume(tmp_path):
    split_dir, merge_dir = _split(tmp_path)
    engine, fake = make_engine(hebrew)
    ok, _ = translate_directory(split_dir, merge_dir, engine, workers=2)
    assert ok and len(fake.calls) == 3
    middle = next(c for c in fake.calls if c["subtitles"][0]["text"] == "Line 4")
    assert middle["context_before"] == ["Line 1", "Line 2", "Line 3"]
    assert middle["context_after"] == ["Line 7"]

    # Second run: everything is reused
    engine2, fake2 = make_engine(hebrew)
    ok, _ = translate_directory(split_dir, merge_dir, engine2)
    assert ok and fake2.calls == []

    # A broken part is retranslated alone
    broken = os.path.join(merge_dir, "Show_part_2.srt")
    blocks = parse_srt(broken)
    with open(broken, "w", encoding="utf-8") as f:
        f.write(f"{blocks[0].index}\n{blocks[0].timestamp}\n{blocks[0].text}\n\n")
    engine3, fake3 = make_engine(hebrew)
    ok, _ = translate_directory(split_dir, merge_dir, engine3)
    assert ok and len(fake3.calls) == 1


def test_changed_options_retranslate_everything(tmp_path):
    split_dir, merge_dir = _split(tmp_path)
    translate_directory(split_dir, merge_dir, make_engine(hebrew)[0])
    engine, fake = make_engine(hebrew, instructions="Use slang")
    translate_directory(split_dir, merge_dir, engine)
    assert len(fake.calls) == 3


def test_stale_parts_are_removed(tmp_path):
    split_dir, merge_dir = _split(tmp_path, chunk=2)  # 4 parts
    translate_directory(split_dir, merge_dir, make_engine(hebrew)[0])
    for f in os.listdir(split_dir):
        os.remove(os.path.join(split_dir, f))
    split_srt_file(str(tmp_path / "Show.srt"), split_dir, 4)  # now 2 parts
    translate_directory(split_dir, merge_dir, make_engine(hebrew)[0])
    assert sorted(f for f in os.listdir(merge_dir) if f.endswith(".srt")) == ["Show_part_1.srt", "Show_part_2.srt"]


def test_cancel(tmp_path):
    split_dir, merge_dir = _split(tmp_path)
    cancel = threading.Event()
    cancel.set()
    engine, fake = make_engine(hebrew, cancel_event=cancel)
    with pytest.raises(Cancelled):
        translate_directory(split_dir, merge_dir, engine)
    assert fake.calls == []


def test_prompt_and_detection():
    prompt = build_system_prompt("en", "fr", convert_units=False, glossary="Walter = Walter")
    assert "into French" in prompt and "metric" not in prompt and "Walter = Walter" in prompt
    assert "Israeli Hebrew" in build_system_prompt(target_lang="he")
    assert not looks_translated("Hello there", "Hello there", "he")
    assert looks_translated("Hello there", "שלום", "he")
    assert looks_translated("♪ ♪", "♪ ♪", "he")
    assert looks_translated("Hello", "Bonjour", "fr")  # Latin targets can't be checked
