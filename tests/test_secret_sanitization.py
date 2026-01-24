"""Tests for API key and secret sanitization.

This test module verifies that sanitize_secret() properly removes
unicode separators and control characters that can cause encoding
errors when secrets are used in HTTP headers.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_sanitize_secret_clean_key():
    """Test that clean API keys pass through unchanged."""
    from config.tri_model_config import sanitize_secret

    # Clean API key (simulated)
    clean_key = "sk-1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    result = sanitize_secret(clean_key)

    assert result == clean_key
    assert len(result) == len(clean_key)
    print("✓ test_sanitize_secret_clean_key passed")


def test_sanitize_secret_unicode_separators():
    """Test that unicode line/paragraph separators are removed."""
    from config.tri_model_config import sanitize_secret

    # API key with unicode separators (U+2028, U+2029)
    dirty_key = "sk-1234\u20285678\u202990ab"
    result = sanitize_secret(dirty_key)

    # Unicode separators should be removed
    assert '\u2028' not in result
    assert '\u2029' not in result

    # Result should only contain printable ASCII
    for char in result:
        assert 32 <= ord(char) <= 126, f"Non-printable char found: {repr(char)}"

    # Key should start with expected prefix
    assert result.startswith("sk-1234")

    print("✓ test_sanitize_secret_unicode_separators passed")


def test_sanitize_secret_control_characters():
    """Test that ASCII control characters are removed."""
    from config.tri_model_config import sanitize_secret

    # API key with control characters
    dirty_key = "sk-\x00abc\x01def\x02ghi"  # NULL, SOH, STX
    result = sanitize_secret(dirty_key)

    # Control characters should be removed
    assert '\x00' not in result
    assert '\x01' not in result
    assert '\x02' not in result

    # Should contain the printable parts
    assert 'sk-' in result
    assert 'abc' in result
    assert 'def' in result
    assert 'ghi' in result

    print("✓ test_sanitize_secret_control_characters passed")


def test_sanitize_secret_whitespace_stripped():
    """Test that leading/trailing whitespace is stripped."""
    from config.tri_model_config import sanitize_secret

    # API key with whitespace
    dirty_key = "  sk-1234567890abcdef  \n"
    result = sanitize_secret(dirty_key)

    # Whitespace should be stripped
    assert not result.startswith(' ')
    assert not result.endswith(' ')
    assert '\n' not in result

    # Core key should be preserved
    assert result == "sk-1234567890abcdef"

    print("✓ test_sanitize_secret_whitespace_stripped passed")


def test_sanitize_secret_tab_and_newline():
    """Test that tabs and newlines are removed (not valid in headers)."""
    from config.tri_model_config import sanitize_secret

    # API key with tab and newline
    dirty_key = "sk-1234\t5678\n90ab"
    result = sanitize_secret(dirty_key)

    # Tab and newline should be removed
    assert '\t' not in result
    assert '\n' not in result

    # Printable parts should remain
    assert 'sk-1234' in result
    assert '5678' in result
    assert '90ab' in result

    print("✓ test_sanitize_secret_tab_and_newline passed")


def test_sanitize_secret_high_unicode():
    """Test that high unicode codepoints (> 126) are removed."""
    from config.tri_model_config import sanitize_secret

    # API key with high unicode characters
    dirty_key = "sk-1234café5678"  # Contains é (U+00E9)
    result = sanitize_secret(dirty_key)

    # High unicode should be removed
    assert 'é' not in result

    # ASCII parts should remain
    assert 'sk-1234' in result
    assert 'caf' in result  # 'é' removed
    assert '5678' in result

    print("✓ test_sanitize_secret_high_unicode passed")


def test_sanitize_secret_none_and_empty():
    """Test that None and empty strings are handled correctly."""
    from config.tri_model_config import sanitize_secret

    # None should return None
    assert sanitize_secret(None) is None

    # Empty string should return None
    assert sanitize_secret("") is None

    # Whitespace-only should return None
    assert sanitize_secret("   ") is None
    assert sanitize_secret("\t\n") is None

    print("✓ test_sanitize_secret_none_and_empty passed")


def test_sanitize_secret_real_world_scenario():
    """Test realistic scenario with multiple issues."""
    from config.tri_model_config import sanitize_secret

    # Simulated API key with multiple problems:
    # - Leading/trailing whitespace
    # - Unicode line separator (U+2028)
    # - Control character
    # - High unicode
    dirty_key = " sk-proj\u2028abc\x01def™ghi "
    result = sanitize_secret(dirty_key)

    # All problematic characters should be removed
    assert '\u2028' not in result
    assert '\x01' not in result
    assert '™' not in result
    assert not result.startswith(' ')
    assert not result.endswith(' ')

    # Clean parts should remain
    assert 'sk-proj' in result
    assert 'abc' in result
    assert 'def' in result
    assert 'ghi' in result

    # Only printable ASCII should remain
    for char in result:
        assert 32 <= ord(char) <= 126

    print("✓ test_sanitize_secret_real_world_scenario passed")


def test_sanitize_secret_preserves_length_info():
    """Test that sanitization doesn't expose key but logs length changes."""
    from config.tri_model_config import sanitize_secret

    # Key with unicode separators
    dirty_key = "sk-1234\u20285678\u202990ab"
    result = sanitize_secret(dirty_key)

    # Result should be shorter (removed 2 unicode chars)
    assert len(result) < len(dirty_key)

    # But should still contain the printable parts
    assert 'sk-1234' in result
    assert '5678' in result
    assert '90ab' in result

    print("✓ test_sanitize_secret_preserves_length_info passed")


if __name__ == "__main__":
    # Run tests
    test_sanitize_secret_clean_key()
    test_sanitize_secret_unicode_separators()
    test_sanitize_secret_control_characters()
    test_sanitize_secret_whitespace_stripped()
    test_sanitize_secret_tab_and_newline()
    test_sanitize_secret_high_unicode()
    test_sanitize_secret_none_and_empty()
    test_sanitize_secret_real_world_scenario()
    test_sanitize_secret_preserves_length_info()
    print("\n✅ All secret sanitization tests passed!")
