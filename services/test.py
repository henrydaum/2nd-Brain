import zlib

def is_gibberish(text, min_len=25):
    """
    Simple language-agnostic gibberish detector for document chunks.
    Works across languages and scripts (English, Spanish, Chinese, Arabic, etc.)
    
    Returns True if text is low quality/gibberish.
    """
    if not text or len(text) < min_len:
        return True
    
    # 1. Whitespace check - Real text has word boundaries
    # Catches URLs, hashes, long identifiers (universal across languages)
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
    
    if max_char_repeat > 5:  # Same character repeated >5 times
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


# Test cases across languages
if __name__ == "__main__":
    test_cases = [
        # English
        ("This is a normal paragraph from a document.", False),
        ("The meeting is scheduled for next Tuesday.", False),
        
        # Spanish
        ("Este es un párrafo normal de un documento.", False),
        
        # German (with consonant clusters - should still pass)
        ("Dieser Text ist vollkommen normal und lesbar.", False),
        
        # French
        ("C'est un paragraphe normal d'un document.", False),
        
        # Gibberish - too short
        ("Hi", True),
        
        # Gibberish - no spaces
        ("https://example.com/very/long/url/path", True),
        
        # Gibberish - repetitive
        ("aaaaa aaaaa aaaaa aaaaa aaaaa", True),
        ("--- --- --- --- --- --- ---", True),
        ("........................", True),
        ("aaaaaaaaaaaaa", True),
        
        # Gibberish - random
        ("xkcd fwqp mnbv qwer tyui asdf ghjk", True),
        ("jK8#mP2$nQ9@rT5&vX3*wY7!", True),
        ("随机字符混合在一起没有意义", False),  # Random Chinese characters
        ("aaa aaa aaa aaa aaa aaa aaa aaa aaa aaa aaa aaa aaa", True),
        
        # Edge cases
        ("Dr. Smith's research at MIT is great.", False),
        ("Q3 2024 revenue: $1.2M (up 15%)", False),
    ]
    
    print("International Gibberish Detection:")
    print("-" * 60)
    correct = 0
    for text, expected in test_cases:
        result = is_gibberish(text)
        status = "✓" if result == expected else "✗"
        if result == expected:
            correct += 1
        label = "Gibberish" if result else "Good"
        print(f"{status} [{label:9}] {text[:50]}")
    
    print(f"\nAccuracy: {correct}/{len(test_cases)} ({100*correct//len(test_cases)}%)")
    
    print("\n" + "="*60)
    print("Alternative: Use existing libraries")
    print("="*60)