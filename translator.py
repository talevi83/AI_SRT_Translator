import os
import time
from google import genai
from google.genai import types

from srt_utils import parse_srt_text, validate_translation

# Text models offered in Settings. Prices: USD per 1M tokens (paid tier, standard),
# from https://ai.google.dev/gemini-api/docs/pricing - thinking tokens are billed as output.
MODELS = {
    "gemini-3.8-flash":      {"input": 0.75, "output": 3.75},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
}
DEFAULT_MODEL = "gemini-3.8-flash"
MODEL = DEFAULT_MODEL  # kept for backwards compatibility
THINKING_LEVELS = ("low", "medium", "high")

# Models that rejected the thinking_level setting during this run
_NO_THINKING_LEVEL = set()

# Flex inference (preview): 50% cheaper, best-effort capacity, can queue for minutes.
# https://ai.google.dev/gemini-api/docs/flex-inference
FLEX_TIMEOUT_MS = 900_000          # recommended client timeout (15 min)
FLEX_BUSY_RETRIES = 3              # 429/503 retries before falling back to standard
FLEX_BACKOFF = (15, 30, 60)        # seconds
_FLEX_UNSUPPORTED = False          # set if the installed SDK doesn't know service_tier


def _is_capacity_error(e):
    msg = str(e).lower()
    return any(k in msg for k in ("503", "429", "unavailable", "resource_exhausted", "overloaded"))


def build_config(system_prompt, thinking_level=None, flex=False):
    """Generation config; thinking_level controls how much the model 'thinks' (and costs)."""
    global _FLEX_UNSUPPORTED
    kwargs = dict(system_instruction=system_prompt, temperature=0.2)
    if thinking_level in THINKING_LEVELS:
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
        except Exception as e:  # older google-genai without thinking_level support
            print(f"Thinking level not supported by this SDK version, using model default ({e})")
    if flex and not _FLEX_UNSUPPORTED:
        try:
            return types.GenerateContentConfig(service_tier="flex", **kwargs)
        except Exception as e:  # older google-genai without service_tier
            _FLEX_UNSUPPORTED = True
            print(f"Flex is not supported by this google-genai version - using standard tier. "
                  f"Run 'pip install -U google-genai' to enable it. ({e})")
    return types.GenerateContentConfig(**kwargs)


def log_usage(response):
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return
    prompt = getattr(usage, "prompt_token_count", None) or 0
    output = getattr(usage, "candidates_token_count", None) or 0
    thoughts = getattr(usage, "thoughts_token_count", None) or 0
    print(f"Tokens - input: {prompt:,} | output: {output:,} | thinking: {thoughts:,}")

