"""
AI SRT Translator - modern UI (pywebview + HTML/CSS).

The window shows web/index.html; the JavaScript side calls the methods of the
Api class below (window.pywebview.api.<method>), and Python pushes live
progress back with window.onPyEvent(...).
The subtitle logic itself lives untouched in srt_utils.py and translator.py.
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import traceback

import webview
from dotenv import dotenv_values, set_key

from srt_utils import compare_structure, list_srt_parts, merge_srt_files, parse_srt, split_srt_file
import qa
from translator import (DEFAULT_MODEL, LANGUAGES, MODELS, RTL_LANGUAGES, Cancelled, TranslationEngine,
                        load_or_build_glossary, looks_translated, translate_directory)

APP_NAME = "AI SRT Translator"

if getattr(sys, "frozen", False):
    # Running as a PyInstaller build: bundled files (web/, icon) are unpacked to a temp
    # folder, while user files (.env, logs) live next to the executable.
    RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    BASE_DIR = os.path.dirname(sys.executable)
    if sys.platform == "darwin":
        # Inside a .app the executable sits in Contents/MacOS, which is replaced on every
        # update and may be read-only - keep user files in Application Support instead.
        BASE_DIR = os.path.join(os.path.expanduser("~/Library/Application Support"), APP_NAME)
        os.makedirs(BASE_DIR, exist_ok=True)
else:
    RESOURCE_DIR = BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ENV_FILE = os.path.join(BASE_DIR, ".env")
INSTRUCTIONS_FILE = os.path.join(BASE_DIR, "instructions.txt")
WEB_DIR = os.path.join(RESOURCE_DIR, "web")

DEFAULT_DIRS = {"split": "split", "merge": "merge", "output": "translated_file"}
DEFAULT_CHUNK = 150
DEFAULT_THINKING = "low"
THINKING_LEVELS = ("low", "medium", "high")
NAMING_MODES = ("lang", "suffix")   # Movie.he.srt  /  Movie_translated.srt
DEFAULT_WORKERS = 3
MAX_WORKERS = 8


def read_env():
    if not os.path.exists(ENV_FILE):
        return {}
    return {k: (v or "") for k, v in dotenv_values(ENV_FILE).items()}


def write_env(key, value):
    if not os.path.exists(ENV_FILE):
        open(ENV_FILE, "w", encoding="utf-8").close()
    set_key(ENV_FILE, key, value)


def prepare_directory(path, clear=True):
    if clear and os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)


def open_in_explorer(path):
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class LogStream:
    """File-like object that forwards every printed line to the UI log."""

    def __init__(self, emit):
        self.emit = emit
        self.buffer = ""
        self.lock = threading.Lock()  # translation workers print from several threads

    def write(self, text):
        with self.lock:
            self.buffer += text
            lines = []
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                if line.strip():
                    lines.append(line)
        for line in lines:
            self.emit(line)

    def flush(self):
        with self.lock:
            rest, self.buffer = self.buffer, ""
        if rest.strip():
            self.emit(rest)


class Api:
    def __init__(self):
        self._window = None
        self._file = None
        self._busy = False
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._queue = []          # [{"path", "status", "final", "error"}] - files for a batch run
        self._usage_base = {}     # usage of earlier files in the current batch
        self._usage_total = {}

    # ---------- plumbing ----------
    def _send(self, event, **data):
        if self._window:
            payload = json.dumps({"event": event, **data}, ensure_ascii=False)
            self._window.evaluate_js(f"window.onPyEvent && window.onPyEvent({payload})")

    def _log(self, message, level="info"):
        self._send("log", message=message, level=level)

    def _dirs(self, path=None):
        path = path or self._file
        env = read_env()
        base = os.path.dirname(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        names = {
            "split": env.get("CUSTOM_SPLIT_DIR", "").strip() or DEFAULT_DIRS["split"],
            "merge": env.get("CUSTOM_MERGE_DIR", "").strip() or DEFAULT_DIRS["merge"],
            "output": env.get("CUSTOM_OUTPUT_DIR", "").strip() or DEFAULT_DIRS["output"],
        }
        dirs = {k: os.path.normpath(os.path.join(base, v)) for k, v in names.items()}
        # Each source file gets its own working folders, so several files in one folder don't mix
        dirs["split"] = os.path.join(dirs["split"], stem)
        dirs["merge"] = os.path.join(dirs["merge"], stem)
        return dirs

    def _final_path(self, path=None):
        path = path or self._file
        name = os.path.splitext(os.path.basename(path))[0]
        if self._naming() == "suffix":
            filename = f"{name}_translated.srt"
        else:
            # Movie.he.srt - video players load it automatically next to Movie.mkv
            filename = f"{name}.{self._target_lang()}.srt"
        return os.path.join(self._dirs(path)["output"], filename)

    def _api_key(self):
        return read_env().get("GEMINI_API_KEY", "").strip()

    def _count_srt(self, folder):
        if not os.path.isdir(folder):
            return 0
        return len([f for f in os.listdir(folder) if f.lower().endswith(".srt")])

    # ---------- state ----------
    def get_state(self):
        env = read_env()
        key = self._api_key()
        state = {
            "lang": env.get("UI_LANG", "he") or "he",
            "theme": env.get("UI_THEME", "system") or "system",
            "has_key": bool(key),
            "key_hint": ("••••" + key[-4:]) if len(key) >= 8 else "",
            "settings": {
                "split": env.get("CUSTOM_SPLIT_DIR", ""),
                "merge": env.get("CUSTOM_MERGE_DIR", ""),
                "output": env.get("CUSTOM_OUTPUT_DIR", ""),
            },
            "defaults": DEFAULT_DIRS,
            "chunk": self._saved_chunk(),
            "thinking": self._thinking(),
            "model": self._model(),
            "flex": self._flex(),
            "workers": self._workers(),
            "auto_glossary": self._auto_glossary(),
            "instructions": self._instructions(),
            "languages": list(LANGUAGES),
            "source_lang": self._source_lang(),
            "target_lang": self._target_lang(),
            "convert_units": self._env_flag("CONVERT_UNITS", True),
            "naming": self._naming(),
            "bom": self._env_flag("SUBTITLE_BOM", False),
            "models": [{"id": k, **v} for k, v in MODELS.items()],
            "file": self._file_info() if self._file else None,
            "queue": self._queue_info(),
            "busy": self._busy,
        }
        return state

    def _file_info(self):
        blocks = parse_srt(self._file)
        dirs = self._dirs()
        final = self._final_path()
        return {
            "path": self._file,
            "name": os.path.basename(self._file),
            "blocks": len(blocks),
            "duration": blocks[-1].timestamp.split("-->")[-1].strip().split(",")[0] if blocks else "",
            "dirs": dirs,
            "split_count": self._count_srt(dirs["split"]),
            "merge_count": self._count_srt(dirs["merge"]),
            "final": final if os.path.exists(final) else None,
        }

    def _dialog(self, kind, **kwargs):
        dialog = getattr(webview, "FileDialog", None)
        types_ = {"open": (dialog.OPEN if dialog else webview.OPEN_DIALOG),
                  "folder": (dialog.FOLDER if dialog else webview.FOLDER_DIALOG)}
        result = self._window.create_file_dialog(types_[kind], **kwargs)
        if not result:
            return []
        return list(result) if isinstance(result, (list, tuple)) else [result]

    def pick_file(self):
        paths = self._dialog("open", allow_multiple=True,
                             file_types=("SRT files (*.srt)", "All files (*.*)"))
        return self.set_files(paths) if paths else None

    def pick_folder(self):
        folders = self._dialog("folder")
        return self.set_files(folders) if folders else None

    def _is_output_name(self, name):
        lower = name.lower()
        return lower.endswith("_translated.srt") or lower.endswith(f".{self._target_lang()}.srt")

    def _expand(self, paths):
        """Files as given, folders expanded to the source SRT files directly inside them."""
        files = []
        for path in paths:
            if os.path.isdir(path):
                for name in sorted(os.listdir(path), key=str.lower):
                    full = os.path.join(path, name)
                    if os.path.isfile(full) and name.lower().endswith(".srt") and not self._is_output_name(name):
                        files.append(full)
            elif os.path.isfile(path) and path.lower().endswith(".srt"):
                files.append(path)
        seen = set()
        return [f for f in files if not (f in seen or seen.add(f))]

    def set_files(self, paths):
        if self._busy:
            return {"error": "busy"}
        paths = [p for p in (paths or []) if p]
        if len(paths) == 1 and os.path.isfile(paths[0]):
            return self.set_file(paths[0])
        files = self._expand(paths)
        if not files:
            return {"error": "no_srt_in_folder" if any(os.path.isdir(p) for p in paths) else "not_srt"}
        if len(files) == 1:
            return self.set_file(files[0])
        self._queue = [{"path": f, "status": "pending", "final": None, "error": ""} for f in files]
        self._file = files[0]
        return {"file": self._file_info(), "queue": self._queue_info()}

    def set_file(self, path):
        if self._busy:
            return {"error": "busy"}
        if not path or not os.path.isfile(path):
            return {"error": "file_not_found"}
        if not path.lower().endswith(".srt"):
            return {"error": "not_srt"}
        self._file = path
        self._queue = []
        info = self._file_info()
        if info["blocks"] == 0:
            return {"error": "empty_srt", "file": info, "queue": []}
        return {"file": info, "queue": []}

    def select_queue_item(self, path):
        if self._busy or not any(q["path"] == path for q in self._queue):
            return {"error": "busy" if self._busy else "file_not_found"}
        self._file = path
        return {"file": self._file_info(), "queue": self._queue_info()}

    def remove_queue_item(self, path):
        if self._busy:
            return {"error": "busy"}
        self._queue = [q for q in self._queue if q["path"] != path]
        if len(self._queue) == 1:
            return self.set_file(self._queue[0]["path"])
        if self._file == path:
            self._file = self._queue[0]["path"] if self._queue else None
        return {"file": self._file_info() if self._file else None, "queue": self._queue_info()}

    def _queue_info(self):
        out = []
        for q in self._queue:
            final = self._final_path(q["path"])
            out.append({"path": q["path"], "name": os.path.basename(q["path"]), "status": q["status"],
                        "error": q["error"], "final": final if os.path.exists(final) else None,
                        "current": q["path"] == self._file})
        return out

    def refresh_file(self):
        return self._file_info() if self._file else None

    # ---------- settings ----------
    def save_settings(self, settings):
        write_env("CUSTOM_SPLIT_DIR", (settings.get("split") or "").strip())
        write_env("CUSTOM_MERGE_DIR", (settings.get("merge") or "").strip())
        write_env("CUSTOM_OUTPUT_DIR", (settings.get("output") or "").strip())
        return self.get_state()

    def save_api_key(self, key):
        key = (key or "").strip().strip('"').strip("'")
        if not key:
            return {"error": "empty_key"}
        write_env("GEMINI_API_KEY", key)
        return self.get_state()

    def _thinking(self):
        level = (read_env().get("THINKING_LEVEL", "") or DEFAULT_THINKING).strip().lower()
        return level if level in THINKING_LEVELS else DEFAULT_THINKING

    def _flex(self):
        return (read_env().get("FLEX_MODE", "") or "0").strip().lower() in ("1", "true", "yes", "on")

    def save_flex(self, enabled):
        write_env("FLEX_MODE", "1" if enabled else "0")
        return self.get_state()

    def _model(self):
        model = (read_env().get("GEMINI_MODEL", "") or DEFAULT_MODEL).strip()
        return model if model in MODELS else DEFAULT_MODEL

    def save_model(self, model):
        if model not in MODELS:
            return {"error": "bad_model"}
        write_env("GEMINI_MODEL", model)
        return self.get_state()

    def save_thinking(self, level):
        if level not in THINKING_LEVELS:
            return {"error": "bad_thinking"}
        write_env("THINKING_LEVEL", level)
        return self.get_state()

    def _workers(self):
        try:
            value = int(read_env().get("PARALLEL_WORKERS", "") or DEFAULT_WORKERS)
        except ValueError:
            return DEFAULT_WORKERS
        return min(max(value, 1), MAX_WORKERS)

    def save_workers(self, workers):
        try:
            workers = int(workers)
        except (TypeError, ValueError):
            return {"error": "bad_workers"}
        if not 1 <= workers <= MAX_WORKERS:
            return {"error": "bad_workers"}
        write_env("PARALLEL_WORKERS", str(workers))
        return self.get_state()

    def _auto_glossary(self):
        return (read_env().get("AUTO_GLOSSARY", "") or "1").strip().lower() in ("1", "true", "yes", "on")

    def save_auto_glossary(self, enabled):
        write_env("AUTO_GLOSSARY", "1" if enabled else "0")
        return self.get_state()

    def _instructions(self):
        try:
            with open(INSTRUCTIONS_FILE, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def save_instructions(self, text):
        with open(INSTRUCTIONS_FILE, "w", encoding="utf-8") as f:
            f.write((text or "").strip() + "\n")
        return self.get_state()

    def _env_flag(self, key, default):
        value = (read_env().get(key, "") or "").strip().lower()
        return default if not value else value in ("1", "true", "yes", "on")

    def _source_lang(self):
        lang = (read_env().get("SOURCE_LANG", "") or "auto").strip()
        return lang if lang in LANGUAGES else "auto"

    def _target_lang(self):
        lang = (read_env().get("TARGET_LANG", "") or "he").strip()
        return lang if lang in LANGUAGES else "he"

    def _naming(self):
        mode = (read_env().get("OUTPUT_NAMING", "") or "lang").strip()
        return mode if mode in NAMING_MODES else "lang"

    def save_translation_prefs(self, prefs):
        source, target = prefs.get("source_lang", "auto"), prefs.get("target_lang", "he")
        naming = prefs.get("naming", "lang")
        if (source != "auto" and source not in LANGUAGES) or target not in LANGUAGES or naming not in NAMING_MODES:
            return {"error": "bad_lang"}
        if source == target:
            return {"error": "same_lang"}
        write_env("SOURCE_LANG", source)
        write_env("TARGET_LANG", target)
        write_env("CONVERT_UNITS", "1" if prefs.get("convert_units", True) else "0")
        write_env("OUTPUT_NAMING", naming)
        write_env("SUBTITLE_BOM", "1" if prefs.get("bom") else "0")
        state = self.get_state()
        return state

    def save_ui_prefs(self, lang, theme):
        write_env("UI_LANG", lang)
        write_env("UI_THEME", theme)
        return True

    # ---------- shell helpers ----------
    def open_path(self, path):
        if path and os.path.exists(path):
            open_in_explorer(path)
            return True
        return False

    def open_url(self, url):
        import webbrowser
        webbrowser.open(url)

    # ---------- jobs ----------
    def _start_job(self, name, target, *args):
        with self._lock:
            if self._busy:
                return {"error": "busy"}
            if not self._file:
                return {"error": "no_file"}
            self._busy = True
            self._cancel.clear()
            self._usage_base = {}
            self._usage_total = {}

        def runner():
            stream = LogStream(self._log)
            ok = False
            result = {}
            cancelled = False
            try:
                with contextlib.redirect_stdout(stream):
                    result = target(*args) or {}
                    stream.flush()
                ok = True
            except Cancelled:
                stream.flush()
                cancelled = True
                self._log("Cancelled. Finished parts are kept - run again to continue.", "warn")
                result = {"cancelled": True}
            except Exception as e:
                stream.flush()
                self._log(str(e), "error")
                result = {"error_message": str(e)}
            finally:
                self._busy = False
                self._send("done", job=name, ok=ok and not result.get("failed"), cancelled=cancelled,
                           result=result, file=self._file_info() if self._file else None,
                           queue=self._queue_info())

        self._send("started", job=name)
        threading.Thread(target=runner, daemon=True).start()
        return {"ok": True}

    def cancel_job(self):
        if self._busy:
            self._cancel.set()
            self._log("Cancelling - waiting for requests in flight to finish...", "warn")
        return True

    def _do_split(self, chunk_size):
        split_dir = self._dirs()["split"]
        self._send("stage", stage="split", status="active")
        prepare_directory(split_dir, clear=True)
        parts = split_srt_file(self._file, split_dir, chunk_size)
        self._log(f"Split into {parts} parts -> {split_dir}", "success")
        self._send("stage", stage="split", status="done")
        return {"parts": parts}

    @staticmethod
    def _add_usage(a, b):
        return {k: a.get(k, 0) + b.get(k, 0) for k in ("input", "output", "thinking", "cost", "calls")}

    def _send_usage(self, engine):
        self._send("usage", **self._add_usage(self._usage_base, engine.usage))

    def _engine(self, api_key):
        return TranslationEngine(api_key=api_key, model=self._model(), thinking_level=self._thinking(),
                                 flex=self._flex(), source_lang=self._source_lang(),
                                 target_lang=self._target_lang(),
                                 convert_units=self._env_flag("CONVERT_UNITS", True),
                                 instructions=self._instructions(), cancel_event=self._cancel)

    def _do_translate(self, api_key):
        dirs = self._dirs()
        if self._count_srt(dirs["split"]) == 0:
            raise ValueError(f"No split files found in {dirs['split']}. Run the split step first.")
        self._send("stage", stage="translate", status="active")
        # Not cleared: parts translated by an earlier (failed / cancelled) run are reused
        prepare_directory(dirs["merge"], clear=False)

        def progress(current, total, filename):
            self._send("progress", stage="translate", current=current, total=total, filename=filename)
            self._send_usage(engine)

        engine = self._engine(api_key)
        try:
            if self._auto_glossary():
                try:
                    glossary = load_or_build_glossary(engine, parse_srt(self._file), dirs["merge"])
                    engine.set_glossary(glossary)
                    sys.stdout.flush()
                    for line in glossary.splitlines():
                        self._log(f"  {line}", "dim")
                except Cancelled:
                    raise
                except Exception as e:
                    print(f"Warning - character / term analysis failed, translating without it: {e}")
            ok, summary = translate_directory(dirs["split"], dirs["merge"], engine,
                                              progress_callback=progress, workers=self._workers())
        finally:
            self._send_usage(engine)
            self._usage_total = self._add_usage(self._usage_base, engine.usage)
        if not ok:
            self._send("stage", stage="translate", status="error")
            raise RuntimeError("Some parts failed translation. Run again to retry only the failed parts.")
        self._log("All parts translated and validated.", "success")
        self._send("stage", stage="translate", status="done")
        return {"usage": engine.usage, "warnings": summary.get("warnings", [])}

    def _do_merge(self):
        dirs = self._dirs()
        files = list_srt_parts(dirs["merge"])
        if not files:
            raise ValueError(f"No translated files found in {dirs['merge']}. Run the translation step first.")
        self._send("stage", stage="merge", status="active")
        prepare_directory(dirs["output"], clear=False)
        final = self._final_path()
        count = merge_srt_files(files, final, rtl_fix=self._target_lang() in RTL_LANGUAGES,
                                bom=self._env_flag("SUBTITLE_BOM", False))
        ok, reason = compare_structure(parse_srt(self._file), parse_srt(final))
        if ok:
            self._log(f"Merged {len(files)} files ({count} blocks) -> {final}", "success")
            self._log("Final check passed: every block and timestamp matches the source file.", "success")
        else:
            self._log(f"Merged {len(files)} files ({count} blocks) -> {final}")
            self._log(f"Warning - the merged file doesn't match the source: {reason}", "warn")
        self._run_qa(final)
        self._send("stage", stage="merge", status="done")
        return {"final": final}

    def _run_qa(self, final):
        issues = qa.check(parse_srt(self._file), parse_srt(final), self._target_lang(), looks_translated)
        counts = qa.summarize(issues)
        if issues:
            parts = ", ".join(f"{n} {kind}" for kind, n in counts.items())
            self._log(f"Quality check: {len(issues)} issue(s) - {parts}.", "warn")
        else:
            self._log("Quality check: no issues found.", "success")
        fixable = sum(1 for i in issues if i["kind"] in qa.FIXABLE)
        self._send("qa", counts=counts, issues=issues[:200], total=len(issues), fixable=fixable)
        return issues

    def _do_fix(self, api_key):
        final = self._final_path()
        if not os.path.exists(final):
            raise ValueError("No translated file yet. Run the pipeline first.")
        issues = qa.check(parse_srt(self._file), parse_srt(final), self._target_lang(), looks_translated)
        fixable = [i for i in issues if i["kind"] in qa.FIXABLE]
        if not fixable:
            self._log("Nothing to fix automatically.", "success")
            return {}
        print(f"Re-translating {len({i['index'] for i in fixable})} block(s) flagged by the quality check...")
        engine = self._engine(api_key)
        glossary_path = os.path.join(self._dirs()["merge"], "glossary.txt")
        if self._auto_glossary() and os.path.exists(glossary_path):
            with open(glossary_path, encoding="utf-8") as f:
                engine.set_glossary(f.read().partition("\n")[2].strip())
        try:
            replaced = qa.fix_issues(engine, self._file, final, fixable,
                                     rtl_fix=self._target_lang() in RTL_LANGUAGES,
                                     bom=self._env_flag("SUBTITLE_BOM", False))
        finally:
            self._send("usage", **engine.usage)
        self._log(f"Replaced {replaced} block(s) in {final}", "success")
        self._run_qa(final)
        return {"final": final}

    def run_fix(self):
        key = self._api_key()
        if not key:
            return {"error": "no_key"}
        return self._start_job("fix", self._do_fix, key)

    def _do_pipeline(self, chunk_size, api_key):
        self._do_split(chunk_size)
        result = self._do_translate(api_key)
        return {**result, **self._do_merge()}

    def _saved_chunk(self):
        try:
            value = int(read_env().get("CHUNK_SIZE", "") or DEFAULT_CHUNK)
            return value if value > 0 else DEFAULT_CHUNK
        except ValueError:
            return DEFAULT_CHUNK

    def _check_chunk(self, chunk_size):
        try:
            chunk_size = int(chunk_size)
            if chunk_size <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return None
        # Remember the last chunk size used
        if chunk_size != self._saved_chunk() or "CHUNK_SIZE" not in read_env():
            write_env("CHUNK_SIZE", str(chunk_size))
        return chunk_size

    def _do_batch(self, chunk_size, api_key):
        total = len(self._queue)
        for q in self._queue:
            if q["status"] != "done":
                q["status"], q["error"] = "pending", ""
        for i, q in enumerate(self._queue):
            if self._cancel.is_set():
                raise Cancelled()
            if q["status"] == "done" and os.path.exists(self._final_path(q["path"])):
                continue
            self._file = q["path"]
            q["status"] = "running"
            self._send("batch", current=i + 1, total=total, name=os.path.basename(q["path"]),
                       file=self._file_info(), queue=self._queue_info())
            self._log(f"[{i + 1}/{total}] {os.path.basename(q['path'])}", "head")
            try:
                self._do_pipeline(chunk_size, api_key)
                q["status"] = "done"
            except Cancelled:
                q["status"] = "pending"
                raise
            except Exception as e:
                sys.stdout.flush()
                q["status"], q["error"] = "failed", str(e)
                self._log(f"{os.path.basename(q['path'])}: {e}", "error")
            finally:
                self._usage_base = self._usage_total or self._usage_base
                self._send("queue", queue=self._queue_info())
        done = sum(q["status"] == "done" for q in self._queue)
        failed = [os.path.basename(q["path"]) for q in self._queue if q["status"] == "failed"]
        u = self._usage_base
        if u:
            self._log(f"Batch total: {u.get('calls', 0)} call(s), ${u.get('cost', 0):.4f}")
        if failed:
            self._log(f"{done}/{total} files translated. Failed: {', '.join(failed)} - run again to retry them.", "error")
            return {"failed": failed}
        self._log(f"All {total} files translated.", "success")
        return {"batch": total}

    def run_pipeline(self, chunk_size):
        chunk = self._check_chunk(chunk_size)
        if not chunk:
            return {"error": "bad_chunk"}
        key = self._api_key()
        if not key:
            return {"error": "no_key"}
        if len(self._queue) > 1:
            return self._start_job("batch", self._do_batch, chunk, key)
        return self._start_job("pipeline", self._do_pipeline, chunk, key)

    def run_split(self, chunk_size):
        chunk = self._check_chunk(chunk_size)
        if not chunk:
            return {"error": "bad_chunk"}
        return self._start_job("split", self._do_split, chunk)

    def run_translate(self):
        key = self._api_key()
        if not key:
            return {"error": "no_key"}
        return self._start_job("translate", self._do_translate, key)

    def run_merge(self):
        return self._start_job("merge", self._do_merge)


def main():
    api = Api()
    window = webview.create_window(
        APP_NAME,
        url=os.path.join(WEB_DIR, "index.html"),
        js_api=api,
        width=1100,
        height=760,
        min_size=(880, 620),
        background_color="#0f1115",
    )
    api._window = window

    def bind_drag_and_drop():
        # Drag & drop with real file paths (pywebview >= 5)
        try:
            from webview.dom import DOMEventHandler

            def on_drop(e):
                files = (e.get("dataTransfer") or {}).get("files") or []
                paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
                result = api.set_files(paths) if paths else {"error": "file_not_found"}
                api._send("file_dropped", result=result)

            window.dom.document.events.dragenter += DOMEventHandler(lambda e: None, True, True)
            window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True, debounce=500)
            window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        except Exception:
            traceback.print_exc()

    icon = os.path.join(RESOURCE_DIR, "icon.ico")
    webview.start(bind_drag_and_drop, icon=icon if os.path.exists(icon) else None)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        with open(os.path.join(BASE_DIR, "app_error.log"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise
