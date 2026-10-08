import os
import re

# "00:01:02,345 --> 00:01:04,000" (also tolerates '.' as the millisecond separator and
# trailing position info like "X1:100 X2:200")
TIMESTAMP_RE = re.compile(r'^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}')
INDEX_RE = re.compile(r'^\s*\d+\s*$')

# Right-to-left scripts (Hebrew, Arabic, Persian, ...) and the Unicode marks used to fix them
RTL_CHARS_RE = re.compile(r'[֐-׿؀-ۿݐ-ݿיִ-﷿ﹰ-﻿]')
RLM = '‏'
_BIDI_MARKS_RE = re.compile(r'^[‎‏‪-‮]+|[‎‏‪-‮]+$')


class SRTBlock:
    def __init__(self, index, timestamp, text):
        self.index = index
        self.timestamp = timestamp
        self.text = text

    def to_string(self):
        return f"{self.index}\n{self.timestamp}\n{self.text}"

    def __repr__(self):
        return f"SRTBlock({self.index!r}, {self.timestamp!r}, {self.text!r})"


def decode_srt_bytes(data):
    """Decode subtitle bytes: BOM-marked UTF-8/16, then UTF-8, then Windows code pages."""
    if data.startswith(b'\xef\xbb\xbf'):
        return data[3:].decode('utf-8', errors='replace')
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16', errors='replace')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        pass
    # Hebrew subtitles saved by old Windows tools are usually cp1255; anything else
    # (English, Western European) is cp1252. Both decode almost any byte, so decide by the
    # word shape: Hebrew words are made only of Hebrew letters, while accented Western
    # letters decoded as cp1255 show up as Hebrew letters stuck inside Latin words.
    try:
        text = data.decode('cp1255')
        pure = len(re.findall(r'(?<![A-Za-z])[א-ת]{2,}(?![A-Za-z])', text))
        mixed = len(re.findall(r'[A-Za-z][א-ת]|[א-ת][A-Za-z]', text))
        if pure > mixed:
            return text
    except UnicodeDecodeError:
        pass
    try:
        return data.decode('cp1252')
    except UnicodeDecodeError:
        return data.decode('latin-1')


def read_srt_text(file_path):
    with open(file_path, 'rb') as f:
        return decode_srt_bytes(f.read())


def _is_block_start(lines, i):
    """A block starts with an index line followed by a timestamp line, or a bare timestamp line."""
    if TIMESTAMP_RE.match(lines[i]):
        return True
    return INDEX_RE.match(lines[i]) is not None and i + 1 < len(lines) and TIMESTAMP_RE.match(lines[i + 1]) is not None


def parse_srt_text(content):
    """
    Parse SRT content from a string into a list of SRTBlock objects.

    Line based, so it copes with real-world files: blocks without text, blank lines
    inside a block's text, missing blank lines between blocks and missing index lines.
    """
    content = content.replace('\r\n', '\n').replace('\r', '\n').lstrip('﻿')
    lines = content.split('\n')
    blocks = []
    i, n = 0, len(lines)
    while i < n:
        if not lines[i].strip() or not _is_block_start(lines, i):
            i += 1
            continue
        if TIMESTAMP_RE.match(lines[i]):
            # No index line - continue the numbering
            index = str(int(blocks[-1].index) + 1) if blocks and blocks[-1].index.isdigit() else str(len(blocks) + 1)
            timestamp = lines[i].strip()
            i += 1
        else:
            index = lines[i].strip()
            timestamp = lines[i + 1].strip()
            i += 2
        text_lines = []
        while i < n:
            # A blank line followed by the next block (or a block glued without a blank line) ends the text
            if not lines[i].strip():
                j = i
                while j < n and not lines[j].strip():
                    j += 1
                if j >= n or _is_block_start(lines, j):
                    i = j
                    break
                i = j  # blank line inside the text - drop it, keep reading
                continue
            if text_lines and _is_block_start(lines, i):
                break
            text_lines.append(lines[i].rstrip())
            i += 1
        blocks.append(SRTBlock(index, timestamp, '\n'.join(text_lines).strip()))
    return blocks


def parse_srt(file_path):
    try:
        return parse_srt_text(read_srt_text(file_path))
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return []


def blocks_to_text(blocks):
    return ''.join(f"{b.index}\n{b.timestamp}\n{b.text}\n\n" for b in blocks)


def write_srt(blocks, output_file, bom=False):
    with open(output_file, 'w', encoding='utf-8-sig' if bom else 'utf-8', newline='\n') as f:
        f.write(blocks_to_text(blocks))


def fix_rtl_line(line):
    """
    Wrap a right-to-left line with RLM marks so players that render with a left-to-right
    base direction don't move the punctuation to the wrong end ("שלום." -> ".שלום").
    Idempotent: existing bidi marks at the edges are replaced.
    """
    core = _BIDI_MARKS_RE.sub('', line)
    if not RTL_CHARS_RE.search(core):
        return core
    return f"{RLM}{core}{RLM}"


def fix_rtl_text(text):
    return '\n'.join(fix_rtl_line(line) for line in text.split('\n'))


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
        if orig.text.strip() and not trans.text.strip():
            return False, f"Empty text at block {orig.index}."

    return True, ""


def split_srt_file(file_path, output_dir, chunk_size=300):
    blocks = parse_srt(file_path)
    if not blocks:
        raise ValueError("Failed to parse the SRT file or the file is empty.")

    name_without_ext = os.path.splitext(os.path.basename(file_path))[0]
    part_num = 0
    for i in range(0, len(blocks), chunk_size):
        part_num += 1
        output_filename = os.path.join(output_dir, f"{name_without_ext}_part_{part_num}.srt")
        write_srt(blocks[i:i + chunk_size], output_filename)
    return part_num


def extract_part_number(filename):
    # Use the last "part_N" so a source name that itself contains "part" doesn't confuse the order
    matches = re.findall(r'part_?(\d+)', filename, re.IGNORECASE)
    if matches:
        return int(matches[-1])
    numbers = re.findall(r'\d+', filename)
    if numbers:
        return int(numbers[-1])
    return 0


def list_srt_parts(folder):
    """SRT files in a folder, ordered by part number."""
    if not os.path.isdir(folder):
        return []
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith('.srt')]
    return sorted(files, key=lambda p: (extract_part_number(os.path.basename(p)), os.path.basename(p)))


def merge_srt_files(file_paths, output_file, rtl_fix=False, bom=False):
    file_paths = sorted(file_paths, key=lambda x: extract_part_number(os.path.basename(x)))

    all_blocks = []
    for file_path in file_paths:
        all_blocks.extend(parse_srt(file_path))

    if not all_blocks:
        raise ValueError("No valid subtitle blocks found in the selected files.")

    try:
        all_blocks.sort(key=lambda b: int(b.index))
    except ValueError:
        pass

    if rtl_fix:
        for block in all_blocks:
            block.text = fix_rtl_text(block.text)

    write_srt(all_blocks, output_file, bom=bom)
    return len(all_blocks)


def compare_structure(source_blocks, merged_blocks):
    """Final check of the merged file against the source file (count, indices, timestamps)."""
    return validate_translation(source_blocks, merged_blocks)
