import json
import os
from types import SimpleNamespace

import pytest

import app
import translator
from translator import Item

SRT = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\nLine {i}\n\n" for i in range(1, 6))


class FakeModels:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on

    def generate_content(self, model, contents, config):
        if config.response_schema is translator.Analysis:
            return SimpleNamespace(parsed=translator.Analysis(characters=[], terms=[], tone="-"), usage_metadata=None)
        payload = json.loads(contents)
        if self.fail_on and any(self.fail_on in x["text"] for x in payload["subtitles"]):
            raise RuntimeError("boom")
        items = [Item(id=x["id"], text="שורה " + x["text"]) for x in payload["subtitles"]]
        usage = SimpleNamespace(prompt_token_count=100, candidates_token_count=50, thoughts_token_count=0)
        return SimpleNamespace(parsed=items, usage_metadata=usage)


@pytest.fixture
def api(tmp_path, monkeypatch):
    env = {}
    monkeypatch.setattr(app, "read_env", lambda: dict(env))
    monkeypatch.setattr(app, "write_env", lambda k, v: env.__setitem__(k, v))
    monkeypatch.setattr(app, "INSTRUCTIONS_FILE", str(tmp_path / "instructions.txt"))
    monkeypatch.setattr(translator, "ERROR_DELAY", 0)
    fake = FakeModels()
    monkeypatch.setattr(translator.genai, "Client", lambda **kw: SimpleNamespace(models=fake))
    a = app.Api()
    a.env, a.fake = env, fake
    return a


def make_season(folder, names=("S01E01.srt", "S01E02.srt", "S01E03.srt")):
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_text(SRT.replace("Line", name[:6]), encoding="utf-8")
    (folder / "S01E00.he.srt").write_text(SRT, encoding="utf-8")       # an earlier output - skipped
    (folder / "old_translated.srt").write_text(SRT, encoding="utf-8")  # old naming - skipped
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    return folder


def test_folder_expands_to_source_files(api, tmp_path):
    season = make_season(tmp_path / "season")
    res = api.set_files([str(season)])
    assert [q["name"] for q in res["queue"]] == ["S01E01.srt", "S01E02.srt", "S01E03.srt"]
    assert res["file"]["name"] == "S01E01.srt"


def test_single_file_and_empty_folder(api, tmp_path):
    season = make_season(tmp_path / "season", names=("A.srt",))
    assert api.set_files([str(season)])["queue"] == []  # one file -> plain single-file mode
    (tmp_path / "empty").mkdir()
    assert api.set_files([str(tmp_path / "empty")]) == {"error": "no_srt_in_folder"}


def test_each_file_gets_its_own_work_folders(api, tmp_path):
    season = make_season(tmp_path / "season")
    a, b = str(season / "S01E01.srt"), str(season / "S01E02.srt")
    assert api._dirs(a)["merge"] != api._dirs(b)["merge"]
    assert api._dirs(a)["output"] == api._dirs(b)["output"]
    api.env["CUSTOM_OUTPUT_DIR"] = "."
    assert api._dirs(a)["output"] == str(season)
    assert api._final_path(a) == str(season / "S01E01.he.srt")


def test_batch_translates_every_file_and_continues_after_a_failure(api, tmp_path):
    season = make_season(tmp_path / "season")
    api.set_files([str(season)])
    api.fake.fail_on = "S01E02"
    result = api._do_batch(2, "key")
    assert result == {"failed": ["S01E02.srt"]}
    statuses = {q["name"]: q["status"] for q in api._queue_info()}
    assert statuses == {"S01E01.srt": "done", "S01E02.srt": "failed", "S01E03.srt": "done"}
    out = app.parse_srt(str(season / "translated_file" / "S01E03.he.srt"))
    assert out[0].text.strip("\u200f") == "שורה S01E03 1"
    assert api._usage_base["calls"] > 0

    # Running again retries only the failed file
    api.fake.fail_on = None
    api._usage_base = {}
    assert api._do_batch(2, "key") == {"batch": 3}
    assert api._usage_base["calls"] == 3  # S01E02 only: 3 parts of 2 blocks, glossary skipped (no usage)


def test_remove_from_queue(api, tmp_path):
    season = make_season(tmp_path / "season")
    api.set_files([str(season)])
    res = api.remove_queue_item(str(season / "S01E01.srt"))
    assert [q["name"] for q in res["queue"]] == ["S01E02.srt", "S01E03.srt"]
    assert res["file"]["name"] == "S01E02.srt"
    res = api.remove_queue_item(str(season / "S01E02.srt"))
    assert res["queue"] == [] and res["file"]["name"] == "S01E03.srt"
