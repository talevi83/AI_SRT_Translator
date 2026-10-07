# AI SRT Translator

A desktop app that translates `.srt` subtitle files from **English to Hebrew** using Google **Gemini** — while keeping every subtitle block, index and timestamp exactly in sync with the original.

> **בעברית:** אפליקציית דסקטופ שמתרגמת קבצי כתוביות SRT מאנגלית לעברית בעזרת Gemini. הקובץ מפוצל לחלקים, כל חלק מתורגם ונבדק שלא חסרים בו בלוקים או שהזמנים לא השתנו, ובסוף הכול מאוחד לקובץ אחד. ממשק בעברית (RTL) ובאנגלית, מצב כהה/בהיר וגרירת קבצים.

---

## Features

- **One-click pipeline** — split → translate → merge, with live progress and log
- **Translation validation** — every translated chunk is checked against the original (block count, indices, timestamps, empty text). A mismatch triggers an automatic retry (up to 3 attempts), and the pipeline stops rather than produce a file with gaps
- **Metric conversion** — miles, pounds, Fahrenheit etc. are converted to metric units during translation
- **Manual mode** — run each step (split / translate / merge) on its own
- **Modern UI** — Hebrew (RTL) and English, dark / light theme, drag & drop, open the result file or folder in one click
- **Settings in the app** — API key, working-folder names and chunk size, all stored locally in `.env`

## Requirements

- Windows 10 / 11
- [Python 3.11+](https://www.python.org/downloads/) (added to PATH)
- Microsoft Edge WebView2 Runtime — preinstalled on Windows 11 and most Windows 10 machines
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) with [billing enabled](https://aistudio.google.com/billing) for large files

## Getting started

1. Clone the repo:
   ```
   git clone <repo-url>
   cd AI_SRT_Translator
   ```
2. Double-click **`running_script.bat`**.
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

## Building a standalone EXE

Double-click **`build.bat`**. It installs PyInstaller and packs the whole app into a single
`dist\AI SRT Translator.exe` with the app icon — no Python needed to run it.

- Your `.env` (API key and settings) is copied next to the EXE on the first build; keep it in the same folder.
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
running_script.bat # Creates the venv, installs requirements, launches the app
build.bat          # Builds a standalone EXE with PyInstaller
```

## Troubleshooting

- **The window doesn't open** — check `app_error.log` in the app folder.
- **"Validation failed" retries in the log** — Gemini dropped or merged blocks; the app retries automatically. If it happens a lot, lower the chunk size.
- **Missing key warning** — set the key in Settings, or check that `.env` contains `GEMINI_API_KEY=...`.
