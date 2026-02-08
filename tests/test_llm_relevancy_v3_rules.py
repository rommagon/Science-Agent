"""Tests for deterministic V3 relevancy business rules."""

from unittest.mock import patch


def test_hard_constraint_caps_non_target_non_detection():
    """Score should be capped at 60 without target cancer or detection method link."""
    from mcp_server.llm_relevancy import _apply_v3_business_rules

    item = {
        "title": "Metastatic therapy market outlook in melanoma",
        "raw_text": "This report covers market growth, pricing strategy, and investor trends.",
        "source": "Industry Report",
    }
    parsed = {
        "relevancy_score": 92,
        "relevancy_reason": "High relevance",
        "signals": {"cancer_type": "other"},
    }

    result = _apply_v3_business_rules(item, parsed)
    assert result["relevancy_score"] <= 60
    assert result["signals"]["target_cancer_match"] is False
    assert result["signals"]["detection_methodology"] is False


def test_generic_ai_without_detection_is_debiased():
    """Generic AI mention should not inflate scores when diagnostics link is missing."""
    from mcp_server.llm_relevancy import _apply_v3_business_rules

    item = {
        "title": "AI platform for oncology workflow optimization",
        "raw_text": "Uses deep learning and LLMs for hospital operations and reporting.",
        "source": "Health IT",
    }
    parsed = {
        "relevancy_score": 70,
        "relevancy_reason": "Relevant due to AI",
        "signals": {"cancer_type": "breast"},
    }

    result = _apply_v3_business_rules(item, parsed)
    assert result["relevancy_score"] < 70
    assert result["signals"]["ai_diagnostics_linked"] is False


def test_ai_linked_to_diagnostics_can_receive_bonus():
    """AI tied directly to diagnostics should be allowed a small positive boost."""
    from mcp_server.llm_relevancy import _apply_v3_business_rules

    item = {
        "title": "Deep learning for early lung cancer detection from LDCT screening",
        "raw_text": "AI model improved sensitivity and specificity in screening diagnostics.",
        "source": "Nature Cancer",
    }
    parsed = {
        "relevancy_score": 70,
        "relevancy_reason": "Relevant",
        "signals": {"cancer_type": "lung"},
    }

    result = _apply_v3_business_rules(item, parsed)
    assert result["relevancy_score"] >= 70
    assert result["signals"]["ai_diagnostics_linked"] is True


def test_target_cancer_list_is_configurable():
    """Target cancer matching should respect SPOTITEARLY_TARGET_CANCER_TYPES."""
    from mcp_server.llm_relevancy import _apply_v3_business_rules

    item = {
        "title": "Early ovarian cancer detection panel",
        "raw_text": "Prospective screening diagnostics in high-risk cohorts.",
        "source": "JCO",
    }
    parsed = {
        "relevancy_score": 75,
        "relevancy_reason": "Relevant",
        "signals": {"cancer_type": "ovarian"},
    }

    with patch.dict("os.environ", {"SPOTITEARLY_TARGET_CANCER_TYPES": "ovarian,prostate"}, clear=False):
        result = _apply_v3_business_rules(item, parsed)
        assert result["signals"]["target_cancer_match"] is True


def test_treatment_only_target_cancer_is_capped_low():
    """Target-cancer treatment efficacy studies should not stay highly scored."""
    from mcp_server.llm_relevancy import _apply_v3_business_rules

    item = {
        "title": "Clinical value of ultrasound in predicting neoadjuvant chemotherapy efficacy for breast cancer",
        "raw_text": "Observational study focused on pathological response to neoadjuvant chemotherapy.",
        "source": "Clinical Oncology",
    }
    parsed = {
        "relevancy_score": 82,
        "relevancy_reason": "Relevant due to breast cancer and imaging",
        "signals": {"cancer_type": "breast"},
    }

    result = _apply_v3_business_rules(item, parsed)
    assert result["relevancy_score"] <= 20
    assert result["signals"]["treatment_only"] is True


def test_population_screening_mced_gets_boost():
    """Population screening impact MCED studies should be boosted, not crushed."""
    from mcp_server.llm_relevancy import _apply_v3_business_rules

    item = {
        "title": "Estimating the population health impact of a multi-cancer early detection genomic blood test to complement existing screening",
        "raw_text": "Public health impact model for MCED blood-based screening in asymptomatic populations.",
        "source": "Lancet Digital Health",
    }
    parsed = {
        "relevancy_score": 35,
        "relevancy_reason": "Moderately relevant",
        "signals": {"cancer_type": "other"},
    }

    result = _apply_v3_business_rules(item, parsed)
    assert result["relevancy_score"] >= 55
    assert result["signals"]["screening_impact"] is True
    assert result["signals"]["mced_screening_combo"] is True
    assert result["signals"]["broad_genomics_without_detection"] is False


def test_breath_vocab_study_gets_bridge_boost():
    """Breath/VOC research with detection framing should avoid over-demotion."""
    from mcp_server.llm_relevancy import _apply_v3_business_rules

    item = {
        "title": "The gut microbiota shapes the human and murine breath volatilome",
        "raw_text": "Breath VOC profiles may inform future early cancer detection biomarker strategies.",
        "source": "Translational Journal",
    }
    parsed = {
        "relevancy_score": 18,
        "relevancy_reason": "Weakly relevant",
        "signals": {"cancer_type": "other"},
    }

    result = _apply_v3_business_rules(item, parsed)
    assert result["relevancy_score"] >= 25
    assert result["signals"]["breath_bridge"] is True