def translate_file(client, file_path, output_path, retries=3, delay=5, thinking_level=None, model=DEFAULT_MODEL,
                   flex=False, standard_client=None):
    """
    Reads an SRT file, translates its content using Gemini API,
    and writes the translated content to the output path.
    Includes basic retry logic for network or API errors.
    """
    system_prompt = (
        "תרגם רק את המשפטים הכתובים באנגלית לעברית, "
        "המר כל מידה או משקל לשיטה המטרית (ק\"מ, ק\"ג, צלזיוס וכו'), "
        "ושמור במדויק על הפורמט, מספרי האינדקס וחותמות הזמן המקוריות. "
        "החזר רק את קובץ ה-SRT התקין ללא שום טקסט מקדים, הסבר או Markdown מיותר."
    )

    try:
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
    except Exception as e:
        print(f"Failed to read {file_path}: {e}")
        return False

    original_blocks = parse_srt_text(content)
    if not original_blocks:
        print(f"No valid SRT blocks found in {os.path.basename(file_path)}.")
        return False

    attempt = 0
    use_flex = flex
    busy = 0
    while attempt < retries:
        attempt += 1
        level = None if model in _NO_THINKING_LEVEL else thinking_level
        try:
            chat = (client if use_flex else (standard_client or client)).chats.create(
                model=model,
                config=build_config(system_prompt, level, flex=use_flex),
            )
            response = chat.send_message(content)
            log_usage(response)
            
            # Clean up potential markdown formatting returned by the model
            translated_text = response.text
            if translated_text.startswith("```srt"):
                translated_text = translated_text[6:]
            elif translated_text.startswith("```"):
                translated_text = translated_text[3:]
            if translated_text.endswith("```"):
                translated_text = translated_text[:-3]
            
            translated_text = translated_text.strip() + "\n\n"

            # Verify the model kept every block, index and timestamp intact
            is_valid, reason = validate_translation(original_blocks, parse_srt_text(translated_text))
            if not is_valid:
                raise ValueError(f"Validation failed - {reason}")

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(translated_text)
            
            print(f"Successfully translated: {os.path.basename(file_path)}")
            return True

        except Exception as e:
            # Some models don't accept thinking_level - drop it and retry without using up an attempt
            if level and "thinking" in str(e).lower():
                _NO_THINKING_LEVEL.add(model)
                print(f"{model} doesn't support thinking level '{level}' - using the model default.")
                attempt -= 1
                continue
            # Flex capacity is best-effort: back off, then fall back to the standard tier
            if use_flex and _is_capacity_error(e):
                attempt -= 1
                if busy < FLEX_BUSY_RETRIES:
                    wait = FLEX_BACKOFF[min(busy, len(FLEX_BACKOFF) - 1)]
                    busy += 1
                    print(f"Flex is busy ({e}). Waiting {wait}s (flex retry {busy}/{FLEX_BUSY_RETRIES})...")
                    time.sleep(wait)
                else:
                    use_flex = False
                    print("Flex unavailable - falling back to standard tier for this part (full price).")
                continue
            print(f"Attempt {attempt}/{retries} failed for {os.path.basename(file_path)}: {e}")
            if attempt < retries:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print(f"Max retries reached. Skipping {os.path.basename(file_path)}.")
                return False

def translate_directory(input_dir, output_dir, api_key=None, progress_callback=None, thinking_level=None,
                        model=DEFAULT_MODEL, flex=False):
    """
    Iterates over all SRT files in the input directory,
    translates each, and saves it to the output directory.
    Designed to be modular for a pipeline or GUI.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Initialize client. Requires GEMINI_API_KEY environment variable or direct api_key.
    try:
        key = {"api_key": api_key} if api_key else {}
        standard_client = genai.Client(**key)
        # Flex requests can sit in a queue for minutes - use a long client timeout
        client = genai.Client(http_options={"timeout": FLEX_TIMEOUT_MS}, **key) if flex else standard_client
    except Exception as e:
        print(f"Error initializing Gemini Client: {e}")
        print("Please ensure the GEMINI_API_KEY environment variable is set or passed directly.")
        return False

    files = [f for f in os.listdir(input_dir) if f.lower().endswith('.srt')]
    if not files:
        print(f"No SRT files found in {input_dir}")
        return False

    print(f"Found {len(files)} files to translate. Model: {model}, thinking: {thinking_level or 'model default'}, tier: {'flex' if flex else 'standard'}")

    success_count = 0
    for idx, file_name in enumerate(files):
        input_file = os.path.join(input_dir, file_name)
        output_file = os.path.join(output_dir, file_name)
        
        print(f"Processing: {file_name}")
        if translate_file(client, input_file, output_file, thinking_level=thinking_level, model=model,
                          flex=flex, standard_client=standard_client):
            success_count += 1
            
        if progress_callback:
            progress_callback(idx + 1, len(files), file_name)
            
    print(f"Translation complete. Successfully translated {success_count}/{len(files)} files.")
    if success_count != len(files):
        print(f"{len(files) - success_count} file(s) failed translation or validation.")
        return False
    return True

if __name__ == "__main__":
    # Example execution that can be chained in a pipeline
    INPUT_FOLDER = "split_files"
    OUTPUT_FOLDER = "translated_files"
    
    # Ensures the function is called properly if run as a standalone script
    if os.path.exists(INPUT_FOLDER):
        success = translate_directory(INPUT_FOLDER, OUTPUT_FOLDER)
        if success:
            print("Ready for the Merge step. Call your merge function here.")
    else:
        print(f"Example input directory '{INPUT_FOLDER}' does not exist.")
