# AI SRT Translator 🎬🤖

**AI SRT Translator** is a smart, automated tool designed to translate subtitle files (.srt) using advanced AI models. 

Unlike traditional word-for-word translators, this tool leverages LLMs to understand context, idioms, and natural phrasing. It ensures high-quality subtitle translations while completely preserving the original timestamps, sequence, and file formatting.

## Key Features
* 🧠 **Context-Aware Translation:** Translates full sentences and context, not just isolated words.
* ⏱️ **Timestamp Preservation:** Keeps your original `.srt` timing and structure exactly intact.
* 🌍 **Multi-Language Support:** Translate to and from multiple languages effortlessly.
* 🚀 **Automated Workflow:** Process entire subtitle files quickly and efficiently.

 ---

A desktop app (Windows, macOS, Linux) that translates `.srt` subtitle files using Google **Gemini** — Hebrew by default, or any of 19 languages — while keeping every subtitle block, index and timestamp exactly in sync with the original.

>  אפליקציית דסקטופ (Windows, Mac, Linux) שמתרגמת קבצי כתוביות SRT בעזרת Gemini — לעברית כברירת מחדל, או לאחת מ-19 שפות. רק הטקסט נשלח למודל, כך שהזמנים לא משתנים לעולם. לפני התרגום מזוהות הדמויות (כולל מגדר) והמונחים כדי שהתרגום יהיה עקבי, החלקים מתורגמים במקביל, ואם משהו נכשל — הרצה חוזרת ממשיכה מאותה נקודה. בסוף רצה בדיקת איכות עם תיקון אוטומטי. אפשר לתרגם קובץ אחד או עונה שלמה.

---

## Features

- **One-click pipeline** — split → translate → merge, with live progress, log and real cost
- **Timestamps can't break** — only the subtitle text is sent to Gemini (as JSON); indices and timestamps are rebuilt locally. Any item the model drops or leaves untranslated is retried on its own, and the merged file is checked against the source
- **Consistent characters and terms** — an optional first pass lists the characters (with gender and name spelling), recurring terms and tone; every part is translated with it (`glossary.txt`, editable). Each part also gets the neighbouring lines as context. Add your own instructions / glossary in Settings
- **Fast and resumable** — parts are translated in parallel; if a run fails or you cancel it, running again translates only what's missing
- **Quality check** — flags untranslated blocks, broken formatting tags, lines over 42 characters, more than 2 lines and fast reading speed, with an automatic fix that re-translates only the flagged blocks
- **Whole seasons** — drop several files or a folder and translate them in one run
- **Languages** — source (or auto-detect) and target from 19 languages; RTL output gets direction marks so punctuation shows correctly in players
- **Player-friendly output** — `Movie.he.srt` (loaded automatically by VLC / Plex / Kodi), optional UTF-8 BOM; source files in UTF-8/16, cp1252 or cp1255 are read correctly
- **Cost control** — model picker with prices, Flex mode (50% cheaper), thinking level
- **Manual mode** — run each step (split / translate / merge) on its own
- **Modern UI** — Hebrew (RTL) and English, dark / light theme, drag & drop, open the result file or folder in one click

## Requirements

