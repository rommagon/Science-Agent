# SpotItEarly Relevancy Rubric v2

**Version:** 2.0
**Effective Date:** 2026-02-01
**Status:** Active (implemented in tri-model pipeline)

---

## 1. Definition of Relevance

### What Relevance Means

A publication is **relevant** to SpotItEarly if it advances our ability to detect cancer earlier in humans—ideally before symptoms appear, when treatment is most effective.

Relevance is measured by how directly a publication contributes to:
- **Population-level screening** for asymptomatic individuals
- **Non-invasive detection methods** that could be deployed at scale
- **Risk stratification** to identify who should be screened
- **Biomarker validation** with a clear path to clinical diagnostics
- **Technical advances** in detection modalities we prioritize (breath, liquid biopsy, imaging)

### What Relevance Explicitly Does NOT Mean

Relevance is **not** about:
- **Scientific quality alone** — A rigorous Nature paper on treatment resistance is not relevant; a modest pilot study on breath VOCs for early detection is.
- **Cancer importance** — We care about all cancers with early detection potential, not just high-mortality cancers.
- **Recency** — A 2015 foundational paper on cfDNA screening may be highly relevant; a 2026 paper on metastatic treatment is not.
- **Citation count or impact factor** — These are credibility signals, not relevance signals.
- **General oncology interest** — Epidemiology, basic biology, drug development, and survivorship research are generally not relevant unless they have a direct detection angle.

---

## 2. Primary Relevance Dimensions

### 2.1 Early Detection vs. Treatment

| Category | Relevance | Example |
|----------|-----------|---------|
| Primary screening in asymptomatic population | HIGH | MCED test in 6,000 asymptomatic adults |
| Early-stage detection (stage I-II) | HIGH | ctDNA for MRD in stage II colon cancer |
| Recurrence monitoring post-treatment | MODERATE | Serial ctDNA to detect molecular relapse |
| Treatment response assessment | LOW | PET imaging to assess chemotherapy response |
| Late-stage/metastatic treatment | NONE | Phase III trial in metastatic TNBC |
| Drug resistance mechanisms | NONE | KRAS G12C inhibitor resistance |

**Key principle:** If the primary endpoint is treatment efficacy or survival in advanced disease, it is not relevant regardless of the biomarkers involved.

### 2.2 Screening / MCED / Population Impact

Publications with the highest relevance demonstrate:
- **Prospective design** in an asymptomatic or screening population
- **Multi-cancer early detection (MCED)** approaches
- **High specificity** (>95%) suitable for population screening
- **Clinical utility** — leads to actionable diagnosis

Lower relevance:
- Retrospective case-control studies
- Single-cancer biomarkers without validation
- Discovery-phase work without clinical samples

### 2.3 Sample Types and Detection Modalities

SpotItEarly prioritizes non-invasive, scalable detection methods:

| Modality | Relevance Weight | Signal Flag | Notes |
|----------|------------------|-------------|-------|
| **Breath/VOC/breathomics** | VERY HIGH | `breath_voc` | Core to company mission |
| **Urine-based detection** | VERY HIGH | `urine_based` | Urine VOCs, urine biomarkers for screening |
| **Canine/animal olfaction** | VERY HIGH | `canine_detection` | Trained dogs detecting cancer; NOT mouse models |
| **Sensor-based platforms** | VERY HIGH | `sensor_based` | Electronic noses, VOC sensors, breath analyzers, electrochemical sensing (NOT generic lab assays) |
| **Liquid biopsy (ctDNA, cfDNA, CTCs)** | HIGH | `ctdna_cfdna` | Proven clinical utility |
| **Imaging (low-dose CT, MRI, ultrasound)** | MODERATE-HIGH | `imaging_based` | Depends on screening context |
| **Tissue biopsy** | LOW | — | Invasive, not suitable for screening |
| **Cell lines / in vitro** | LOW | — | No direct clinical applicability |

### 2.4 Validation Strength and Translational Readiness

