"""
Gemini subtitle translation.

Only the subtitle *text* is sent to the model, as JSON ({"id", "text"} items); indices and
timestamps never leave this machine, so the model can't break them and we don't pay output
tokens for them. Each part is translated with a little context from its neighbours, missing
or untranslated items are retried individually, and finished parts are reused on the next run.
"""
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from google import genai
from google.genai import types
from pydantic import BaseModel

from srt_utils import SRTBlock, list_srt_parts, parse_srt, validate_translation, write_srt

# Text models offered in Settings. Prices: USD per 1M tokens (paid tier, standard),
# from https://ai.google.dev/gemini-api/docs/pricing - thinking tokens are billed as output.
MODELS = {
    "gemini-3.8-flash":      {"input": 0.75, "output": 3.75},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
}
DEFAULT_MODEL = "gemini-3.8-flash"
THINKING_LEVELS = ("low", "medium", "high")

# Languages offered in the UI (code -> English name used in the prompt)
LANGUAGES = {
    "he": "Hebrew", "en": "English", "ar": "Arabic", "ru": "Russian", "es": "Spanish",
    "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese", "nl": "Dutch",
    "pl": "Polish", "tr": "Turkish", "uk": "Ukrainian", "el": "Greek", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese (Simplified)", "hi": "Hindi", "fa": "Persian",
}
RTL_LANGUAGES = {"he", "ar", "fa"}

# Letters of the target script: a translated item with none of these is treated as untranslated
TARGET_SCRIPT = {
    "he": r"[א-ת]", "ar": r"[ؠ-ي]", "fa": r"[ؠ-يپ-ی]",
    "ru": r"[Ѐ-ӿ]", "uk": r"[Ѐ-ӿ]", "el": r"[Ͱ-Ͽ]",
    "ja": r"[぀-ヿ一-鿿]", "zh": r"[一-鿿]", "ko": r"[가-힯]",
    "hi": r"[ऀ-ॿ]",
}
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")

# Retry policy
MAX_ATTEMPTS = 3                   # model attempts per part (missing items only after the first)
RATE_LIMIT_BACKOFF = (10, 20, 40, 60, 90)  # seconds, for 429 / 503 on the standard tier
ERROR_DELAY = 5

# Flex inference (preview): 50% cheaper, best-effort capacity, can queue for minutes.
# https://ai.google.dev/gemini-api/docs/flex-inference
FLEX_TIMEOUT_MS = 900_000          # recommended client timeout (15 min)
FLEX_BUSY_RETRIES = 3              # 429/503 retries before falling back to standard
FLEX_BACKOFF = (15, 30, 60)        # seconds

CONTEXT_BEFORE = 4                 # source lines from the previous part sent as context
CONTEXT_AFTER = 2                  # source lines from the next part sent as context
MANIFEST = ".translation.json"     # remembers the options a translated folder was made with


class Cancelled(Exception):
    pass


class Item(BaseModel):
    id: str
    text: str


class Character(BaseModel):
    name: str
    gender: str          # male / female / unknown
    target_name: str     # how the name is written in the target language
    notes: str           # role, who they talk to formally / informally, etc.


class Term(BaseModel):
    source: str
    target: str


class Analysis(BaseModel):
    characters: list[Character]
    terms: list[Term]
    tone: str


ANALYSIS_MAX_CHARS = 200_000       # source text sent to the analysis call (~50k tokens)
ANALYSIS_MODEL = DEFAULT_MODEL     # the glossary is built once per file - use the best model for it
ANALYSIS_THINKING = "medium"
GLOSSARY_VERSION = "3"             # bump to regenerate glossaries made by an older prompt


def _is_capacity_error(e):
    msg = str(e).lower()
    return any(k in msg for k in ("503", "429", "unavailable", "resource_exhausted", "overloaded", "rate limit"))


