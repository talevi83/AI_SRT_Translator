# AI SRT Translator 🎬🤖

**AI SRT Translator** is a smart, automated tool designed to translate subtitle files (.srt) using advanced AI models. 

Unlike traditional word-for-word translators, this tool leverages LLMs to understand context, idioms, and natural phrasing. It ensures high-quality subtitle translations while completely preserving the original timestamps, sequence, and file formatting.

## Key Features
* 🧠 **Context-Aware Translation:** Translates full sentences and context, not just isolated words.
* ⏱️ **Timestamp Preservation:** Keeps your original `.srt` timing and structure exactly intact.
* 🌍 **Multi-Language Support:** Translate to and from multiple languages effortlessly.
* 🚀 **Automated Workflow:** Process entire subtitle files quickly and efficiently.

 ---

A desktop app that translates `.srt` subtitle files from **English to Hebrew** using Google **Gemini** — while keeping every subtitle block, index and timestamp exactly in sync with the original.

>  אפליקציית דסקטופ שמתרגמת קבצי כתוביות SRT מאנגלית לעברית בעזרת Gemini. הקובץ מפוצל לחלקים, כל חלק מתורגם ונבדק שלא חסרים בו בלוקים או שהזמנים לא השתנו, ובסוף הכול מאוחד לקובץ אחד. ממשק בעברית (RTL) ובאנגלית, מצב כהה/בהיר וגרירת קבצים.

---

## Features

- **One-click pipeline** — split → translate → merge, with live progress and log
- **Translation validation** — every translated chunk is checked against the original (block count, indices, timestamps, empty text). A mismatch triggers an automatic retry (up to 3 attempts), and the pipeline stops rather than produce a file with gaps
- **Metric conversion** — miles, pounds, Fahrenheit etc. are converted to metric units during translation
- **Manual mode** — run each step (split / translate / merge) on its own
- **Modern UI** — Hebrew (RTL) and English, dark / light theme, drag & drop, open the result file or folder in one click
- **Settings in the app** — API key, working-folder names and chunk size, all stored locally in `.env`

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

1. Drag an `.srt` file into the window (or click **Choose file**).
2. Pick a chunk size — the number of subtitle blocks sent to Gemini per call (default **150**; smaller chunks = fewer dropped blocks and retries).
3. Click **Run full pipeline**.
4. When it's done, open the translated file straight from the app.

Output is written next to the source file:

```
Movie.srt
split/              ← source chunks      (Movie_part_1.srt, Movie_part_2.srt, …)
merge/              ← translated chunks
translated_file/
  └── Movie_translated.srt
```

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
| `UI_LANG` / `UI_THEME` | Interface language / theme | `he` / `system` |

The list of models (with prices) and the translation prompt are defined in `translator.py`.

## Project structure

```
app.py             # pywebview window + Python API exposed to the UI
srt_utils.py       # SRT parsing, splitting, merging and translation validation
translator.py      # Gemini translation with retries
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
- **"Validation failed" retries in the log** — Gemini dropped or merged blocks; the app retries automatically. If it happens a lot, lower the chunk size.
- **Missing key warning** — set the key in Settings, or check that `.env` contains `GEMINI_API_KEY=...`.
