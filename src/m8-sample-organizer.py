import argparse
import logging
import os
import re
import subprocess
import sys
import yaml
import pathlib

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

def generate_unique_path(original_path, used_paths):
    """Generate a unique path by appending numeric suffixes to avoid collisions."""
    # Check if this exact path is already used
    if original_path not in used_paths:
        # Check if there would be a collision after any potential truncation/processing
        path_obj = pathlib.Path(original_path)
        directory = str(path_obj.parent) if path_obj.parent != pathlib.Path('.') else ""
        stem = path_obj.stem
        ext = path_obj.suffix
        
        # Extract any existing numeric suffix
        base_stem, existing_suffix = extract_numeric_suffix(stem)
        
        # Check if there's any path that would conflict with this base name
        base_collision = False
        for used_path in used_paths:
            used_path_obj = pathlib.Path(used_path)
            if (used_path_obj.parent == path_obj.parent and 
                used_path_obj.suffix == ext):
                used_base_stem, _ = extract_numeric_suffix(used_path_obj.stem)
                if used_base_stem == base_stem:
                    base_collision = True
                    break
        
        if not base_collision:
            used_paths.add(original_path)
            return original_path
    
    # We have a collision, so we need to handle it properly
    path_obj = pathlib.Path(original_path)
    directory = str(path_obj.parent) if path_obj.parent != pathlib.Path('.') else ""
    stem = path_obj.stem
    ext = path_obj.suffix
    
    # Extract the base stem and any existing numeric suffix
    base_stem, existing_suffix = extract_numeric_suffix(stem)
    
    # Find all existing paths with this base stem in the same directory
    collision_group = []
    existing_numbers = set()
    
    for used_path in list(used_paths):
        used_path_obj = pathlib.Path(used_path)
        if (used_path_obj.parent == path_obj.parent and 
            used_path_obj.suffix == ext):
            used_base_stem, used_suffix = extract_numeric_suffix(used_path_obj.stem)
            
            # Check if stems match (ignoring numeric suffixes)
            if used_base_stem == base_stem:
                collision_group.append(used_path)
                if used_suffix is not None:
                    existing_numbers.add(used_suffix)
    
    # If we found existing files in the collision group, we need to renumber them
    if collision_group:
        # Remove the collision group from used_paths temporarily
        for path in collision_group:
            used_paths.discard(path)
        
        # Re-add them with proper numbering, preserving existing numeric suffixes
        for i, path in enumerate(collision_group):
            path_obj_temp = pathlib.Path(path)
            temp_base_stem, temp_existing_suffix = extract_numeric_suffix(path_obj_temp.stem)
            
            if temp_existing_suffix is not None:
                # Keep the existing suffix
                suffix = format_suffix(temp_existing_suffix)
                existing_numbers.add(temp_existing_suffix)
                suffix_num = temp_existing_suffix
            else:
                # Assign a new suffix, finding the next available number
                suffix_num = 1
                while suffix_num in existing_numbers:
                    suffix_num += 1
                suffix = format_suffix(suffix_num)
                existing_numbers.add(suffix_num)

            # Calculate space for the new filename
            max_stem_length = MAX_FILE_LENGTH - len(ext) - len(suffix)

            if max_stem_length <= 0:
                new_stem = f"{suffix_num:02d}"
            else:
                truncated_base = temp_base_stem[:max_stem_length]
                new_stem = truncated_base + suffix
            
            new_filename = new_stem + ext
            new_path = os.path.join(directory, new_filename) if directory else new_filename
            used_paths.add(new_path)
    
    # Now add the current file with the appropriate suffix
    if existing_suffix is not None:
        # Use the existing suffix if it's not already taken
        if existing_suffix not in existing_numbers:
            suffix = format_suffix(existing_suffix)
            next_counter = existing_suffix
        else:
            # Find the next available number
            next_counter = max(existing_numbers) + 1 if existing_numbers else 1
            suffix = format_suffix(next_counter)
    else:
        # Find the next available number
        next_counter = max(existing_numbers) + 1 if existing_numbers else 1
        suffix = format_suffix(next_counter)
    
    # Calculate space for the new filename
    max_stem_length = MAX_FILE_LENGTH - len(ext) - len(suffix)
    
    if max_stem_length <= 0:
        new_stem = f"{next_counter:02d}"
    else:
        truncated_base = base_stem[:max_stem_length]
        new_stem = truncated_base + suffix
    
    new_filename = new_stem + ext
    new_path = os.path.join(directory, new_filename) if directory else new_filename
    
    # Final safety check
    counter = next_counter
    while new_path in used_paths and counter < 1000:
        counter += 1
        suffix = format_suffix(counter)
        max_stem_length = MAX_FILE_LENGTH - len(ext) - len(suffix)
        
        if max_stem_length <= 0:
            new_stem = f"{counter:02d}"
        else:
            truncated_base = base_stem[:max_stem_length]
            new_stem = truncated_base + suffix
        
        new_filename = new_stem + ext
        new_path = os.path.join(directory, new_filename) if directory else new_filename
    
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
    file_plan = []

    for src_path in files:
        relative_path = strip_path_prefix(src_path, SRC_FOLDER)
        short_path = shorten_path(relative_path)

        if len(short_path) >= MAX_OUTPUT_LENGTH:
            print(f"Output {short_path} is longer than {MAX_OUTPUT_LENGTH} characters. Edit?")
            while len(short_path) >= MAX_OUTPUT_LENGTH:
                short_path = input(">")

        unique_short_path = generate_unique_path(short_path, used_output_paths)

        print(f"Input  {relative_path}")
        print(f"Output {unique_short_path}")
        if unique_short_path != short_path:
            print(f"  (collision resolved: {short_path} -> {unique_short_path})")

        file_plan.append((src_path, unique_short_path))

    # Second pass: convert and write files using the final resolved paths
    converted = 0
    for src_path, unique_short_path in file_plan:
        dest_path = os.path.join(DEST_FOLDER, unique_short_path)

        if SKIP_EXISTING and os.path.exists(dest_path):
            continue

        convert_audio(FFMPEG_PATH, src_path, dest_path)
        converted += 1

    print(f"{len(files)} files found, {converted} converted")

if __name__ == "__main__":
    main()
