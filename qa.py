"""
Quality checks for a translated subtitle file, and a fixer that re-translates only the
blocks that failed them.
"""
import re

from srt_utils import RTL_CHARS_RE, fix_rtl_text, parse_srt, write_srt

MAX_LINE_CHARS = 42     # common broadcast / streaming limit per line
MAX_LINES = 2
MAX_CPS = 20            # characters per second an adult viewer can comfortably read

_TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")
_BIDI_RE = re.compile(r"[‎‏‪-‮]")
_TS_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")

# Issue kinds, in display order. FIXABLE ones are sent back to the model by fix_issues().
KINDS = ("untranslated", "tags", "lines", "long", "fast")
FIXABLE = {"untranslated", "tags", "lines", "long"}


def visible(text):
    return _BIDI_RE.sub("", _TAG_RE.sub("", text))


def _seconds(ts):
    h, m, s, ms = _TS_RE.match(ts.strip()).groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000


def duration(timestamp):
    try:
        start, end = timestamp.split("-->")
        return max(_seconds(end) - _seconds(start), 0.001)
    except (ValueError, AttributeError):
        return None


def _tags(text):
    return sorted(t.lower() for t in _TAG_RE.findall(text))


def check(source_blocks, translated_blocks, target_lang, looks_translated):
    """
    Compare a translated file with its source block by block.
    looks_translated(source_text, translated_text, target_lang) -> bool comes from translator.
    Returns a list of {"index", "kind", "detail", "text"} dicts.
    """
    issues = []
    for src, out in zip(source_blocks, translated_blocks):
        if not out.text.strip():
            continue
        add = lambda kind, detail: issues.append(
            {"index": out.index, "kind": kind, "detail": detail, "text": visible(out.text)})
        if not looks_translated(src.text, visible(out.text), target_lang):
            add("untranslated", "")
        if _tags(src.text) != _tags(out.text):
            add("tags", " ".join(_TAG_RE.findall(src.text)))
        lines = visible(out.text).split("\n")
        if len(lines) > MAX_LINES:
            add("lines", str(len(lines)))
        longest = max(len(line) for line in lines)
        if longest > MAX_LINE_CHARS:
            add("long", str(longest))
        secs = duration(out.timestamp)
        if secs:
            cps = len(visible(out.text).replace("\n", "")) / secs
            if cps > MAX_CPS:
                add("fast", f"{cps:.0f}")
    return issues


def summarize(issues):
    counts = {k: 0 for k in KINDS}
    for issue in issues:
        counts[issue["kind"]] += 1
    return {k: v for k, v in counts.items() if v}


FIX_INSTRUCTIONS = (
    "These subtitles failed a quality check (not translated, too long, too many lines or "
    "broken formatting tags). Translate them again: every item must be in the target language, "
    f"at most {MAX_LINES} lines of at most {MAX_LINE_CHARS} characters each, keep exactly the "
    "formatting tags of the source, and condense the wording if needed while keeping the meaning."
)


def fix_issues(engine, source_path, final_path, issues, rtl_fix=False, bom=False, context=3):
    """
    Re-translate the source blocks behind the fixable issues and patch them into the final file.
    Returns the number of blocks replaced.
    """
    wanted = {i["index"] for i in issues if i["kind"] in FIXABLE}
    if not wanted:
        return 0
    source = parse_srt(source_path)
    final = parse_srt(final_path)
    if len(source) != len(final):
        raise ValueError("The translated file doesn't match the source anymore - run the merge step again.")

    engine.instructions = (engine.instructions + "\n\n" if engine.instructions.strip() else "") + FIX_INSTRUCTIONS
    engine.set_glossary(engine.glossary)  # rebuild the system prompt with the extra instructions

    positions = [i for i, b in enumerate(source) if b.index in wanted]
    # Translate in small groups of neighbouring blocks so each request carries its own context
    replaced = 0
    for start in range(0, len(positions), 40):
        group = positions[start:start + 40]
        blocks = [source[i] for i in group]
        before = source[max(group[0] - context, 0):group[0]]
        after = source[group[-1] + 1:group[-1] + 1 + context]
        translated, _ = engine.translate_blocks(blocks, before, after, label="quality fix")
        for pos, block in zip(group, translated):
            text = fix_rtl_text(block.text) if rtl_fix and RTL_CHARS_RE.search(block.text) else block.text
            if text != final[pos].text:
                final[pos].text = text
                replaced += 1
    write_srt(final, final_path, bom=bom)
    return replaced