| Study Type | Relevance | Rationale |
|------------|-----------|-----------|
| Prospective screening cohort (n>500) | VERY HIGH | Gold standard for detection research |
| Prospective validation (n=100-500) | HIGH | Strong evidence |
| Retrospective validation | MODERATE | Useful but lower evidence |
| Case-control discovery | LOW-MODERATE | Preliminary, needs validation |
| Pilot study (n<50) | LOW | Hypothesis-generating only |
| Cell line / animal-only | VERY LOW | No human translation demonstrated |

### 2.5 Publication Age and Industry Context

**When age matters:**
- Recent publications (last 2 years) on emerging modalities are prioritized
- Outdated methods or superseded findings are less relevant

**When age doesn't matter:**
- Foundational papers that established a field remain relevant
- Large prospective studies with durable findings
- Seminal validation studies still cited as benchmarks

**Industry context:**
- Company announcements without peer review: LOW relevance, may note strategically
- FDA clearances/approvals: MODERATE relevance for context
- Clinical trial registrations: LOW relevance without results

---

## 3. Explicit Disqualifiers and Heavy Penalties

The following categories receive **heavy penalties** or are **disqualified** from relevance:

### 3.1 Late-Stage Treatment Studies (-30 points)
- Phase I/II/III trials in metastatic or advanced disease
- Treatment response biomarkers
- Combination therapy studies
- Immunotherapy or targeted therapy efficacy

**Example:** "Pembrolizumab plus chemotherapy in metastatic triple-negative breast cancer" → Rating 0

### 3.2 Basic Mechanistic Biology (-20 points)
- Cancer cell signaling pathways
- Gene function studies
- Protein-protein interactions
- Tumor microenvironment without detection application

**Example:** "Role of BRCA1 in homologous recombination repair" → Rating 0

### 3.3 Mouse/Animal-Only Studies (-15 points)
- Xenograft models without human validation
- Spontaneous tumor models
- Drug efficacy in mice
- Exception: Animal detection studies (e.g., canine cancer sniffing) are relevant

**Example:** "Novel imaging agent detects tumors in mouse xenografts" → Rating 0-1

### 3.4 Treatment Resistance Mechanisms (-25 points)
- Acquired resistance to targeted therapy
- Chemoresistance pathways
- Immune escape mechanisms

**Example:** "Mechanisms of KRAS G12C inhibitor resistance in lung adenocarcinoma" → Rating 0

### 3.5 Reviews/Meta-analyses Without Novel Insights (-10 points)
- Narrative reviews of established findings
- Meta-analyses without new data
- Exception: Systematic reviews informing screening guidelines may be relevant

### 3.6 Non-Cancer or Non-Oncology Policy
- Cardiovascular, neurological, infectious disease
- Healthcare policy without cancer detection focus
- Survivorship and quality of life (unless biomarker-related)

---

## 4. The 0-3 Relevance Scale

### Rating 3: Core / Central (Score 75-100)

**Definition:** Must-read for SpotItEarly. Directly advances our early detection mission. Leadership should be aware of this publication.

**Criteria (must meet 2+ of the following):**
- Prospective screening study in asymptomatic population
- Multi-cancer early detection approach
- Large validation (n>200) of early-stage biomarkers
- Breath/VOC detection with clinical data
- ctDNA/liquid biopsy for primary screening
- Demonstrates clinical utility for early detection

**Examples from ground truth:**

1. **"Multi-cancer early detection test shows high specificity in prospective screening cohort"**
   - Prospective design, 6,000 asymptomatic adults
   - cfDNA methylation-based MCED
   - 99.5% specificity, detected stage I-II cancers
   - → Rating 3, Score 92

2. **"Breath-based volatile organic compounds for early breast cancer detection: validation in 500 patients"**
   - Prospective validation, large cohort
   - Breath VOC panel (core modality)
   - AUC 0.89, point-of-care potential
   - → Rating 3, Score 88

3. **"Prospective evaluation of ctDNA for lung cancer screening in high-risk population"**
   - Screening context, not treatment
   - ctDNA in asymptomatic smokers
   - → Rating 3, Score 80-90

### Rating 2: Relevant but Not Central (Score 50-74)

**Definition:** Strong connection to early detection with valuable insights. Worth reading but not mission-critical.

**Criteria:**
- Biomarker studies focused on early-stage disease
- Risk stratification for screening populations
- Imaging advances for early tumor detection
- ctDNA for recurrence monitoring (post-diagnosis)
- Retrospective studies with promising signals
- Technical advances in detection methods

