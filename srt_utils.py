import os
import re

class SRTBlock:
    def __init__(self, index, timestamp, text):
        self.index = index
        self.timestamp = timestamp
        self.text = text

    def to_string(self):
        return f"{self.index}\n{self.timestamp}\n{self.text}"

def parse_srt_text(content):
    """Parse SRT content from a string into a list of SRTBlock objects."""
    blocks = []
    content = content.replace('\r\n', '\n').replace('\r', '\n').lstrip('\ufeff')
    raw_blocks = re.split(r'\n\s*\n', content.strip())
    for raw_block in raw_blocks:
        lines = raw_block.strip().split('\n')
        if len(lines) >= 3:
            index_line = lines[0].strip()
            timestamp_line = lines[1].strip()
            text_lines = '\n'.join(lines[2:]).strip()

            if '-->' in timestamp_line:
                blocks.append(SRTBlock(index_line, timestamp_line, text_lines))
    return blocks

def parse_srt(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            return parse_srt_text(f.read())
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return []

def validate_translation(original_blocks, translated_blocks):
    """
    Checks that a translated SRT matches the original structure:
    same number of blocks, same indices and same timestamps, in the same order.
    Returns (True, "") if valid, otherwise (False, reason).
    """
    if not translated_blocks:
        return False, "Translated output contains no valid SRT blocks."

    if len(original_blocks) != len(translated_blocks):
        orig_idx = [b.index for b in original_blocks]
        trans_idx = set(b.index for b in translated_blocks)
        missing = [i for i in orig_idx if i not in trans_idx]
        reason = f"Block count mismatch: original {len(original_blocks)}, translated {len(translated_blocks)}."
        if missing:
            reason += f" Missing indices: {', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}"
        return False, reason

    norm = lambda t: re.sub(r'\s+', ' ', t).strip()
    for orig, trans in zip(original_blocks, translated_blocks):
        if orig.index != trans.index:
            return False, f"Index mismatch: expected {orig.index}, got {trans.index}."
        if norm(orig.timestamp) != norm(trans.timestamp):
            return False, f"Timestamp mismatch at block {orig.index}: expected '{orig.timestamp}', got '{trans.timestamp}'."
        if not trans.text.strip():
            return False, f"Empty text at block {orig.index}."

    return True, ""

def split_srt_file(file_path, output_dir, chunk_size=300):
    blocks = parse_srt(file_path)
    if not blocks:
        raise ValueError("Failed to parse the SRT file or the file is empty.")

    base_name = os.path.basename(file_path)
    name_without_ext = os.path.splitext(base_name)[0]

    total_blocks = len(blocks)
    part_num = 1
    
    for i in range(0, total_blocks, chunk_size):
        chunk_blocks = blocks[i:i + chunk_size]
        output_filename = os.path.join(output_dir, f"{name_without_ext}_part_{part_num}.srt")
        
        with open(output_filename, 'w', encoding='utf-8') as f:
            for block in chunk_blocks:
                f.write(f"{block.index}\n")
                f.write(f"{block.timestamp}\n")
                f.write(f"{block.text}\n\n")
        
        part_num += 1
    
    return part_num - 1

def extract_part_number(filename):
    match = re.search(r'part_?(\d+)', filename, re.IGNORECASE)
    if match:
        return int(match.group(1))
    numbers = re.findall(r'\d+', filename)
    if numbers:
        return int(numbers[-1])
    return 0

def merge_srt_files(file_paths, output_file):
    # Sort files based on part number
    file_paths.sort(key=lambda x: extract_part_number(os.path.basename(x)))
    
    all_blocks = []
    for file_path in file_paths:
        blocks = parse_srt(file_path)
        all_blocks.extend(blocks)

    if not all_blocks:
        raise ValueError("No valid subtitle blocks found in the selected files.")

    try:
        all_blocks.sort(key=lambda b: int(b.index))
    except ValueError:
        pass

    with open(output_file, 'w', encoding='utf-8') as f:
        for block in all_blocks:
            f.write(f"{block.index}\n")
            f.write(f"{block.timestamp}\n")
            f.write(f"{block.text}\n\n")
            
    return len(all_blocks)