- Windows 10 / 11, macOS 11+ or Linux (desktop)
- [Python 3.11+](https://www.python.org/downloads/) (added to PATH)
- **Windows:** Microsoft Edge WebView2 Runtime — preinstalled on Windows 11 and most Windows 10 machines
- **macOS:** nothing extra (uses the built-in WebKit)
- **Linux:** either the GTK WebKit bindings (`sudo apt install python3-gi gir1.2-webkit2-4.1`, then create the venv with `--system-site-packages`) or nothing — `run.sh` falls back to installing the pip-only Qt backend automatically
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) with [billing enabled](https://aistudio.google.com/billing) for large files

## Getting started

1. Clone the repo:
   ```
   git clone <repo-url>
   cd AI_SRT_Translator
   ```
2. Start the app:
   - **Windows:** double-click **`running_script.bat`**
   - **macOS:** double-click **`run.command`** (or run `./run.sh` in Terminal)
   - **Linux:** run `./run.sh`

   On first run it creates a virtual environment and installs the requirements, then opens the app.
3. Go to **Settings** and paste your Gemini API key
   (or copy `.env.example` to `.env` and fill in `GEMINI_API_KEY`).

## Usage

1. Drag an `.srt` file, several files or a folder into the window (or click **Choose file** / **Choose folder**).
2. Pick a chunk size — the number of subtitle blocks per Gemini call (default **150**).
3. Click **Run full pipeline** (or **Translate N files**).
4. When it's done, review the quality check, fix issues automatically if needed, and open the file straight from the app.

Output is written next to the source file:

```
Movie.srt
split/Movie/        ← source chunks      (Movie_part_1.srt, Movie_part_2.srt, …)
merge/Movie/        ← translated chunks + glossary.txt
translated_file/
  └── Movie.he.srt
```

Set the output folder to `.` in Settings to save `Movie.he.srt` right next to `Movie.srt`.

## Building a standalone app

| OS | Command | Output |
| --- | --- | --- |
| Windows | double-click **`build.bat`** | `dist\AI SRT Translator.exe` |
| macOS | `./build.sh` | `dist/AI SRT Translator.app` |
| Linux | `./build.sh` | `dist/AI SRT Translator` (single binary) |

The build installs PyInstaller and packs the whole app with its icon — no Python needed to run it.
Builds are per-OS: build on the system you want to run on.

- Your `.env` (API key and settings) is copied on the first build. On Windows / Linux it sits next to the
  executable — keep it in the same folder. On macOS the `.app` keeps it in
  `~/Library/Application Support/AI SRT Translator/`.
- The macOS build is not code-signed. On first launch, right-click the app → **Open** to get past Gatekeeper.
- Rebuild after every code change.

## Configuration

All settings live in `.env` (see [`.env.example`](.env.example)) and can be edited from the Settings screen:

| Key | Description | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | Your Gemini API key | — (required) |
| `CUSTOM_SPLIT_DIR` | Folder for source chunks | `split` |
| `CUSTOM_MERGE_DIR` | Folder for translated chunks | `merge` |
| `CUSTOM_OUTPUT_DIR` | Folder for the final file | `translated_file` |
| `CHUNK_SIZE` | Blocks per Gemini call | `150` |
| `GEMINI_MODEL` | `gemini-3.8-flash` / `gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` | `gemini-3.8-flash` |
| `FLEX_MODE` | `1` = [Flex inference](https://ai.google.dev/gemini-api/docs/flex-inference): 50% cheaper, slower, falls back to standard if busy | `0` |
| `THINKING_LEVEL` | Model thinking level: `low` / `medium` / `high` (thinking is billed as output) | `low` |
| `SOURCE_LANG` / `TARGET_LANG` | Language codes (`auto` for source auto-detect) | `auto` / `he` |
| `CONVERT_UNITS` | `1` = convert imperial units to metric | `1` |
| `AUTO_GLOSSARY` | `1` = analyse characters and terms before translating | `1` |
| `PARALLEL_WORKERS` | Parts translated at the same time (1–8) | `3` |
| `OUTPUT_NAMING` | `lang` = `Movie.he.srt`, `suffix` = `Movie_translated.srt` | `lang` |
| `SUBTITLE_BOM` | `1` = save the output with a UTF-8 BOM | `0` |
| `UI_LANG` / `UI_THEME` | Interface language / theme | `he` / `system` |

Your own translation instructions are saved in `instructions.txt` next to `.env`.
The list of models (with prices), languages and the translation prompt are defined in `translator.py`;
quality-check limits in `qa.py`.

## Running the tests

```
pip install -r requirements-dev.txt
pytest
```

## Project structure

```
app.py             # pywebview window + Python API exposed to the UI
srt_utils.py       # SRT parsing (encodings, broken files), splitting, merging, RTL fix
translator.py      # Gemini translation: JSON protocol, glossary, retries, parallel parts, cost
qa.py              # Quality check of the final file and automatic fix
tests/             # pytest suite (no network - Gemini is faked)
web/               # UI: index.html, style.css, app.js, i18n.js (Hebrew / English strings)
running_script.bat # Windows: creates the venv, installs requirements, launches the app
run.sh             # macOS / Linux launcher (run.command = double-clickable on macOS)
build.bat          # Windows: builds a standalone EXE with PyInstaller
build.sh           # macOS / Linux: builds a .app / binary with PyInstaller
```

## Troubleshooting

- **The window doesn't open** — check `app_error.log` in the app folder
  (macOS `.app`: `~/Library/Application Support/AI SRT Translator/`).
- **Linux: "You must have either QT or GTK"** — run `./run.sh` (it installs the Qt backend), or install the GTK packages listed under Requirements.
- **"missing or untranslated" retries in the log** — Gemini skipped some items; the app re-sends just those. If it happens a lot, lower the chunk size.
- **Many "Rate limited" messages** — lower *Parallel requests* in Settings.
- **A run failed or was cancelled** — just run it again; finished parts are reused.
- **Wrong names or genders** — edit `merge/<file>/glossary.txt` (or add instructions in Settings) and run again.
- **Missing key warning** — set the key in Settings, or check that `.env` contains `GEMINI_API_KEY=...`.