def build_system_prompt(source_lang="auto", target_lang="he", convert_units=True, glossary="", instructions=""):
    target = LANGUAGES.get(target_lang, target_lang)
    source = "the source language (detect it)" if source_lang in ("", "auto") else LANGUAGES.get(source_lang, source_lang)
    rules = [
        f"You are a professional film and TV subtitle translator. Translate subtitles from {source} into {target}.",
        "",
        "Input: a JSON object. \"subtitles\" is a list of {\"id\", \"text\"} items to translate. "
        "\"context_before\" / \"context_after\", when present, are neighbouring subtitle lines given ONLY "
        "for context - never translate or return them.",
        "",
        "Rules:",
        "- Return a JSON list with exactly one {\"id\", \"text\"} item for every input item, with the same ids "
        "in the same order. Never merge, split, skip, reorder or add items, even if a sentence continues "
        "across items.",
        f"- Write natural, idiomatic {target} as it is actually spoken, matching each character's tone and register. "
        "Translate the full meaning of every item - don't drop information - but prefer short, natural "
        "phrasing that reads easily on screen.",
        "- Keep the line structure: at most 2 lines per item, breaking lines where the source does.",
        "- Keep formatting tags such as <i>, </i>, <b>, <font ...> and {\\an8} exactly, around the matching words. "
        "Keep music notes (♪) and other symbols.",
        "- Translate sound and speaker labels in brackets, e.g. [laughs], too.",
        "- Names: transliterate only the names of people (and pets) and brand / company names. Names of places, "
        "sites, buildings, events and nicknames that are made of ordinary words (e.g. \"Base Camp Two\", "
        "\"the Triangle\", \"Hill Country\") must be TRANSLATED into natural "
        f"{target}, the way a professional {target} subtitler would - never written as a phonetic copy of the "
        "English words. Keep every name consistent throughout.",
        "- Abbreviations: use an established target-language abbreviation only if one is in common use; "
        "otherwise keep the original Latin abbreviation (EEG, GPS, DNA) or spell the term out. Never invent "
        "new abbreviations.\n"
        "- Infer gender (speaker and addressee), formality and who is talking from the context, and use the "
        "correct grammatical forms.",
    ]
    if convert_units:
        rules.append("- Convert imperial units to metric (miles -> km, pounds -> kg, feet -> meters, "
                     "Fahrenheit -> Celsius, etc.) and convert the NUMBER too, rounding to natural values: "
                     "500 feet -> 150 meters, \"hundreds of feet\" -> tens of meters (not hundreds), "
                     "a mile -> 1.6 km, 100 pounds -> 45 kg.")
    if target_lang == "he":
        rules.append("- Use modern spoken Israeli Hebrew, without niqqud.")
    if glossary.strip():
        rules += ["", "Glossary and character notes - use these names, genders and terms consistently:", glossary.strip()]
    if instructions.strip():
        rules += ["", "Additional instructions from the user:", instructions.strip()]
    return "\n".join(rules)


def format_analysis(analysis):
    lines = []
    if analysis.characters:
        lines.append("Characters:")
        for c in analysis.characters:
            notes = f" - {c.notes}" if c.notes.strip() else ""
            lines.append(f"- {c.name} ({c.gender}) = {c.target_name}{notes}")
    if analysis.terms:
        lines.append("Terms:")
        lines += [f"- {t.source} = {t.target}" for t in analysis.terms]
    if analysis.tone.strip():
        lines.append(f"Tone: {analysis.tone.strip()}")
    return "\n".join(lines)


def needs_translation(text):
    return bool(_LATIN_WORD.search(text))


def looks_translated(source, translated, target_lang):
    """False if the item clearly wasn't translated into the target script."""
    pattern = TARGET_SCRIPT.get(target_lang)
    if not pattern or not needs_translation(source):
        return True
    return re.search(pattern, translated) is not None


