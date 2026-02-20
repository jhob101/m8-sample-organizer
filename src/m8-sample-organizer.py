import argparse
import logging
import os
import re
import subprocess
import sys
import yaml
import pathlib
from tqdm import tqdm

parser = argparse.ArgumentParser(description="Organize and convert samples for the M8 tracker.")
parser.add_argument("-c", "--config", default="config.yml", help="path to config file (default: config.yml)")
args = parser.parse_args()

logging.basicConfig(
    filename="error.log",
    level=logging.ERROR,
    format="%(asctime)s %(message)s",
)

with open(args.config, "r") as f:
    config = yaml.safe_load(f)


SRC_FOLDER = config["SRC_FOLDER"]
DEST_FOLDER = config["DEST_FOLDER"]
FFMPEG_PATH = config["FFMPEG_PATH"]
TARGET_BIT_DEPTH = config["TARGET_BIT_DEPTH"]
FILE_TYPES = config["FILE_TYPES"]
SPLIT_PUNCTUATION = config["SPLIT_PUNCTUATION"]
FILL_PUNCTUATION = config["FILL_PUNCTUATION"]
STRIKE_WORDS = [word.lower() for word in (config["STRIKE_WORDS"] or [])]
JOIN_SEP = config["JOIN_SEP"]
WORD_FORMAT = config["WORD_FORMAT"]
DUPES_ELIMINATE_PATH = config["DUPES_ELIMINATE_PATH"]
SKIP_EXISTING = config["SKIP_EXISTING"]
MAX_FILE_LENGTH = config["MAX_FILE_LENGTH"]
MAX_DIR_LENGTH = config["MAX_DIR_LENGTH"]
MAX_OUTPUT_LENGTH = config["MAX_OUTPUT_LENGTH"]

# Load phrase replacements (case-insensitive)
PHRASE_REPLACEMENTS = {}
if config.get("PHRASE_REPLACEMENTS"):
    for original, replacement in config["PHRASE_REPLACEMENTS"].items():
        PHRASE_REPLACEMENTS[original.lower()] = replacement

def get_files_by_type(folder, file_types=None):
    # Initialize an empty list to store the file paths
    file_paths = []

    # Iterate through the files in the folder
    for root, dirs, files in os.walk(folder):
        for file in files:
            if file_types:
                # Get the file extension
                file_ext = file.split(".")[-1].lower()
                # If the file extension is in the list of file types, add the file path to the list
                if file_ext not in file_types:
                    continue

            file_path = os.path.join(root, file)
            file_paths.append(file_path)

    return file_paths

def strip_path_prefix(path, prefix):
    # Ensure that the prefix ends with a separator
    if not prefix.endswith(os.sep):
        prefix = prefix + os.sep

    # Check if the path starts with the prefix
    if path.startswith(prefix):
        # Strip the prefix from the path and return the remainder
        return path[len(prefix):]
    else:
        # Return the original path if it doesn't start with the prefix
        return path

def shorten_path(path):
    (root, ext,) = os.path.splitext(path) 

    # Apply phrase replacements to the entire path early in the process
    root = apply_phrase_replacements(root)

    # Clean up the punctuation
    for c in SPLIT_PUNCTUATION:
        root = root.replace(c, " ")

    for c in FILL_PUNCTUATION:
        root = root.replace(c, "")

    path = root + ext

    # Split the path into a list of parts (i.e., folders)
    parts = path.split(os.sep)

    # Create a set to store the unique words
    unique_words = set()

    if len(parts) == 1:
        # File directly in SRC_FOLDER with no subdirectory
        file = file_to_wav(clean_file(parts[0], unique_words))
        return file

    pack = parts[0]
    path = parts[1:-1]
    file = parts[-1]

    pack = clean_folder(pack, unique_words)
    path = clean_path(path, unique_words)
    file = file_to_wav(clean_file(file, unique_words))

    # Join the parts back together
    parts = [pack, path, file]
    path = os.sep.join([part for part in parts if part])
    return path

