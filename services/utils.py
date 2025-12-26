# services/utils.py
import re
import string
import unicodedata
import zlib
import pathlib
import logging
from functools import partial

# --- IMPORTS FOR PARSING ---
# Assuming Parsers.py is available or you copy the necessary functions here. 
# Since you provided Parsers.py, we rely on it.
from Parsers import (
    parse_docx, 
    parse_pdf, 
    parse_code_or_text, 
    parse_csv, 
    parse_xlsx, 
    parse_image_placeholder, 
    parse_gdoc, 
    get_drive_service
)
from Parsers import _EXTENSION_MAPPING, file_handler, parse_gdoc

logger = logging.getLogger("Utils")

# --- TEXT SPLITTER ---
class RecursiveCharacterSplitter:
    def __init__(self, chunk_size=500, chunk_overlap=50):
        # The logic uses character count, not token count, which is simpler and fine for the current scope.
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = ["\n\n", "\n", ".", "?", "!", " ", ""]

    def _split_text(self, text, separators):
        final_chunks = []
        separator = separators[-1]
        new_separators = []
        for i, sep in enumerate(separators):
            if sep == "": separator = ""; break
            if sep in text:
                separator = sep; new_separators = separators[i + 1:]; break
        
        splits = text.split(separator) if separator else list(text)
        
        good_splits = []
        current_split = ""
        
        for split in splits:
            if not split.strip(): continue

            # Apply overlap logic (simple version)
            segment = split.strip()
            
            # Combine if possible
            if current_split and len(current_split) + len(separator) + len(segment) <= self.chunk_size:
                current_split += separator + segment
            else:
                # If current_split is too long, or we are starting:
                if current_split:
                    good_splits.append(current_split)
                current_split = segment

        if current_split:
            good_splits.append(current_split)

        # Recursive step
        for s in good_splits:
            if len(s) <= self.chunk_size or not new_separators:
                final_chunks.append(s)
            else:
                final_chunks.extend(self._split_text(s, new_separators))
        
        return final_chunks

    def split_text(self, text):
        return self._split_text(text, self.separators)

# --- GIBBERISH CHECKER ---

def is_gibberish(text, min_len=25):
    """
    Simple language-agnostic gibberish detector for document chunks.
    Works across languages and scripts (English, Spanish, Chinese, Arabic, etc.)
    
    Returns True if text is low quality/gibberish.
    """
    if not text or len(text) < min_len:
        return True
    
    # 1. Whitespace check - Real text has word boundaries
    # Catches URLs, hashes, long identifiers (universal across Proto-Indo European languages - any who use spaces)
    space_ratio = text.count(' ') / len(text)
    if space_ratio < 0.05:  # Less than 5% spaces
        return True
    
    # 2. Word repetition - Catches "aaaaa aaaaa" or "--- --- ---"
    # Works for any language with space-separated words
    words = text.split()
    if len(words) >= 3:
        word_counts = {}
        for word in words:
            word_counts[word] = word_counts.get(word, 0) + 1
        
        max_count = max(word_counts.values())
        if max_count / len(words) > 0.4:  # Same word >40% of text
            return True
    
    # 3. Character repetition - Catches "aaaaaaa" or "………"
    # Universal check that works in any script
    max_char_repeat = 1
    current_repeat = 1
    for i in range(1, len(text)):
        if text[i] == text[i-1] and text[i] not in ' \n\t':
            current_repeat += 1
            max_char_repeat = max(max_char_repeat, current_repeat)
        else:
            current_repeat = 1
    
    if max_char_repeat > 10:  # Same character repeated >10 times
        return True
    
    # 4. Compression ratio - Works across all languages and scripts!
    # Natural language has patterns; random gibberish doesn't compress well
    # Repetitive junk compresses too well
    try:
        compressed = zlib.compress(text.encode('utf-8', 'ignore'), level=9)
        ratio = len(compressed) / len(text)
        
        if ratio < 0.1:  # Too repetitive
            return True
        
        if len(text) > 100 and ratio > 0.9:  # Too random
            return True
    except:
        pass
    
    return False

# --- TEXT PROCESSING WRAPPER ---

def process_text_file(file_path: pathlib.Path, drive_service, config, text_splitter):
    """
    Parses content, chunks, and filters gibberish.
    Returns: list of (index, chunk_text) tuples.
    """
    try:
        text_content = get_text_content(file_path, drive_service, config)

        # 4. Chunk content   
        chunks = text_splitter.split_text(text_content)
        
        # 5. Filter (Prefix removed)
        final_chunks = []
        gibberish_counter = 0
        
        for i, chunk in enumerate(chunks):
            chunk = chunk.lstrip('. ')
            if not is_gibberish(chunk):
                # Just store the raw chunk. 
                # The DB Trigger handles the filename association for search.
                final_chunks.append((i, chunk))
            else:
                gibberish_counter += 1
        
        if gibberish_counter > 0:
            logger.info(f"Removed {gibberish_counter} gibberish chunks from {file_path.name}")
        return final_chunks
    except Exception as e:
        logger.error(f"Error processing file - {file_path.name}: {e}")
        return []

# FOR LLM READING

def get_text_content(file_path: pathlib.Path, drive_service, config) -> str:
    """
    Parses content once for the LLM. No chunking, just the first X characters.
    Returns: full text content, or None/empty string if unreadable.
    """
    # 1. Get Parser
    is_multimodal = config.get('image_model_name') is not None
    use_drive = config.get("use_drive", False)
    handler = file_handler(file_path.suffix, is_multimodal, use_drive, config)
    if not handler:
        logger.warning(f"Unsupported file type: {file_path.name}")
        return ""

    # 2. Get Raw Content
    limit = config.get("max_text_chars", 500000)
    content = handler(file_path, drive_service, limit) if handler == parse_gdoc else handler(file_path, limit)

    if not content or content == " ": # Only text files should be processed here
        logger.warning(f"Did not extract any text: {file_path.name}")
        return ""

    # 3. Clean and Split Content
    # Replace multiple spaces/tabs with one space
    content = re.sub(r'[ \t]+', ' ', content)
    # Limit newlines to max 2 (paragraph break) to remove massive gaps
    content = re.sub(r'\n{3,}', '\n\n', content)
    content = content.strip()
    # Don't forget this!
    return content