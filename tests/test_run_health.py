"""Tests for per-model failure accounting in the tri-model daily run.

Regression for the run that reported "99 successful, 0 failures" while Gemini
404'd on 95/99 papers and the OpenAI evaluator 429'd throughout: failed
reviewer calls were masked by the reviewer-fallback evaluator.
"""

import io

import pytest

from tri_model import run_health as rh


def _tracker(*outcomes, external=("claude", "gemini", "gpt")):
    t = rh.ModelFailureTracker(external_models=list(external))
    for o in outcomes:
        t.record_item(o)
    return t


# ── Counting + thresholds ─────────────────────────────────────────────────────

def test_last_passing_run_shape_warns_but_claude_keeps_it_green():
    """Gemini 95/99 and GPT 99/99 failing while Claude succeeds: loud warnings
    for both, but scores still have a real external model behind them."""
    outcomes = (
        [{"claude": True, "gemini": False, "gpt": False}] * 95
        + [{"claude": True, "gemini": True, "gpt": False}] * 4
    )
    t = _tracker(*outcomes)
    assert t.summary() == "claude 0/99, gemini 95/99, gpt 99/99"
    v = t.evaluate(0.20, 0.50)
    assert [w.split()[0] for w in v.warnings] == ["gemini", "gpt"]
    assert not v.failed


def test_all_external_models_failing_is_an_error():
    outcomes = (
        [{"claude": False, "gemini": False, "gpt": None}] * 6   # skipped papers
        + [{"claude": True, "gemini": False, "gpt": False}] * 4
    )
    t = _tracker(*outcomes)
    assert t.fallback_items == 6
    v = t.evaluate(0.20, 0.50)
    assert v.failed and "6/10" in v.error and "fallback-only" in v.error


def test_thresholds_are_strict():
    t = _tracker(
        *[{"claude": False, "gemini": False, "gpt": None}] * 5,
        *[{"claude": True, "gemini": True, "gpt": True}] * 5,
    )
    assert not t.evaluate(0.20, 0.50).failed          # exactly 50% -> not an error
    t = _tracker(
        *[{"claude": True, "gemini": False, "gpt": True}] * 2,
        *[{"claude": True, "gemini": True, "gpt": True}] * 8,
    )
    assert t.evaluate(0.20, 0.50).warnings == []      # exactly 20% -> no warning


def test_unattempted_model_is_not_a_failure():
    t = _tracker(*[{"claude": True, "gemini": None, "gpt": True}] * 3)
    assert "gemini" not in t.attempts and t.evaluate().warnings == []


def test_external_set_override_makes_gpt_outage_red(monkeypatch):
    monkeypatch.setenv("TRI_MODEL_EXTERNAL_REVIEWERS", "gpt,gemini")
    t = rh.new_tracker()
    for _ in range(4):
        t.record_item({"claude": True, "gemini": False, "gpt": False})
    assert t.evaluate(0.20, 0.50).failed


def test_thresholds_from_env(monkeypatch):
    monkeypatch.delenv("TRI_MODEL_FAILURE_WARN_RATE", raising=False)
    monkeypatch.delenv("TRI_MODEL_FAILURE_ERROR_RATE", raising=False)
    assert rh.thresholds_from_env() == (0.20, 0.50)
    monkeypatch.setenv("TRI_MODEL_FAILURE_WARN_RATE", "10")
    monkeypatch.setenv("TRI_MODEL_FAILURE_ERROR_RATE", "0.75")
    assert rh.thresholds_from_env() == (0.10, 0.75)
    monkeypatch.delenv("TRI_MODEL_EXTERNAL_REVIEWERS", raising=False)
    assert rh.new_tracker().external_models == ["claude", "gemini", "gpt"]


