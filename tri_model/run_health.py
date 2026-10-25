"""Per-model reviewer/evaluator failure accounting for a scoring run.

Every LLM call in the scoring path degrades gracefully (a failed arbiter falls
back to a reviewer's score, a failed reviewer is simply skipped), which keeps a
run's data flowing — but it also means a run can report itself as fully
successful while one or more models failed on every single item (e.g. an
exhausted API quota). This module makes that visible:

  * ``ModelFailureTracker`` counts, per model, how many items it was attempted
    on and how many of those attempts failed, plus how many items were scored
    without ANY external model succeeding (i.e. "fallback-only" scores).
  * ``evaluate`` turns those counts into a verdict against two thresholds:
      - warn:  any single model failed on more than ``warn_rate`` of the items
               it was attempted on            -> ``::warning::`` annotation
      - error: more than ``error_rate`` of items had every external model fail
               (scores are essentially fallback-only) -> ``::error::`` + exit 1
  * ``emit_annotations`` prints GitHub Actions workflow commands to stdout.

The check is advisory bookkeeping only: callers run it at the very END of a
run, after every result has been persisted, so a red run never loses data.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, TextIO

DEFAULT_WARN_RATE = 0.20
DEFAULT_ERROR_RATE = 0.50


def _env_rate(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    # Accept either a fraction (0.2) or a percentage (20).
    if value > 1:
        value = value / 100.0
    return min(max(value, 0.0), 1.0)


def _env_models(name: str, default: Iterable[str]) -> List[str]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return list(default)
    return [m.strip().lower() for m in raw.split(",") if m.strip()]


@dataclass
class HealthVerdict:
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.error is not None


@dataclass
class ModelFailureTracker:
    """Counts per-model attempts/failures across the items of one run.

    ``external_models`` are the LLMs whose success makes a score "real". An
    item on which none of them succeeded is counted as fallback-only.
    """

    external_models: List[str]
    items: int = 0
    fallback_items: int = 0
    attempts: Dict[str, int] = field(default_factory=dict)
    failures: Dict[str, int] = field(default_factory=dict)

    def record_item(self, outcomes: Mapping[str, Optional[bool]]) -> None:
        """Record one scored item.

        ``outcomes`` maps model name -> True (succeeded), False (attempted and
        failed) or None (not attempted, e.g. disabled). An empty mapping means
        scoring crashed before any model result was known: the item counts as
        fallback-only but is not attributed to any model.
        """
        self.items += 1
        any_external_ok = False
        for model, ok in outcomes.items():
            if ok is None:
                continue
            key = model.lower()
            self.attempts[key] = self.attempts.get(key, 0) + 1
            if ok:
                if key in self.external_models:
                    any_external_ok = True
            else:
                self.failures[key] = self.failures.get(key, 0) + 1
        if not any_external_ok:
            self.fallback_items += 1

    def failure_rate(self, model: str) -> float:
        n = self.attempts.get(model, 0)
        return (self.failures.get(model, 0) / n) if n else 0.0

    def summary(self) -> str:
        """Compact per-model failure counts, e.g. ``claude 0/99, gpt 99/99``."""
        if not self.attempts:
            return "none recorded"
        return ", ".join(
            f"{m} {self.failures.get(m, 0)}/{self.attempts[m]}" for m in sorted(self.attempts)
        )

    def evaluate(
        self,
        warn_rate: float = DEFAULT_WARN_RATE,
        error_rate: float = DEFAULT_ERROR_RATE,
    ) -> HealthVerdict:
        verdict = HealthVerdict()
        if self.items == 0:
            return verdict
        for model in sorted(self.attempts):
            rate = self.failure_rate(model)
            if rate > warn_rate:
                verdict.warnings.append(
                    f"{model} failed on {self.failures.get(model, 0)}/{self.attempts[model]} "
                    f"items ({rate:.0%}, threshold {warn_rate:.0%})"
                )
        fallback_rate = self.fallback_items / self.items
        if fallback_rate > error_rate:
            verdict.error = (
                f"All external LLM reviewers ({', '.join(self.external_models) or 'none'}) "
                f"failed on {self.fallback_items}/{self.items} items ({fallback_rate:.0%}, "
                f"threshold {error_rate:.0%}): scores are essentially fallback-only. "
                f"Per-model failures: {self.summary()}"
            )
        return verdict


def _escape(message: str) -> str:
    # GitHub workflow-command data escaping.
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit_annotations(
    verdict: HealthVerdict,
    *,
    title: str = "LLM scoring",
    stream: Optional[TextIO] = None,
) -> None:
    """Print GitHub Actions ``::warning::`` / ``::error::`` workflow commands."""
    out = stream or sys.stdout
    for w in verdict.warnings:
        print(f"::warning title={title}::{_escape(w)}", file=out)
    if verdict.error:
        print(f"::error title={title}::{_escape(verdict.error)}", file=out)
    out.flush()


def check_and_annotate(
    tracker: ModelFailureTracker,
    warn_rate: float = DEFAULT_WARN_RATE,
    error_rate: float = DEFAULT_ERROR_RATE,
    *,
    title: str = "LLM scoring",
    log=None,
    stream: Optional[TextIO] = None,
) -> int:
    """Evaluate, emit annotations (and log lines), and return an exit code:
    1 when every external LLM reviewer failed on most items, else 0."""
    verdict = tracker.evaluate(warn_rate, error_rate)
    emit_annotations(verdict, title=title, stream=stream)
    if log is not None:
        for w in verdict.warnings:
            log.warning("Model failure rate high: %s", w)
        if verdict.error:
            log.error("%s", verdict.error)
    return 1 if verdict.failed else 0


# ── Tri-model daily wiring ────────────────────────────────────────────────────
#
# Claude and Gemini review each paper and the GPT evaluator synthesizes them;
# all three are external APIs. When GPT fails, a deterministic
# reviewer-fallback aggregate is used; when both reviewers fail the paper is
# skipped. "Fallback-only" therefore means no external model succeeded on the
# paper. Override the set with TRI_MODEL_EXTERNAL_REVIEWERS (e.g. "gpt,gemini"
# to also treat Claude-only fallback scores as fallback).

def new_tracker() -> ModelFailureTracker:
    return ModelFailureTracker(
        external_models=_env_models("TRI_MODEL_EXTERNAL_REVIEWERS", ["claude", "gemini", "gpt"]),
    )


def thresholds_from_env() -> tuple:
    """(warn_rate, error_rate) from TRI_MODEL_FAILURE_WARN_RATE /
    TRI_MODEL_FAILURE_ERROR_RATE (fractions or percentages; defaults 0.2/0.5)."""
    return (
        _env_rate("TRI_MODEL_FAILURE_WARN_RATE", DEFAULT_WARN_RATE),
        _env_rate("TRI_MODEL_FAILURE_ERROR_RATE", DEFAULT_ERROR_RATE),
    )
