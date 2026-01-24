"""Unit tests for tri-model validation normalization."""

import unittest
from config.tri_model_config import normalize_validation_result


class TestValidationNormalization(unittest.TestCase):
    """Test validation result normalization for backwards compatibility."""

    def test_normalize_tuple_valid(self):
        """Test normalization of valid tuple result."""
        result = (True, None)
        normalized = normalize_validation_result(result)

        self.assertIsInstance(normalized, dict)
        self.assertTrue(normalized["valid"])
        self.assertEqual(normalized["errors"], [])
        self.assertIsNone(normalized["details"])

    def test_normalize_tuple_invalid_with_message(self):
        """Test normalization of invalid tuple result with error message."""
        result = (False, "No API key configured")
        normalized = normalize_validation_result(result)

        self.assertIsInstance(normalized, dict)
        self.assertFalse(normalized["valid"])
        self.assertEqual(normalized["errors"], ["No API key configured"])
        self.assertEqual(normalized["details"], "No API key configured")

    def test_normalize_tuple_invalid_without_message(self):
        """Test normalization of invalid tuple result without error message."""
        result = (False, None)
        normalized = normalize_validation_result(result)

        self.assertIsInstance(normalized, dict)
        self.assertFalse(normalized["valid"])
        self.assertEqual(normalized["errors"], [])
        self.assertIsNone(normalized["details"])

    def test_normalize_dict_complete(self):
        """Test normalization of complete dict result."""
        result = {
            "valid": False,
            "errors": ["Error 1", "Error 2"],
            "details": "2 errors found"
        }
        normalized = normalize_validation_result(result)

        self.assertIsInstance(normalized, dict)
        self.assertFalse(normalized["valid"])
        self.assertEqual(normalized["errors"], ["Error 1", "Error 2"])
        self.assertEqual(normalized["details"], "2 errors found")

    def test_normalize_dict_partial(self):
        """Test normalization of partial dict result (missing keys)."""
        result = {"valid": True}
        normalized = normalize_validation_result(result)

        self.assertIsInstance(normalized, dict)
        self.assertTrue(normalized["valid"])
        self.assertEqual(normalized["errors"], [])
        self.assertIsNone(normalized["details"])

    def test_normalize_dict_empty(self):
        """Test normalization of empty dict."""
        result = {}
        normalized = normalize_validation_result(result)

        self.assertIsInstance(normalized, dict)
        self.assertFalse(normalized["valid"])  # Default to False if missing
        self.assertEqual(normalized["errors"], [])
        self.assertIsNone(normalized["details"])

    def test_normalize_invalid_type(self):
        """Test normalization of invalid result type."""
        result = "invalid"
        normalized = normalize_validation_result(result)

        self.assertIsInstance(normalized, dict)
        self.assertFalse(normalized["valid"])
        self.assertEqual(normalized["errors"], ["Invalid validation result type"])
        self.assertIsNone(normalized["details"])

    def test_normalize_list_type(self):
        """Test normalization of list type (invalid)."""
        result = [True, "error"]
        normalized = normalize_validation_result(result)

        self.assertIsInstance(normalized, dict)
        self.assertFalse(normalized["valid"])
        self.assertEqual(normalized["errors"], ["Invalid validation result type"])


if __name__ == "__main__":
    unittest.main()