def apply_phrase_replacements(text):
    """Apply phrase replacements to shorten common terms."""
    if not PHRASE_REPLACEMENTS:
        return text
    
    # Sort replacements by length (longest first) to avoid partial replacements
    sorted_replacements = sorted(PHRASE_REPLACEMENTS.items(), key=lambda x: len(x[0]), reverse=True)
    
    text_lower = text.lower()
    result = text
    
    for original, replacement in sorted_replacements:
        # Case-insensitive replacement while preserving original case pattern
        if original in text_lower:
            # Find all occurrences and replace them
            start = 0
            while True:
                pos = text_lower.find(original, start)
                if pos == -1:
                    break
                
                # Replace in the result string
                before = result[:pos]
                after = result[pos + len(original):]
                result = before + replacement + after
                
                # Update text_lower to reflect the change
                text_lower = result.lower()
                start = pos + len(replacement)
    
    return result

def clean_folder(folder, unique_words):
    words = folder.split()

    words = remove_strike_words(words)

    words_deduped = remove_dupe_words(words, unique_words)

    if DUPES_ELIMINATE_PATH:
        # always use the deduped word list
        words = words_deduped
    elif words_deduped:
        # only use the deduped word list if it doesn't eliminate the path
        words = words_deduped
    
    words = [format_word(word) for word in words]

    return JOIN_SEP.join(words)[:MAX_DIR_LENGTH]
    
def clean_path(path, unique_words):
    for i, folder in enumerate(path):
        path[i] = clean_folder(folder, unique_words)

    path = [folder for folder in path if re.sub(r"\s+", "", folder)]

    return os.sep.join(path)

def clean_file(file, unique_words):
    p = pathlib.Path(file)

    words = p.stem.split()
    words = remove_strike_words(words)
    words_deduped = remove_dupe_words(words, unique_words)
    if words_deduped:
        words = words_deduped
    words = [format_word(word) for word in words]

    return JOIN_SEP.join(words) + p.suffix

def file_to_wav(file):
    return pathlib.Path(file).stem[:MAX_FILE_LENGTH - 4] + ".wav"

def remove_strike_words(words):
    return [word for word in words if not any(word.lower().startswith(prefix) for prefix in STRIKE_WORDS)]

def remove_dupe_words(words, unique_words):
    # Remove duplicate words
    words = [word for word in words if word.lower() not in unique_words]

    # Add the remaining words to the set of unique words
    unique_words.update([word.lower() for word in words])

    # Flip the plurals and add those to our set of unique words
    flipped_plurals = []
    for word in words:
        if word.endswith('s'):
            flipped_plurals.append(word[:-1])
        else:
            flipped_plurals.append(word + 's')

    unique_words.update([word.lower() for word in flipped_plurals])

    return words

def format_word(word):
    if WORD_FORMAT == "lower":
        word = word.lower()
    elif WORD_FORMAT == "upper":
        word = word.upper()
    elif WORD_FORMAT == "title":
        word = word.title()
    return word

def extract_numeric_suffix(stem):
    """Extract existing numeric suffix from filename stem. Returns (base_stem, suffix_num) or (stem, None)."""
    # Look for patterns like _01, _02, _1, _2 at the end of the filename
    match = re.search(r'_(\d+)$', stem)
    if match:
        suffix_num = int(match.group(1))
        base_stem = stem[:match.start()]
        return base_stem, suffix_num
    return stem, None

def format_suffix(num):
    """Format numeric suffix with zero padding for single digits."""
    return f"_{num:02d}"

def make_numbered_path(directory, base_stem, num, ext):
    """Build a path with a numeric suffix, respecting MAX_FILE_LENGTH."""
    suffix = format_suffix(num)
    max_stem_length = MAX_FILE_LENGTH - len(ext) - len(suffix)
    if max_stem_length <= 0:
        new_stem = f"{num:02d}"
    else:
        new_stem = base_stem[:max_stem_length] + suffix
    filename = new_stem + ext
    return os.path.join(directory, filename) if directory else filename

