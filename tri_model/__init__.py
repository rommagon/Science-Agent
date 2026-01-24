"""Tri-model review system for experimental mini-daily runs.

This module implements an experimental multi-model review system:
1. Claude reviews papers
2. Gemini reviews papers
3. GPT evaluates both reviews and produces final decision

Modules:
- prompts: Versioned prompts for all models
- reviewers: Claude and Gemini review implementations
- evaluator: GPT evaluator implementation
- runner: Mini-daily pipeline orchestration
"""

__version__ = "1.0.0"
