"""Text sanitization utilities for tri-model system.

This module provides utilities to sanitize text before sending to LLM APIs,
particularly handling unicode characters that can cause encoding issues.
"""


def sanitize_for_llm(text: str) -> str:
    """Sanitize text for LLM API calls.

    Handles problematic unicode characters that can cause encoding errors:
    - U+2028 (LINE SEPARATOR) - replaced with newline
    - U+2029 (PARAGRAPH SEPARATOR) - replaced with double newline
    - Other control characters - stripped

    Args:
        text: Input text string

    Returns:
        Sanitized text string safe for LLM APIs
    """
    if not text:
        return ""

    # Replace unicode line/paragraph separators with standard newlines
    # These characters (U+2028, U+2029) can cause ascii encoding errors
    text = text.replace('\u2028', '\n')  # LINE SEPARATOR
    text = text.replace('\u2029', '\n\n')  # PARAGRAPH SEPARATOR

    # Normalize other newline variants
    text = text.replace('\r\n', '\n')  # Windows CRLF
    text = text.replace('\r', '\n')    # Old Mac CR

    # Remove other problematic control characters (except tab and newline)
    # Keep: \t (tab), \n (newline)
    # Remove: other control chars (0x00-0x1F except 0x09, 0x0A)
    sanitized = []
    for char in text:
        code = ord(char)
        # Allow printable chars, tab, newline, and standard unicode
        if code >= 0x20 or char in ('\t', '\n'):
            sanitized.append(char)
        # Skip other control characters

    return ''.join(sanitized)


def sanitize_paper_for_review(paper: dict) -> dict:
    """Sanitize a paper dict for LLM review.

    Applies sanitization to all text fields that will be sent to LLM APIs.

    Args:
        paper: Paper dict with title, source, raw_text, etc.

    Returns:
        New dict with sanitized text fields
    """
    sanitized = paper.copy()

    # Sanitize text fields
    if 'title' in sanitized:
        sanitized['title'] = sanitize_for_llm(sanitized['title'])

    if 'raw_text' in sanitized and sanitized['raw_text']:
        sanitized['raw_text'] = sanitize_for_llm(sanitized['raw_text'])

    if 'summary' in sanitized and sanitized['summary']:
        sanitized['summary'] = sanitize_for_llm(sanitized['summary'])

    if 'source' in sanitized:
        sanitized['source'] = sanitize_for_llm(sanitized['source'])

    return sanitized