**Examples:**

1. **"Circulating tumor DNA dynamics predict recurrence in early-stage colorectal cancer"**
   - ctDNA for MRD, not primary screening
   - Stage I-III (early-stage focus)
   - Clinical utility for treatment decisions
   - → Rating 2, Score 65

2. **"Machine learning model predicts 5-year lung cancer risk from low-dose CT imaging features"**
   - Risk stratification for screening
   - Imaging-based, large dataset
   - Computational but clinically applicable
   - → Rating 2, Score 58

3. **"Urinary biomarker panel for early bladder cancer detection: retrospective validation"**
   - Non-invasive modality
   - Early detection focus
   - Retrospective limits evidence strength
   - → Rating 2, Score 55

### Rating 1: Peripheral (Score 25-49)

**Definition:** Tangential connection to early detection. May inform strategy or provide background knowledge.

**Criteria:**
- Discovery-phase biomarker work
- Computational methods needing clinical validation
- Animal models with translational potential
- Pilot studies with small sample sizes
- Indirect relevance to detection

**Examples:**

1. **"Novel protein biomarkers in pancreatic cancer: a case-control discovery study"**
   - Discovery phase, n=50 per group
   - Case-control design limits applicability
   - Pancreatic cancer is high priority, but study is preliminary
   - → Rating 1, Score 35

2. **"Metabolomic profiling reveals altered lipid metabolism in ovarian cancer cell lines"**
   - Cell line study, not human subjects
   - No direct detection application
   - May eventually inform biomarker development
   - → Rating 1, Score 28

3. **"Deep learning for mammography interpretation: single-center pilot"**
   - Imaging relevant
   - Small pilot, single-center
   - Needs validation before clinical impact
   - → Rating 1, Score 40

### Rating 0: Not Relevant (Score 0-24)

**Definition:** No clear connection to early cancer detection. Do not surface to leadership.

**Criteria:**
- Treatment studies for advanced/metastatic cancer
- Drug resistance mechanisms
- Basic cancer biology without detection angle
- Epidemiology without biomarker component
- Non-cancer research

**Examples:**

1. **"Phase III trial of pembrolizumab plus chemotherapy in metastatic triple-negative breast cancer"**
   - Late-stage treatment
   - No detection or biomarker component
   - → Rating 0, Score 8

2. **"Mechanisms of KRAS G12C inhibitor resistance in lung adenocarcinoma"**
   - Treatment resistance, not detection
   - Advanced disease focus
   - → Rating 0, Score 5

3. **"Gut microbiome and colorectal cancer risk: epidemiological analysis"**
   - Epidemiology without detection application
   - No biomarker or screening component
   - → Rating 0, Score 12

---

## 5. Edge Cases

### 5.1 Old but Foundational Reviews

**Scenario:** A 2018 review article that established the conceptual framework for multi-cancer early detection.

**Guidance:** If still widely cited and foundational to the field, may warrant Rating 1-2. Penalize if superseded by more recent comprehensive reviews. Note in rationale that relevance is historical/contextual.

### 5.2 Non-Peer-Reviewed but Strategically Important

**Scenario:** A company white paper or preprint announcing a breakthrough in breath-based detection.

**Guidance:**
- Preprints from reputable groups: Score based on content, note lack of peer review in concerns
- Company announcements: Generally Rating 1 maximum unless accompanied by peer-reviewed data
- Conference abstracts: Rating 1-2 if highly relevant modality, pending full publication

### 5.3 Weak Results but Strong Modality Relevance

**Scenario:** A breath VOC study with AUC 0.65 (poor performance) but novel approach.

**Guidance:** Modality relevance alone does not override weak results. If the study fails to demonstrate clinical utility, cap at Rating 1-2. Strong modality relevance without strong results = "promising but unproven."

### 5.4 Dual-Purpose Studies

**Scenario:** A study uses ctDNA for both treatment response monitoring AND recurrence detection.

**Guidance:** Score based on the early detection component. If the study demonstrates value for recurrence detection (before clinical progression), that portion is relevant. Treatment response portion is not.

### 5.5 Computational/AI Studies