def test_check_and_annotate_output_and_exit_code():
    buf = io.StringIO()
    t = _tracker(*[{"claude": False, "gemini": False, "gpt": None}] * 2)
    assert rh.check_and_annotate(t, title="Tri-model scoring", stream=buf) == 1
    out = buf.getvalue()
    assert "::warning title=Tri-model scoring::claude failed on 2/2 items (100%25" in out
    assert "::warning title=Tri-model scoring::gemini failed on 2/2" in out
    assert "::error title=Tri-model scoring::All external LLM reviewers" in out

    buf = io.StringIO()
    ok = _tracker({"claude": True, "gemini": True, "gpt": True})
    assert rh.check_and_annotate(ok, stream=buf) == 0 and buf.getvalue() == ""


# ── Wiring in run_tri_model_daily.review_paper_with_tri_model ─────────────────

PAPER = {"id": "p" * 16, "title": "Breath VOCs for lung cancer", "source": "x", "raw_text": "abc"}


@pytest.fixture()
def runner(monkeypatch):
    import run_tri_model_daily
    from tri_model import credibility, evaluator, reviewers

    monkeypatch.setattr(credibility, "score_paper_credibility",
                        lambda paper: {"credibility_score": 50})
    monkeypatch.setattr(evaluator, "reviewer_fallback_evaluate",
                        lambda paper, c, g, error=None: {"success": True,
                                                         "model": "reviewer-fallback",
                                                         "evaluation": {}})
    return run_tri_model_daily, reviewers, evaluator


def _ok(model):
    return {"success": True, "model": model, "review": {"relevancy_score": 70}}


def _fail(model, err):
    return {"success": False, "model": model, "review": None, "error": err}


def test_review_records_failures_even_when_fallback_succeeds(runner, monkeypatch):
    r, reviewers, evaluator = runner
    monkeypatch.setattr(reviewers, "claude_review", lambda p: _ok("claude"))
    monkeypatch.setattr(reviewers, "gemini_review", lambda p: _fail("gemini", "404 model not found"))
    monkeypatch.setattr(evaluator, "gpt_evaluate",
                        lambda p, c, g: {"success": False, "error": "429 insufficient_quota"})

    t = rh.new_tracker()
    result = r.review_paper_with_tri_model(PAPER, ["claude", "gemini"], tracker=t)

    assert result is not None                       # paper still scored (fallback)
    assert result["gpt_evaluation"]["model"] == "reviewer-fallback"
    assert t.failures == {"gemini": 1, "gpt": 1}
    assert t.attempts == {"claude": 1, "gemini": 1, "gpt": 1}


def test_review_records_skipped_paper_and_exceptions(runner, monkeypatch):
    r, reviewers, evaluator = runner

    def boom(p):
        raise RuntimeError("network down")

    monkeypatch.setattr(reviewers, "claude_review", boom)
    monkeypatch.setattr(reviewers, "gemini_review", lambda p: _fail("gemini", "404"))
    called = {"gpt": False}

    def gpt(p, c, g):
        called["gpt"] = True
        return {"success": True}

    monkeypatch.setattr(evaluator, "gpt_evaluate", gpt)

    t = rh.new_tracker()
    assert r.review_paper_with_tri_model(PAPER, ["claude", "gemini"], tracker=t) is None
    assert called["gpt"] is False                   # never reached the evaluator
    assert t.failures == {"claude": 1, "gemini": 1}
    assert "gpt" not in t.attempts
    assert t.fallback_items == 1


def test_review_without_tracker_still_works(runner, monkeypatch):
    r, reviewers, evaluator = runner
    monkeypatch.setattr(reviewers, "claude_review", lambda p: _ok("claude"))
    monkeypatch.setattr(reviewers, "gemini_review", lambda p: _ok("gemini"))
    monkeypatch.setattr(evaluator, "gpt_evaluate",
                        lambda p, c, g: {"success": True, "model": "gpt", "evaluation": {}})
    result = r.review_paper_with_tri_model(PAPER, ["claude", "gemini"])
    assert result["gpt_evaluation"]["model"] == "gpt"
