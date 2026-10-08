import json
from types import SimpleNamespace

import qa
from srt_utils import RLM, parse_srt, parse_srt_text, write_srt
from translator import Item, TranslationEngine, looks_translated

SOURCE = """1
00:00:01,000 --> 00:00:04,000
<i>Hello there.</i>

2
00:00:05,000 --> 00:00:08,000
How are you?

3
00:00:09,000 --> 00:00:09,500
Run!

4
00:00:10,000 --> 00:00:14,000
Fine.

"""

TRANSLATED = """1
00:00:01,000 --> 00:00:04,000
שלום לך.

2
00:00:05,000 --> 00:00:08,000
How are you?

3
00:00:09,000 --> 00:00:09,500
רוץ מהר ככל שאתה יכול!

4
00:00:10,000 --> 00:00:14,000
‏שורה אחת‏
שורה שתיים שהיא באמת ארוכה מאוד מעבר למגבלה
שורה שלוש

"""


def kinds(issues):
    return {(i["index"], i["kind"]) for i in issues}


def test_check_finds_each_issue_kind():
    issues = qa.check(parse_srt_text(SOURCE), parse_srt_text(TRANSLATED), "he", looks_translated)
    assert kinds(issues) == {("1", "tags"), ("2", "untranslated"), ("3", "fast"), ("4", "lines"), ("4", "long")}
    assert qa.summarize(issues) == {"untranslated": 1, "tags": 1, "lines": 1, "long": 1, "fast": 1}


def test_clean_file_has_no_issues():
    clean = SOURCE.replace("<i>Hello there.</i>", "<i>שלום.</i>").replace("How are you?", "מה שלומך?") \
        .replace("Run!", "רוץ!").replace("Fine.", f"{RLM}בסדר.{RLM}")
    assert qa.check(parse_srt_text(SOURCE), parse_srt_text(clean), "he", looks_translated) == []


def test_fix_replaces_only_flagged_blocks(tmp_path):
    src, final = tmp_path / "s.srt", tmp_path / "s.he.srt"
    src.write_text(SOURCE, encoding="utf-8")
    final.write_text(TRANSLATED, encoding="utf-8")
    sent = []

    def generate_content(model, contents, config):
        payload = json.loads(contents)
        sent.append(payload)
        assert "failed a quality check" in config.system_instruction
        items = [Item(id=x["id"], text="<i>מתוקן</i>" if "<i>" in x["text"] else "מתוקן") for x in payload["subtitles"]]
        return SimpleNamespace(parsed=items, usage_metadata=None)

    engine = TranslationEngine(api_key="k")
    engine.standard_client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    issues = qa.check(parse_srt(str(src)), parse_srt(str(final)), "he", looks_translated)
    replaced = qa.fix_issues(engine, str(src), str(final), issues, rtl_fix=True)

    assert replaced == 3  # blocks 1, 2 and 4 - block 3 only reads fast, which isn't auto-fixed
    assert [x["text"] for x in sent[0]["subtitles"]] == ["<i>Hello there.</i>", "How are you?", "Fine."]
    out = parse_srt(str(final))
    assert out[0].text == f"{RLM}<i>מתוקן</i>{RLM}" and out[2].text == "רוץ מהר ככל שאתה יכול!"
    assert [b.timestamp for b in out] == [b.timestamp for b in parse_srt(str(src))]