**Scenario:** A deep learning model achieves excellent performance on a curated dataset but lacks prospective validation.

**Guidance:** Computational advances without clinical validation are capped at Rating 1-2. The algorithm must demonstrate real-world clinical utility to achieve Rating 3.

---

## 6. Mapping to the Tri-Model Pipeline

### 6.1 Pipeline Overview

```
Publication → Claude Review →
              Gemini Review → GPT Evaluator → Final Score
```

Each model receives the same rubric (v2) but evaluates independently before the GPT evaluator synthesizes.

### 6.2 What Claude Evaluates

**Role:** Primary reviewer with detailed rubric application

**Evaluates:**
- Cancer type and detection focus
- Study design (prospective vs. retrospective)
- Sample type and modality relevance
- Validation strength (cohort size, endpoints)
- Penalties for treatment/mechanistic focus

**Output:**
- `relevancy_rating_0_3`: Integer rating
- `relevancy_score_0_100`: Numeric score
- `key_reasons`: 1-3 specific factual reasons
- `tags`: Categorical labels (screening, ctdna, breath-voc, etc.)
- `signals`: Boolean flags for detection dimensions
- `uncertainty`: low/medium/high

### 6.3 What Gemini Evaluates

**Role:** Independent second reviewer with same rubric

**Evaluates:** Same dimensions as Claude, providing independent assessment

**Output:** Same schema as Claude

**Design intent:** Two independent assessments reduce single-model bias and catch errors in rubric application.

### 6.4 What the GPT Evaluator Adjudicates

**Role:** Meta-evaluator that synthesizes Claude and Gemini reviews

**Evaluates:**
1. **Agreement level:**
   - High: Ratings within 1 point, scores within 15 points
   - Moderate: Ratings differ by 1, scores 15-30 apart
   - Low: Ratings differ by 2+, scores >30 apart

2. **Quality check:**
   - Did reviewers correctly identify study type?
   - Are key_reasons supported by the abstract?
   - Did either miss important signals?
   - Any factual errors?

3. **Tie-breaking principles:**
   - Favor reviewer with more specific evidence
   - Prospective > retrospective
   - Human data > animal/cell line
   - When in doubt, be conservative (lower score)

**Output:**
- `final_relevancy_rating_0_3`: Authoritative final rating
- `final_relevancy_score`: Authoritative final score (0-100)
- `final_relevancy_reason`: GPT's own reasoning
- `key_reasons`: Synthesized or selected reasons
- `agreement_level`: high/moderate/low
- `disagreements`: Description of reviewer differences
- `evaluator_rationale`: Which reviewer was weighted and why

### 6.5 Score-Rating Consistency

The `final_relevancy_score` and `final_relevancy_rating_0_3` must be consistent:

| Rating | Score Range |
|--------|-------------|
| 3 | 75-100 |
| 2 | 50-74 |
| 1 | 25-49 |
| 0 | 0-24 |

If a reviewer produces inconsistent score/rating, the GPT evaluator should correct this.

### 6.6 Output Fields Used Downstream

**For ranking/selection (weekly digest, must-reads):**
- `final_relevancy_score` — Primary sort key
- `final_relevancy_rating_0_3` — Threshold filtering (e.g., only show rating >= 2)

**For display (reports, UI):**
- `final_relevancy_reason` — Human-readable explanation
- `key_reasons` — Bullet points for quick scan
- `tags` — Categorical filtering
- `final_summary` — Brief abstract

**Informational only (not used for ranking):**
- `credibility_score` — Assesses study quality, not relevance
- `agreement_level` — Confidence indicator
- `uncertainty` — Model's self-assessed confidence

---

## Appendix: Prompt Version History

| Version | Date | Changes |
|---------|------|---------|
| v1 | 2025-12 | Original rubric: cancer type priority + breath/VOC bonuses |
| v2 | 2026-02 | SpotItEarly-aligned rubric: early detection focus, explicit penalties, few-shot examples, 0-3 rating scale |

---

## Document Maintenance

This document should be updated when:
- Prompt templates are modified
- New few-shot examples are added
- Scoring thresholds are adjusted
- New modalities or cancer types are prioritized

**Owner:** Engineering / Data Science
**Review cycle:** Quarterly or upon significant prompt changes