def generate_unique_path(original_path, used_paths, collision_index):
    """Generate a unique path by appending numeric suffixes to avoid collisions.

    collision_index is a dict mapping (directory, base_stem, ext) to a dict of
    {suffix_num: path} for O(1) lookups instead of scanning all used_paths.
    """
    path_obj = pathlib.Path(original_path)
    directory = str(path_obj.parent) if path_obj.parent != pathlib.Path('.') else ""
    ext = path_obj.suffix
    base_stem, existing_suffix = extract_numeric_suffix(path_obj.stem)
    key = (directory, base_stem, ext)

    group = collision_index.get(key)

    # No collision — first file with this base stem
    if group is None:
        collision_index[key] = {None: original_path}
        used_paths.add(original_path)
        return original_path

    # Collision detected. If the first entry was unnumbered, renumber it.
    if None in group:
        first_path = group.pop(None)
        used_paths.discard(first_path)
        # Check if the first file had its own numeric suffix
        first_stem = pathlib.Path(first_path).stem
        _, first_suffix = extract_numeric_suffix(first_stem)
        if first_suffix is not None:
            num = first_suffix
        else:
            num = 1
            while num in group:
                num += 1
        new_first = make_numbered_path(directory, base_stem, num, ext)
        group[num] = new_first
        used_paths.add(new_first)

    # Find a number for the current file
    if existing_suffix is not None and existing_suffix not in group:
        num = existing_suffix
    else:
        num = 1
        while num in group:
            num += 1

    new_path = make_numbered_path(directory, base_stem, num, ext)

    # Safety: ensure no exact-path collision (e.g. from truncation)
    while new_path in used_paths and num < 1000:
        num += 1
        new_path = make_numbered_path(directory, base_stem, num, ext)

    group[num] = new_path
    used_paths.add(new_path)
    return new_path

BIT_DEPTH_CODECS = {
    16: 'pcm_s16le',
    24: 'pcm_s24le',
    32: 'pcm_s32le',
}

def convert_audio(ffmpeg_path, input_path, output_path):
    # Create the directories in the output path if they do not exist
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    codec = BIT_DEPTH_CODECS.get(TARGET_BIT_DEPTH, 'pcm_s16le')

    # Construct the FFmpeg command
    command = [ffmpeg_path, '-hide_banner', '-loglevel', 'error', '-y', '-i', input_path, '-acodec', codec, output_path]

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error converting {input_path}: {e}")
        logging.error(f"Failed to convert {input_path} -> {output_path}: {e}")

def main():
    if not os.path.isdir(SRC_FOLDER):
        print(f"Error: SRC_FOLDER does not exist: {SRC_FOLDER}")
        sys.exit(1)

    files = get_files_by_type(SRC_FOLDER, file_types=FILE_TYPES)

    # First pass: compute all output paths before writing any files
    used_output_paths = set()
    collision_index = {}
    file_plan = []

    for src_path in tqdm(files, desc="Scanning", unit="file"):
        relative_path = strip_path_prefix(src_path, SRC_FOLDER)
        short_path = shorten_path(relative_path)

        if len(short_path) >= MAX_OUTPUT_LENGTH:
            tqdm.write(f"Output {short_path} is longer than {MAX_OUTPUT_LENGTH} characters. Edit?")
            while len(short_path) >= MAX_OUTPUT_LENGTH:
                short_path = input(">")

        unique_short_path = generate_unique_path(short_path, used_output_paths, collision_index)

        if unique_short_path != short_path:
            tqdm.write(f"  Collision: {short_path} -> {unique_short_path}")

        file_plan.append((src_path, unique_short_path))

    # Second pass: convert and write files using the final resolved paths
    converted = 0
    skipped = 0
    with tqdm(file_plan, desc="Converting", unit="file") as pbar:
        for src_path, unique_short_path in pbar:
            dest_path = os.path.join(DEST_FOLDER, unique_short_path)

            if SKIP_EXISTING and os.path.exists(dest_path):
                skipped += 1
                continue

            relative_src = strip_path_prefix(src_path, SRC_FOLDER)
            pbar.set_postfix_str(f"{relative_src} -> {unique_short_path}", refresh=True)
            convert_audio(FFMPEG_PATH, src_path, dest_path)
            converted += 1

    print(f"{len(files)} files found, {converted} converted, {skipped} skipped")

if __name__ == "__main__":
    main()
