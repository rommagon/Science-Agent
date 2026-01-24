"""Prompts for tri-model review system.

This module contains versioned prompts for:
- Claude reviewer
- Gemini reviewer
- GPT evaluator

All prompts enforce a strict JSON schema for structured output.
"""
from typing import Optional
# Shared review schema documentation
REVIEW_SCHEMA_DOC = """
OUTPUT SCHEMA (strict JSON):
{
  "relevancy_score": <integer 0-100>,
  "relevancy_reason": "<1-3 sentences explaining score>",
  "signals": {
    "cancer_type": "<breast|lung|colon|other|none>",
    "breath_based": <true|false>,
    "sensor_based": <true|false>,
    "animal_model": <true|false>,
    "ngs_genomics": <true|false>,
    "early_detection_focus": <true|false>
  },
  "summary": "<2-3 sentence summary of key findings>",
  "concerns": "<any methodological or relevance concerns, or 'None'>",
  "confidence": "<low|medium|high>"
}
"""

# Claude Reviewer Prompt (v1)
CLAUDE_REVIEW_PROMPT_V1 = """You are a research reviewer for SpotItEarly, analyzing publications for early cancer detection relevance.

**RUBRIC:**
1. CANCER TYPE PRIORITY:
   - Breast cancer: 40 points (highest)
   - Lung cancer: 35 points
   - Colon cancer: 30 points
   - Other cancers: 20 points
   - Non-cancer: 0 points

2. DETECTION METHOD BOOSTS:
   - Breath/VOC/breathomics: +40 points (MAJOR)
   - Sensor-based detection: +20 points
   - Animal model detection: +20 points
   - NGS/genomics: +10 points
   - Early detection/screening: +10 points

3. PENALTIES:
   - Treatment-only (no detection): -20 points
   - Review/meta-analysis (no novel method): -10 points
   - Purely computational: -15 points

**SCORING GUIDELINES:**
- Max 100, Min 0
- >80: Breakthrough relevance
- 60-79: Strong relevance
- 40-59: Moderate relevance
- 20-39: Weak relevance
- 0-19: Irrelevant

**PUBLICATION:**
Title: {title}
Source: {source}
Abstract: {abstract}

{schema_doc}

**IMPORTANT:**
- Respond ONLY with valid JSON
- No markdown, no explanations outside JSON
- Be rigorous and conservative in scoring
"""

# Gemini Reviewer Prompt (v1)
GEMINI_REVIEW_PROMPT_V1 = """You are a research reviewer for SpotItEarly evaluating publications for early cancer detection relevance.

**SCORING RUBRIC:**
1. CANCER TYPE (base points):
   - Breast cancer: 40 pts (top priority)
   - Lung cancer: 35 pts
   - Colon cancer: 30 pts
   - Other cancers: 20 pts
   - Non-cancer topics: 0 pts

2. DETECTION METHODS (additive bonuses):
   - Breath collection/VOC/breathomics: +40 pts (critical boost)
   - Sensor-based detection: +20 pts
   - Animal model detection: +20 pts
   - NGS/genomics: +10 pts
   - Early detection/screening focus: +10 pts

3. DEDUCTIONS:
   - Treatment-only (no detection): -20 pts
   - Review/meta-analysis (no new method): -10 pts
   - Purely computational/database: -15 pts

**SCORE INTERPRETATION:**
- 80-100: Highly relevant breakthrough
- 60-79: Strong relevance to mission
- 40-59: Moderate relevance
- 20-39: Weak relevance
- 0-19: Not relevant

**ANALYZE THIS PUBLICATION:**
Title: {title}
Source: {source}
Abstract/Summary: {abstract}

{schema_doc}

**CRITICAL REQUIREMENTS:**
- Output ONLY valid JSON (no markdown, no extra text)
- Be thorough but conservative in scoring
- Clearly identify both strengths and concerns
"""

