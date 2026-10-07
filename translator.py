import os
import time
from google import genai
from google.genai import types

from srt_utils import parse_srt_text, validate_translation

def translate_file(client, file_path, output_path, retries=3, delay=5):
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

    for attempt in range(1, retries + 1):
        try:
            chat = client.chats.create(
                model='gemini-3.8-flash',
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.2,
                )
            )
            response = chat.send_message(content)
            
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
            print(f"Attempt {attempt}/{retries} failed for {os.path.basename(file_path)}: {e}")
            if attempt < retries:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print(f"Max retries reached. Skipping {os.path.basename(file_path)}.")
                return False

def translate_directory(input_dir, output_dir, api_key=None, progress_callback=None):
    """
    Iterates over all SRT files in the input directory,
    translates each, and saves it to the output directory.
    Designed to be modular for a pipeline or GUI.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Initialize client. Requires GEMINI_API_KEY environment variable or direct api_key.
    try:
        if api_key:
            client = genai.Client(api_key=api_key)
        else:
            client = genai.Client()
    except Exception as e:
        print(f"Error initializing Gemini Client: {e}")
        print("Please ensure the GEMINI_API_KEY environment variable is set or passed directly.")
        return False

    files = [f for f in os.listdir(input_dir) if f.lower().endswith('.srt')]
    if not files:
        print(f"No SRT files found in {input_dir}")
        return False

    print(f"Found {len(files)} files to translate. Starting translation...")

    success_count = 0
    for idx, file_name in enumerate(files):
        input_file = os.path.join(input_dir, file_name)
        output_file = os.path.join(output_dir, file_name)
        
        print(f"Processing: {file_name}")
        if translate_file(client, input_file, output_file):
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
