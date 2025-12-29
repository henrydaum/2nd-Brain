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

class RecursiveTokenSplitter:
    def __init__(self, chunk_size=500, chunk_overlap=50):
        import tiktoken
        self.encoder = tiktoken.get_encoding("cl100k_base")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # Regex is safer for separators to avoid edge cases with consecutive splits
        self.separators = ["\n\n", "\n", ".", "?", "!", " ", ""]

    def _token_len(self, text):
        return len(self.encoder.encode(text, disallowed_special=()))

    def split_text(self, text):
        final_chunks = []
        if not text:
            return final_chunks
        
        # 1. Break text into the smallest semantic units possible (atomic segments)
        atomic_segments = self._recursive_split(text, self.separators)
        
        # 2. Merge these small segments into chunks of the correct size
        current_chunk = []
        current_len = 0
        
        for segment in atomic_segments:
            seg_len = self._token_len(segment)
            
            # If a single segment is massive (larger than chunk_size) even after recursion,
            # we accept it as is (or you could force-cut it, but usually bad for semantics).
            if seg_len > self.chunk_size:
                # Flush current buffer
                if current_chunk:
                    final_chunks.append("".join(current_chunk))
                    current_chunk = []
                    current_len = 0
                final_chunks.append(segment)
                continue

            # If adding this segment exceeds the size, finalize the current chunk
            if current_len + seg_len > self.chunk_size:
                final_chunks.append("".join(current_chunk))
                
                # --- OVERLAP LOGIC ---
                # We need to keep the "tail" of the previous chunk to start the new one.
                # We backtrack from the end of current_chunk until we have enough overlap.
                overlap_buffer = []
                overlap_len = 0
                
                # Iterate backwards through the current chunk segments
                for prev_seg in reversed(current_chunk):
                    prev_len = self._token_len(prev_seg)
                    if overlap_len + prev_len > self.chunk_overlap:
                        break
                    overlap_buffer.insert(0, prev_seg)
                    overlap_len += prev_len
                
                current_chunk = overlap_buffer
                current_len = overlap_len

            current_chunk.append(segment)
            current_len += seg_len

        if current_chunk:
            final_chunks.append("".join(current_chunk))
        
        return final_chunks

    def _recursive_split(self, text, separators):
        """Recursively breaks text down into smallest units (sentences/words)."""
        final_segments = []
        separator = separators[0]
        new_separators = separators[1:]
        
        # If no separators left, text is the atomic unit (even if long)
        if not separator:
            return [text] if text else []

        # Split current text
        splits = text.split(separator)
        
        # Re-attach separator to the end of each split (except the last)
        # to preserve punctuation/newlines in the final output.
        for i, s in enumerate(splits):
            if i < len(splits) - 1:
                s += separator
            if not s:
                continue
                
            # If the segment is small enough to be a building block, keep it.
            # If it's still huge, recurse deeper.
            if self._token_len(s) <= self.chunk_size or not new_separators:
                final_segments.append(s)
            else:
                final_segments.extend(self._recursive_split(s, new_separators))
                
        return final_segments

# --- GIBBERISH CHECKER ---

def is_gibberish(text, min_len=25):
    """
    Simple language-agnostic gibberish detector for document chunks.
    Works across languages and scripts (English, Spanish, Chinese, Arabic, etc.)
    
    Returns True if text is low quality/gibberish.
    """
    if not text or len(text) < min_len:
        # logger.warning(f"[Gibberish] Too short: {text}")
        return True
    
    # 1. Whitespace check - Real text has word boundaries
    # Catches URLs, hashes, long identifiers (universal across Proto-Indo European languages - any who use spaces)
    space_ratio = text.count(' ') / len(text)
    if space_ratio < 0.05:  # Less than 5% spaces
        # logger.warning(f"[Gibberish] Too few spaces: {text}")
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
            # logger.warning(f"[Gibberish] Too many word repetitions: {text}")
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
    
    if max_char_repeat > 40:  # Same character repeated >40 times in a row
        # logger.warning(f"[Gibberish] Too many character repetitions: {text}")
        return True
    
    # 4. Compression ratio - Works across all languages and scripts!
    # Natural language has patterns; random gibberish doesn't compress well
    # Repetitive junk compresses too well
    try:
        compressed = zlib.compress(text.encode('utf-8', 'ignore'), level=9)
        ratio = len(compressed) / len(text)
        
        if ratio < 0.1:  # Too repetitive
            # logger.warning(f"[Gibberish] Compresses too well: {text}")
            return True
        
        if len(text) > 100 and ratio > 0.9:  # Too random
            # logger.warning(f"[Gibberish] Compresses too badly: {text}")
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
        gibberish_chunks = []
        
        for i, chunk in enumerate(chunks):
            chunk = chunk.lstrip('. ')
            if not is_gibberish(chunk):
                # Just store the raw chunk. 
                # The DB Trigger handles the filename association for search.
                final_chunks.append((i, chunk))
            else:
                gibberish_chunks.append((chunk))
        
        if len(gibberish_chunks) > 0:
            logger.info(f"Removed {len(gibberish_chunks)} gibberish chunks from {file_path.name}")
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
    is_multimodal = config.get('embed_image_model_name') is not None
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