class TranslationEngine:
    """Holds the Gemini clients, options, usage totals and the cancel flag for one run."""

    def __init__(self, api_key=None, model=DEFAULT_MODEL, thinking_level=None, flex=False,
                 source_lang="auto", target_lang="he", convert_units=True, glossary="", instructions="",
                 cancel_event=None):
        key = {"api_key": api_key} if api_key else {}
        self.standard_client = genai.Client(**key)
        # Flex requests can sit in a queue for minutes - use a long client timeout
        self.flex_client = genai.Client(http_options={"timeout": FLEX_TIMEOUT_MS}, **key) if flex else None
        self.model = model if model in MODELS else DEFAULT_MODEL
        self.thinking_level = thinking_level if thinking_level in THINKING_LEVELS else None
        self.flex = flex
        self.source_lang = source_lang or "auto"
        self.target_lang = target_lang or "he"
        self.convert_units = convert_units
        self.glossary = glossary or ""
        self.instructions = instructions or ""
        self.system_prompt = build_system_prompt(self.source_lang, self.target_lang, convert_units,
                                                 self.glossary, self.instructions)
        self.cancel_event = cancel_event or threading.Event()
        self._lock = threading.Lock()
        self._no_thinking_level = False
        self._flex_unsupported = False
        self.usage = {"input": 0, "output": 0, "thinking": 0, "cost": 0.0, "calls": 0}

    # ---------- helpers ----------
    def signature(self):
        """Options that change the translation itself - a folder made with other options is not reused."""
        raw = json.dumps([self.source_lang, self.target_lang, self.convert_units,
                          self.glossary.strip(), self.instructions.strip()], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def sleep(self, seconds):
        if self.cancel_event.wait(seconds):
            raise Cancelled()

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def _config(self, use_flex, schema=list[Item], system_prompt=None, thinking_level=None):
        kwargs = dict(system_instruction=system_prompt or self.system_prompt, temperature=0.2)
        if schema is not None:
            kwargs.update(response_mime_type="application/json", response_schema=schema)
        level = thinking_level or self.thinking_level
        if level and not self._no_thinking_level:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=level)
        if use_flex and not self._flex_unsupported:
            try:
                return types.GenerateContentConfig(service_tier="flex", **kwargs)
            except Exception as e:  # older google-genai without service_tier
                self._flex_unsupported = True
                print(f"Flex is not supported by this google-genai version - using standard tier. "
                      f"Run 'pip install -U google-genai' to enable it. ({e})")
        return types.GenerateContentConfig(**kwargs)

    def _record_usage(self, response, flex_used, model=None):
        usage = getattr(response, "usage_metadata", None)
        if not usage:
            return
        prompt = usage.prompt_token_count or 0
        output = usage.candidates_token_count or 0
        thoughts = usage.thoughts_token_count or 0
        price = MODELS[model or self.model]
        cost = (prompt * price["input"] + (output + thoughts) * price["output"]) / 1e6
        if flex_used:
            cost *= 0.5
        with self._lock:
            u = self.usage
            u["input"] += prompt
            u["output"] += output
            u["thinking"] += thoughts
            u["cost"] += cost
            u["calls"] += 1

    def generate(self, contents, label, schema=list[Item], system_prompt=None, model=None, thinking_level=None):
        """One model call with thinking / flex / rate-limit handling. Other errors are raised."""
        use_flex = self.flex
        flex_busy = 0
        limited = 0
        while True:
            self.check_cancel()
            client = self.flex_client if use_flex and self.flex_client else self.standard_client
            flex_used = use_flex and self.flex_client is not None and not self._flex_unsupported
            try:
                response = client.models.generate_content(
                    model=model or self.model, contents=contents,
                    config=self._config(use_flex, schema, system_prompt, thinking_level))
                self._record_usage(response, flex_used, model)
                return response
            except Cancelled:
                raise
            except Exception as e:
                msg = str(e).lower()
                # Some models don't accept thinking_level - drop it and retry
                if self.thinking_level and not self._no_thinking_level and "thinking" in msg:
                    self._no_thinking_level = True
                    print(f"{self.model} doesn't support thinking level '{self.thinking_level}' - using the model default.")
                    continue
                if not _is_capacity_error(e):
                    raise
                # Flex capacity is best-effort: back off, then fall back to the standard tier
                if use_flex:
                    if flex_busy < FLEX_BUSY_RETRIES:
                        wait = FLEX_BACKOFF[min(flex_busy, len(FLEX_BACKOFF) - 1)]
                        flex_busy += 1
                        print(f"Flex is busy for {label}. Waiting {wait}s (flex retry {flex_busy}/{FLEX_BUSY_RETRIES})...")
                        self.sleep(wait)
                    else:
                        use_flex = False
                        print(f"Flex unavailable - falling back to standard tier for {label} (full price).")
                    continue
                if limited >= len(RATE_LIMIT_BACKOFF):
                    raise
                wait = RATE_LIMIT_BACKOFF[limited]
                limited += 1
                print(f"Rate limited on {label}. Waiting {wait}s (retry {limited}/{len(RATE_LIMIT_BACKOFF)})...")
                self.sleep(wait)

    def set_glossary(self, glossary):
        self.glossary = glossary or ""
        self.system_prompt = build_system_prompt(self.source_lang, self.target_lang, self.convert_units,
                                                 self.glossary, self.instructions)

    # ---------- analysis ----------
    def analyze(self, blocks):
        """
        One call over the whole source text that returns a glossary: characters with gender and
        target-language spelling, recurring terms and the overall tone. Every part is then
        translated with it, so names, genders and terms stay consistent across parts.
        """
        target = LANGUAGES.get(self.target_lang, self.target_lang)
        prompt = (
            f"You prepare a translation brief for translating these subtitles into {target}. "
            "Read the dialogue and return:\n"
            "- characters: every named PERSON - name as written in the source, gender (male / female / "
            f"unknown), the name written in {target} letters, and short notes (role, relationships, "
            "who they address formally or informally).\n"
            "- terms: recurring places, sites, organisations, made-up words, ranks, jargon and slang, each with the "
            f"{target} rendering a professional {target} subtitler would use. TRANSLATE every term that is made of "
            "ordinary words (\"Base Camp Two\" is translated word by word into natural "
            f"{target}, not written phonetically); transliterate only personal names and brand names. "
            "Keep established translations of well-known terms (e.g. UFO = עב\"ם in Hebrew). For abbreviations, use "
            "an established target-language abbreviation only if it is in common use, otherwise keep the Latin "
            "abbreviation (EEG, GPS) - never invent new abbreviations.\n"
            "- tone: one or two sentences on genre, register and how the characters speak.\n"
            "Be concise. Only include what actually appears in the text."
        )
        text = "\n".join(b.text for b in blocks if b.text.strip())[:ANALYSIS_MAX_CHARS]
        # One call per file, so it always uses the strongest model with more thinking - a wrong
        # entry here would be repeated in every part, whatever model translates the parts.
        response = self.generate(text, "analysis", schema=Analysis, system_prompt=prompt,
                                 model=ANALYSIS_MODEL, thinking_level=ANALYSIS_THINKING)
        result = getattr(response, "parsed", None)
        if result is None:
            result = Analysis(**json.loads(response.text))
        return format_analysis(result)

    # ---------- translation ----------
    def _request(self, items, before, after, label):
        """items: list of (id, SRTBlock). Returns {id: translated text}."""
        payload = {"subtitles": [{"id": key, "text": b.text} for key, b in items]}
        if before:
            payload["context_before"] = [b.text for b in before]
        if after:
            payload["context_after"] = [b.text for b in after]
        response = self.generate(json.dumps(payload, ensure_ascii=False), label)
        parsed = getattr(response, "parsed", None)
        if parsed is None:
            text = (response.text or "").strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
            parsed = [Item(**x) for x in json.loads(text)]
        return {str(x.id).strip(): x.text for x in parsed}

    def translate_blocks(self, blocks, before=(), after=(), label="part"):
        """
        Translate a list of SRTBlocks. Returns (translated blocks, list of warnings).
        Raises if some items are still missing after all attempts.
        """
        # Ids are positions, not SRT indices - broken files can repeat an index
        todo = [(str(i + 1), b) for i, b in enumerate(blocks) if b.text.strip()]
        result = {}
        suspicious = {}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            pending = [(k, b) for k, b in todo if k not in result]
            if not pending:
                break
            self.check_cancel()
            try:
                answer = self._request(pending, before, after, label)
            except Cancelled:
                raise
            except Exception as e:
                print(f"Attempt {attempt}/{MAX_ATTEMPTS} failed for {label}: {e}")
                if attempt < MAX_ATTEMPTS:
                    self.sleep(ERROR_DELAY)
                continue
            retry = []
            for k, b in pending:
                text = (answer.get(k) or "").strip()
                if not text:
                    retry.append(b.index)
                elif not looks_translated(b.text, text, self.target_lang) and attempt < MAX_ATTEMPTS:
                    suspicious[k] = text  # retry it; keep this answer as a fallback
                    retry.append(b.index)
                else:
                    result[k] = text
                    suspicious.pop(k, None)
            if retry:
                shown = ", ".join(retry[:10]) + (" ..." if len(retry) > 10 else "")
                print(f"{label}: {len(retry)} item(s) missing or untranslated ({shown}) - "
                      f"{'retrying just those' if attempt < MAX_ATTEMPTS else 'giving up'}.")

        warnings = []
        for k, b in todo:
            if k not in result and k in suspicious:
                result[k] = suspicious[k]
                warnings.append(f"Block {b.index} may be untranslated.")
        missing = [b.index for k, b in todo if k not in result]
        if missing:
            raise RuntimeError(f"{label}: no translation for block(s) {', '.join(missing[:10])}"
                               f"{' ...' if len(missing) > 10 else ''} after {MAX_ATTEMPTS} attempts.")
        keys = {id(b): k for k, b in todo}
        out = [SRTBlock(b.index, b.timestamp, result[keys[id(b)]] if id(b) in keys else b.text) for b in blocks]
        return out, warnings


