import os

import pytest

from srt_utils import (RLM, compare_structure, decode_srt_bytes, extract_part_number, fix_rtl_line,
                       fix_rtl_text, list_srt_parts, merge_srt_files, parse_srt, parse_srt_text,
                       split_srt_file, validate_translation)

BASIC = """1
00:00:01,000 --> 00:00:02,000
Hello there.

2
00:00:03,000 --> 00:00:04,500
General Kenobi!
Two lines.

"""


def test_parse_basic():
    blocks = parse_srt_text(BASIC)
    assert [b.index for b in blocks] == ["1", "2"]
    assert blocks[1].timestamp == "00:00:03,000 --> 00:00:04,500"
    assert blocks[1].text == "General Kenobi!\nTwo lines."


def test_parse_crlf_and_bom():
    blocks = parse_srt_text("﻿" + BASIC.replace("\n", "\r\n"))
    assert len(blocks) == 2 and blocks[0].text == "Hello there."


def test_parse_keeps_block_without_text():
    blocks = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\nHi\n")
    assert [b.index for b in blocks] == ["1", "2"]
    assert blocks[0].text == "" and blocks[1].text == "Hi"


def test_parse_blank_line_inside_text():
    blocks = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\nLine one\n\nLine two\n\n2\n00:00:03,000 --> 00:00:04,000\nNext\n")
    assert len(blocks) == 2
    assert blocks[0].text == "Line one\nLine two"


def test_parse_missing_blank_between_blocks():
    blocks = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\nA\n2\n00:00:03,000 --> 00:00:04,000\nB\n")
    assert [b.text for b in blocks] == ["A", "B"]


def test_parse_missing_index_line():
    blocks = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\nA\n\n00:00:03,000 --> 00:00:04,000\nB\n")
    assert [b.index for b in blocks] == ["1", "2"]


def test_parse_numeric_text_line():
    blocks = parse_srt_text("1\n00:00:01,000 --> 00:00:02,000\nHow many?\n42\n\n2\n00:00:03,000 --> 00:00:04,000\nB\n")
    assert blocks[0].text == "How many?\n42"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "cp1252"])
def test_decode_encodings(encoding):
    text = "1\n00:00:01,000 --> 00:00:02,000\nCafé déjà vu\n"
    assert decode_srt_bytes(text.encode(encoding)) == text


def test_decode_cp1255_hebrew():
    text = "1\n00:00:01,000 --> 00:00:02,000\nשלום עולם\n"
    assert decode_srt_bytes(text.encode("cp1255")) == text


def test_rtl_fix():
    assert fix_rtl_line("שלום.") == f"{RLM}שלום.{RLM}"
    assert fix_rtl_line(fix_rtl_line("שלום.")) == f"{RLM}שלום.{RLM}"  # idempotent
    assert fix_rtl_line("Hello.") == "Hello."
    assert fix_rtl_text("<i>מה?</i>\nOK") == f"{RLM}<i>מה?</i>{RLM}\nOK"


def test_validate_translation():
    orig = parse_srt_text(BASIC)
    good = parse_srt_text(BASIC.replace("Hello there.", "שלום."))
    assert validate_translation(orig, good) == (True, "")
    ok, reason = validate_translation(orig, good[:1])
    assert not ok and "Missing indices: 2" in reason
    bad = parse_srt_text(BASIC.replace("00:00:03,000", "00:00:03,100"))
    assert not validate_translation(orig, bad)[0]


def test_extract_part_number_uses_last_match():
    assert extract_part_number("Show part 2_part_11.srt") == 11
    assert extract_part_number("Movie_part_3.srt") == 3


def test_split_and_merge_roundtrip(tmp_path):
    src = tmp_path / "Movie.srt"
    body = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},500\nLine {i}\n\n" for i in range(1, 26))
    src.write_text(body, encoding="utf-8")
    split_dir = tmp_path / "split"
    split_dir.mkdir()
    assert split_srt_file(str(src), str(split_dir), 10) == 3
    parts = list_srt_parts(str(split_dir))
    assert [os.path.basename(p) for p in parts] == ["Movie_part_1.srt", "Movie_part_2.srt", "Movie_part_3.srt"]
    out = tmp_path / "out.srt"
    assert merge_srt_files(list(reversed(parts)), str(out)) == 25
    assert compare_structure(parse_srt(str(src)), parse_srt(str(out))) == (True, "")