# GPT Evaluator Prompt (v1)
GPT_EVALUATOR_PROMPT_V1 = """You are a meta-evaluator analyzing two independent reviews of a cancer research publication.

**YOUR TASK:**
Compare Claude's and Gemini's reviews, then produce a FINAL authoritative decision on relevance to SpotItEarly's mission (early cancer detection).

**INPUTS:**

Publication:
Title: {title}
Source: {source}
Abstract: {abstract}

Claude's Review:
{claude_review}

Gemini's Review:
{gemini_review}

**EVALUATION CRITERIA:**

1. SCORE AGREEMENT:
   - If scores within 10 points: High agreement
   - If scores 11-20 points apart: Moderate agreement
   - If scores >20 points apart: Low agreement (investigate why)

2. SIGNAL CONSISTENCY:
   - Check if both identified same cancer type, breath-based, etc.
   - Flag major inconsistencies

3. REASONING QUALITY:
   - Which reviewer provided more specific evidence?
   - Which identified more relevant technical details?
   - Are there obvious errors or oversights?

4. FINAL DECISION LOGIC:
   - If high agreement: Average scores, merge signals
   - If moderate agreement: Weight toward reviewer with better reasoning
   - If low agreement: Deeply analyze and choose most justified position

**OUTPUT SCHEMA (strict JSON):**
{{
  "final_relevancy_score": <integer 0-100>,
  "final_relevancy_reason": "<2-3 sentences, YOUR authoritative reasoning>",
  "final_signals": {{
    "cancer_type": "<breast|lung|colon|other|none>",
    "breath_based": <true|false>,
    "sensor_based": <true|false>,
    "animal_model": <true|false>,
    "ngs_genomics": <true|false>,
    "early_detection_focus": <true|false>
  }},
  "final_summary": "<2-3 sentence authoritative summary>",
  "agreement_level": "<high|moderate|low>",
  "disagreements": "<list key disagreements, or 'None'>",
  "evaluator_rationale": "<1-2 sentences explaining which reviewer you weighted and why>",
  "confidence": "<low|medium|high>"
}}

**CRITICAL:**
- Output ONLY valid JSON
- Be decisive - don't hedge
- Clearly explain your reasoning
- If one review is clearly better, favor it strongly
"""


def get_claude_prompt(title: str, source: str, abstract: str) -> str:
    """Get Claude reviewer prompt with paper details.

    Args:
        title: Publication title
        source: Source name
        abstract: Abstract text

    Returns:
        Formatted prompt string
    """
    return CLAUDE_REVIEW_PROMPT_V1.format(
        title=title,
        source=source,
        abstract=abstract[:2000],  # Truncate to avoid token limits
        schema_doc=REVIEW_SCHEMA_DOC,
    )


def get_gemini_prompt(title: str, source: str, abstract: str) -> str:
    """Get Gemini reviewer prompt with paper details.

    Args:
        title: Publication title
        source: Source name
        abstract: Abstract text

    Returns:
        Formatted prompt string
    """
    return GEMINI_REVIEW_PROMPT_V1.format(
        title=title,
        source=source,
        abstract=abstract[:2000],  # Truncate to avoid token limits
        schema_doc=REVIEW_SCHEMA_DOC,
    )


def get_gpt_evaluator_prompt(
    title: str,
    source: str,
    abstract: str,
    claude_review: Optional[dict],
    gemini_review: Optional[dict],
) -> str:
    """Get GPT evaluator prompt with paper and reviews.

    Args:
        title: Publication title
        source: Source name
        abstract: Abstract text
        claude_review: Claude's review dict (or None if unavailable)
        gemini_review: Gemini's review dict (or None if unavailable)

    Returns:
        Formatted prompt string
    """
    import json

    # Format reviews as JSON strings
    claude_json = json.dumps(claude_review, indent=2) if claude_review else "UNAVAILABLE (API error or timeout)"
    gemini_json = json.dumps(gemini_review, indent=2) if gemini_review else "UNAVAILABLE (API error or timeout)"

    return GPT_EVALUATOR_PROMPT_V1.format(
        title=title,
        source=source,
        abstract=abstract[:2000],
        claude_review=claude_json,
        gemini_review=gemini_json,
    )
