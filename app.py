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

from srt_utils import parse_srt, split_srt_file, merge_srt_files
from translator import translate_directory

if getattr(sys, "frozen", False):
    # Running as a PyInstaller EXE: bundled files (web/, icon) are unpacked to a temp
    # folder, while user files (.env, logs) live next to the EXE.
    RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    BASE_DIR = os.path.dirname(sys.executable)
else:
    RESOURCE_DIR = BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ENV_FILE = os.path.join(BASE_DIR, ".env")
WEB_DIR = os.path.join(RESOURCE_DIR, "web")

DEFAULT_DIRS = {"split": "split", "merge": "merge", "output": "translated_file"}
DEFAULT_CHUNK = 150
DEFAULT_THINKING = "low"
THINKING_LEVELS = ("low", "medium", "high")


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

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.emit(line)

    def flush(self):
        if self.buffer.strip():
            self.emit(self.buffer)
        self.buffer = ""


class Api:
    def __init__(self):
        self._window = None
        self._file = None
        self._busy = False
        self._lock = threading.Lock()

    # ---------- plumbing ----------
    def _send(self, event, **data):
        if self._window:
            payload = json.dumps({"event": event, **data}, ensure_ascii=False)
            self._window.evaluate_js(f"window.onPyEvent && window.onPyEvent({payload})")

    def _log(self, message, level="info"):
        self._send("log", message=message, level=level)

    def _dirs(self):
        env = read_env()
        base = os.path.dirname(self._file)
        names = {
            "split": env.get("CUSTOM_SPLIT_DIR", "").strip() or DEFAULT_DIRS["split"],
            "merge": env.get("CUSTOM_MERGE_DIR", "").strip() or DEFAULT_DIRS["merge"],
            "output": env.get("CUSTOM_OUTPUT_DIR", "").strip() or DEFAULT_DIRS["output"],
        }
        return {k: os.path.join(base, v) for k, v in names.items()}

    def _final_path(self):
        name = os.path.splitext(os.path.basename(self._file))[0]
        return os.path.join(self._dirs()["output"], f"{name}_translated.srt")

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
            "file": self._file_info() if self._file else None,
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

    def pick_file(self):
        dialog = getattr(webview, "FileDialog", None)
        dialog_type = dialog.OPEN if dialog else webview.OPEN_DIALOG
        result = self._window.create_file_dialog(
            dialog_type, file_types=("SRT files (*.srt)", "All files (*.*)")
        )
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        return self.set_file(path)

    def set_file(self, path):
        if not path or not os.path.isfile(path):
            return {"error": "file_not_found"}
        if not path.lower().endswith(".srt"):
            return {"error": "not_srt"}
        self._file = path
        info = self._file_info()
        if info["blocks"] == 0:
            return {"error": "empty_srt", "file": info}
        return {"file": info}

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

    def save_thinking(self, level):
        if level not in THINKING_LEVELS:
            return {"error": "bad_thinking"}
        write_env("THINKING_LEVEL", level)
        return self.get_state()

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

        def runner():
            stream = LogStream(self._log)
            ok = False
            result = {}
            try:
                with contextlib.redirect_stdout(stream):
                    result = target(*args) or {}
                    stream.flush()
                ok = True
            except Exception as e:
                stream.flush()
                self._log(str(e), "error")
                result = {"error_message": str(e)}
            finally:
                self._busy = False
                self._send("done", job=name, ok=ok and not result.get("failed"),
                           result=result, file=self._file_info())

        self._send("started", job=name)
        threading.Thread(target=runner, daemon=True).start()
        return {"ok": True}

    def _do_split(self, chunk_size):
        split_dir = self._dirs()["split"]
        self._send("stage", stage="split", status="active")
        prepare_directory(split_dir, clear=True)
        parts = split_srt_file(self._file, split_dir, chunk_size)
        self._log(f"Split into {parts} parts -> {split_dir}", "success")
        self._send("stage", stage="split", status="done")
        return {"parts": parts}

    def _do_translate(self, api_key):
        dirs = self._dirs()
        if self._count_srt(dirs["split"]) == 0:
            raise ValueError(f"No split files found in {dirs['split']}. Run the split step first.")
        self._send("stage", stage="translate", status="active")
        prepare_directory(dirs["merge"], clear=True)

        def progress(current, total, filename):
            self._send("progress", stage="translate", current=current, total=total, filename=filename)

        ok = translate_directory(dirs["split"], dirs["merge"], api_key=api_key,
                                 progress_callback=progress, thinking_level=self._thinking())
        if not ok:
            self._send("stage", stage="translate", status="error")
            raise RuntimeError("Some parts failed translation or validation. See the log above.")
        self._log("All parts translated and validated.", "success")
        self._send("stage", stage="translate", status="done")
        return {}

    def _do_merge(self):
        dirs = self._dirs()
        files = [os.path.join(dirs["merge"], f) for f in os.listdir(dirs["merge"])
                 if f.lower().endswith(".srt")] if os.path.isdir(dirs["merge"]) else []
        if not files:
            raise ValueError(f"No translated files found in {dirs['merge']}. Run the translation step first.")
        self._send("stage", stage="merge", status="active")
        prepare_directory(dirs["output"], clear=False)
        final = self._final_path()
        count = merge_srt_files(files, final)
        self._log(f"Merged {len(files)} files ({count} blocks) -> {final}", "success")
        self._send("stage", stage="merge", status="done")
        return {"final": final}

    def _do_pipeline(self, chunk_size, api_key):
        self._do_split(chunk_size)
        self._do_translate(api_key)
        return self._do_merge()

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

    def run_pipeline(self, chunk_size):
        chunk = self._check_chunk(chunk_size)
        if not chunk:
            return {"error": "bad_chunk"}
        key = self._api_key()
        if not key:
            return {"error": "no_key"}
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
        "AI SRT Translator",
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
                path = files[0].get("pywebviewFullPath") if files else None
                result = api.set_file(path) if path else {"error": "file_not_found"}
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