# ---------- folder pipeline ----------
GLOSSARY_FILE = "glossary.txt"
_GLOSSARY_HEADER = "# Auto-generated for: "


def _source_hash(blocks, target_lang):
    raw = GLOSSARY_VERSION + target_lang + "\n" + "\n".join(b.text for b in blocks)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_or_build_glossary(engine, source_blocks, folder):
    """
    Reuse folder/glossary.txt if it was made for this source text (the user may have edited it),
    otherwise analyse the source and save a new one. Returns the glossary text.
    """
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, GLOSSARY_FILE)
    header = f"{_GLOSSARY_HEADER}{_source_hash(source_blocks, engine.target_lang)}"
    try:
        with open(path, encoding="utf-8") as f:
            first, _, body = f.read().partition("\n")
        if first.strip() == header:
            print(f"Using the glossary in {path}")
            return body.strip()
    except OSError:
        pass
    print("Analysing characters, genders and terms...")
    glossary = engine.analyze(source_blocks)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{header}\n{glossary}\n")
    print(f"Glossary saved to {path} - edit it and run again to change names or terms.")
    return glossary



def _read_manifest(output_dir):
    try:
        with open(os.path.join(output_dir, MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_manifest(output_dir, signature):
    with open(os.path.join(output_dir, MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"signature": signature}, f)


def is_part_done(src_path, out_path):
    """A translated part can be reused if it exists and matches its source part block-for-block."""
    if not os.path.exists(out_path):
        return False
    return validate_translation(parse_srt(src_path), parse_srt(out_path))[0]


def translate_directory(input_dir, output_dir, engine, progress_callback=None, workers=3, resume=True):
    """
    Translate every SRT part in input_dir into output_dir, in parallel.
    Parts already translated with the same options are skipped (resume).
    Returns (ok, summary dict).
    """
    os.makedirs(output_dir, exist_ok=True)
    parts = list_srt_parts(input_dir)
    if not parts:
        print(f"No SRT files found in {input_dir}")
        return False, {}

    signature = engine.signature()
    if resume and _read_manifest(output_dir).get("signature") not in (None, signature):
        print("Translation options changed since the last run - translating all parts again.")
        resume = False
    # Drop translated parts that don't belong to the current split (e.g. the chunk size changed)
    names = {os.path.basename(p) for p in parts}
    for f in os.listdir(output_dir):
        if f.lower().endswith(".srt") and (not resume or f not in names):
            os.remove(os.path.join(output_dir, f))
    _write_manifest(output_dir, signature)

    blocks = [parse_srt(p) for p in parts]
    jobs, done = [], 0
    for i, path in enumerate(parts):
        out = os.path.join(output_dir, os.path.basename(path))
        if resume and is_part_done(path, out):
            done += 1
            continue
        before = blocks[i - 1][-CONTEXT_BEFORE:] if i > 0 else []
        after = blocks[i + 1][:CONTEXT_AFTER] if i + 1 < len(parts) else []
        jobs.append((path, out, blocks[i], before, after))

    total = len(parts)
    print(f"{total} part(s): {done} already translated, {len(jobs)} to translate. "
          f"Model: {engine.model}, thinking: {engine.thinking_level or 'model default'}, "
          f"tier: {'flex' if engine.flex else 'standard'}, "
          f"{LANGUAGES.get(engine.source_lang, 'auto')} -> {LANGUAGES.get(engine.target_lang, engine.target_lang)}")
    if done and progress_callback:
        progress_callback(done, total, "")

    failed, warnings = [], []

    def work(job):
        path, out, part_blocks, before, after = job
        name = os.path.basename(path)
        translated, warns = engine.translate_blocks(part_blocks, before, after, label=name)
        write_srt(translated, out)
        return name, warns

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        try:
            for future in as_completed(futures):
                name = os.path.basename(futures[future][0])
                try:
                    _, warns = future.result()
                    warnings += [f"{name}: {w}" for w in warns]
                    print(f"Translated: {name}")
                except Cancelled:
                    continue
                except Exception as e:
                    failed.append(name)
                    print(f"Failed: {name} - {e}")
                done += 1
                if progress_callback:
                    progress_callback(done, total, name)
        finally:
            if engine.cancel_event.is_set():
                for f in futures:
                    f.cancel()

    if engine.cancel_event.is_set():
        raise Cancelled()

    for w in warnings:
        print(f"Warning - {w}")
    u = engine.usage
    print(f"Tokens - input: {u['input']:,} | output: {u['output']:,} | thinking: {u['thinking']:,} "
          f"| cost: ${u['cost']:.4f} ({u['calls']} call(s))")
    if failed:
        print(f"{len(failed)} part(s) failed: {', '.join(failed)}. Run again to retry just those.")
        return False, {"failed": failed, "warnings": warnings}
    print(f"Translation complete: {total}/{total} parts.")
    return True, {"failed": [], "warnings": warnings}
