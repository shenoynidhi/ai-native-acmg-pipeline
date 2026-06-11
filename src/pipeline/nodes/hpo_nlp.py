"""
src/pipeline/nodes/hpo_nlp.py

HPO NLP Node — extracts HPO phenotype terms from free-text clinical notes.

Strategy (three-source merge):
  1. Doc2HPO (primary)  — external API; best-in-class negation detection via
     NegEx; returns HPO IDs directly. NOTE: clinical text is sent to
     https://doc2hpo.wglab.org — acceptable for research use but must be
     reviewed before deployment in a HIPAA/GDPR-regulated environment.
  2. LLM (pod-b, secondary) — catches terms Doc2HPO misses, especially
     complex or multi-word phenotypes buried in narrative prose.
  3. hp.obo validation (all terms) — every ID from both sources is
     cross-checked against the local ontology file. Invalid IDs trigger
     label-based fallback before discard. Canonical labels are enforced.
  4. Merge + deduplicate on hpo_id:
       - If both sources agree on present/absent → keep, highest confidence wins.
       - If sources CONFLICT on negation → Doc2HPO wins (built for negation).
       - Redundant terms (same hpo_id) collapsed to single entry.

Node contract:
  Input fields read : clinical_notes (str)
  Output fields set : patient_hpo_terms (List[Dict])
  Side effects      : HTTP POST to doc2hpo.wglab.org (clinical text only)
  Skipped when      : _should_run_hpo_nlp returns "skip_nlp"

Output format per term:
  {
      "hpo_id":     "HP:0001250",   # validated canonical ID
      "label":      "Seizure",       # canonical label from hp.obo
      "present":    True,            # False = explicitly negated
      "confidence": "HIGH",          # HIGH / MEDIUM / LOW
      "source":     "doc2hpo"        # "doc2hpo" | "llm" | "doc2hpo+llm"
  }
"""

import re
import logging
import requests
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.pipeline.state import VariantState
from src.utils.llm_client import call_llm_json
from src.config import DATABASE_PATHS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOC2HPO_URL   = "https://doc2hpo.wglab.org/parse/acdat"
DOC2HPO_TIMEOUT = 5   # seconds

# ---------------------------------------------------------------------------
# hp.obo parser — lazy-loaded once at first node invocation
# ---------------------------------------------------------------------------

_HPO_OBO_PATH = DATABASE_PATHS["hpo_obo"]

_HPO_ID_TO_LABEL:   Dict[str, str] = {}   # "HP:0001250" → "Seizure"
_HPO_LABEL_TO_ID:   Dict[str, str] = {}   # "seizure"    → "HP:0001250"  (lowercased)
_HPO_SYNONYM_TO_ID: Dict[str, str] = {}   # synonym text  → canonical ID (lowercased)
_HPO_LOADED = False


def _load_hpo_obo() -> None:
    """Parse hp.obo and populate module-level lookup dicts. No-op after first call."""
    global _HPO_LOADED, _HPO_ID_TO_LABEL, _HPO_LABEL_TO_ID, _HPO_SYNONYM_TO_ID

    if _HPO_LOADED:
        return

    if not _HPO_OBO_PATH.exists():
        logger.error(
            f"hp.obo not found at {_HPO_OBO_PATH}. "
            "HPO validation will be skipped — terms accepted without ontology cross-check."
        )
        _HPO_LOADED = True
        return

    current_id:       Optional[str] = None
    current_name:     Optional[str] = None
    current_synonyms: List[str]     = []
    is_obsolete = False

    def _commit():
        if current_id and current_name and not is_obsolete:
            _HPO_ID_TO_LABEL[current_id]            = current_name
            _HPO_LABEL_TO_ID[current_name.lower()]  = current_id
            for syn in current_synonyms:
                _HPO_SYNONYM_TO_ID[syn.lower()] = current_id

    with open(_HPO_OBO_PATH, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()

            if line == "[Term]":
                _commit()
                current_id       = None
                current_name     = None
                current_synonyms = []
                is_obsolete      = False
            elif line.startswith("id: HP:"):
                current_id = line[4:]
            elif line.startswith("name: "):
                current_name = line[6:]
            elif line.startswith("synonym:"):
                m = re.search(r'synonym:\s*"([^"]+)"', line)
                if m:
                    current_synonyms.append(m.group(1))
            elif line == "is_obsolete: true":
                is_obsolete = True

    _commit()  # final term in file

    logger.info(
        f"hp.obo loaded: {len(_HPO_ID_TO_LABEL)} terms, "
        f"{len(_HPO_SYNONYM_TO_ID)} synonyms."
    )
    _HPO_LOADED = True


# ---------------------------------------------------------------------------
# hp.obo validator / corrector
# ---------------------------------------------------------------------------

def _validate_and_correct(
    hpo_id: str,
    label:  str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (canonical_id, canonical_label) or (None, None) if unresolvable.

    Resolution order:
      1. hpo_id exists in ontology             → fix label to canonical, return
      2. hpo_id invalid, label = primary name  → look up correct ID, return
      3. hpo_id invalid, label = synonym        → resolve to canonical, return
      4. Nothing matches                        → discard (None, None)
    """
    if not _HPO_ID_TO_LABEL:
        # obo not loaded — accept as-is (graceful degradation)
        return hpo_id, label

    # 1. Direct ID hit
    if hpo_id in _HPO_ID_TO_LABEL:
        return hpo_id, _HPO_ID_TO_LABEL[hpo_id]

    label_lower = label.lower().strip() if label else ""

    # 2. Label → primary name
    if label_lower and label_lower in _HPO_LABEL_TO_ID:
        cid = _HPO_LABEL_TO_ID[label_lower]
        return cid, _HPO_ID_TO_LABEL[cid]

    # 3. Label → synonym
    if label_lower and label_lower in _HPO_SYNONYM_TO_ID:
        cid = _HPO_SYNONYM_TO_ID[label_lower]
        return cid, _HPO_ID_TO_LABEL[cid]

    logger.warning(
        f"HPO term discarded — ID '{hpo_id}' not in ontology and "
        f"label '{label}' matched no primary name or synonym."
    )
    return None, None


# ---------------------------------------------------------------------------
# Source A — Doc2HPO
# ---------------------------------------------------------------------------

def _doc2hpo_extract(clinical_notes: str) -> List[dict]:
    """
    Call the Doc2HPO API with NegEx enabled.
    Returns a list of raw dicts; empty list on any failure.

    Doc2HPO response item keys used:
      hpoId    → HPO identifier string e.g. "HP:0001250"
      hpoName  → English label
      present  → bool (NegEx negation result)
    """
    try:
        resp = requests.post(
            DOC2HPO_URL,
            json={"note": clinical_notes, "negex": True},
            timeout=DOC2HPO_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json()

        if not isinstance(items, list):
            logger.warning("Doc2HPO returned non-list response — skipping.")
            return []

        terms = []
        for item in items:
            hpo_id = str(item.get("hpoId",   "")).strip()
            label  = str(item.get("hpoName", "")).strip()
            # Doc2HPO returns present as bool; default True if key absent
            present = bool(item.get("present", True))
            if hpo_id:
                terms.append({
                    "hpo_id":     hpo_id,
                    "label":      label,
                    "present":    present,
                    "confidence": "HIGH",
                    "source":     "doc2hpo",
                })

        logger.info(f"Doc2HPO extracted {len(terms)} raw terms.")
        return terms

    except requests.exceptions.Timeout:
        logger.warning(f"Doc2HPO timed out after {DOC2HPO_TIMEOUT}s.")
    except requests.exceptions.ConnectionError:
        logger.warning("Doc2HPO unreachable (connection error).")
    except Exception as exc:
        logger.warning(f"Doc2HPO failed: {exc}")

    return []


# ---------------------------------------------------------------------------
# Source B — LLM extraction
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """\
You are a clinical geneticist with expertise in HPO (Human Phenotype Ontology) coding.
Extract ALL phenotype features mentioned in the clinical notes and map them to their
correct HPO identifiers.

Rules:
- Extract BOTH present phenotypes and explicitly ABSENT phenotypes
  (negated findings such as "no seizures", "denies intellectual disability").
- Set "present": true for observed phenotypes, false for negated phenotypes.
- Use the most specific HPO term available.
- Do NOT invent phenotypes not mentioned in the notes.
- If uncertain of the exact HPO ID, provide your best match and set "confidence": "LOW".
  Do not fabricate IDs.
- Respond ONLY with a valid JSON object. No preamble, no markdown fences.

Output schema:
{
  "extracted_terms": [
    {
      "hpo_id":     "HP:0001250",
      "label":      "Seizure",
      "present":    true,
      "confidence": "HIGH"
    }
  ],
  "extraction_notes": "optional comment on ambiguous cases"
}
"""


def _llm_extract(clinical_notes: str) -> List[dict]:
    """
    Call LLM on pod-b to extract HPO terms.
    Returns raw list; empty list on failure.
    """
    user_prompt = (
        "Extract all HPO phenotype terms from the following clinical notes.\n\n"
        f"CLINICAL NOTES:\n{clinical_notes}\n\nReturn JSON only."
    )

    try:
        result = call_llm_json(
            system_prompt=_LLM_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.0,
        )
    except Exception as exc:
        logger.error(f"LLM extraction failed in hpo_nlp_node: {exc}")
        return []

    raw_terms = result.get("extracted_terms", [])
    notes     = result.get("extraction_notes", "")
    if notes:
        logger.info(f"LLM extraction notes: {notes}")

    if not isinstance(raw_terms, list):
        logger.warning("LLM returned non-list 'extracted_terms'.")
        return []

    for item in raw_terms:
        if isinstance(item, dict):
            item["source"] = "llm"

    logger.info(f"LLM extracted {len(raw_terms)} raw terms.")
    return raw_terms


# ---------------------------------------------------------------------------
# Validation pass — applied to ALL terms regardless of source
# ---------------------------------------------------------------------------

def _validate_terms(raw_terms: List[dict]) -> List[dict]:
    """
    Run every term through hp.obo validation.
    Returns only terms that pass (invalid IDs with unresolvable labels discarded).
    """
    validated = []
    discarded = 0

    for item in raw_terms:
        if not isinstance(item, dict):
            discarded += 1
            continue

        raw_id    = str(item.get("hpo_id",    "")).strip()
        raw_label = str(item.get("label",     "")).strip()
        present   = bool(item.get("present",  True))
        conf      = str(item.get("confidence", "MEDIUM")).upper()
        source    = str(item.get("source",    "unknown"))

        if conf not in ("HIGH", "MEDIUM", "LOW"):
            conf = "MEDIUM"

        canon_id, canon_label = _validate_and_correct(raw_id, raw_label)

        if canon_id is None:
            discarded += 1
            continue

        validated.append({
            "hpo_id":     canon_id,
            "label":      canon_label,
            "present":    present,
            "confidence": conf,
            "source":     source,
        })

    if discarded:
        logger.info(f"Validation: {discarded} terms discarded (not in ontology).")

    return validated


# ---------------------------------------------------------------------------
# Merge + deduplicate
# ---------------------------------------------------------------------------

def _merge_terms(
    doc2hpo_terms: List[dict],
    llm_terms:     List[dict],
) -> List[dict]:
    """
    Merge two validated term lists, deduplicate on hpo_id.

    Merge rules per hpo_id:
      - Both sources agree on present/absent → keep; source = "doc2hpo+llm";
        confidence = highest of the two.
      - Sources CONFLICT on negation → Doc2HPO wins (NegEx purpose-built for this).
      - Term from only one source → keep as-is.

    Confidence ranking: HIGH > MEDIUM > LOW
    """
    CONF_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

    # Index doc2hpo terms by hpo_id
    merged: Dict[str, dict] = {}
    for t in doc2hpo_terms:
        merged[t["hpo_id"]] = t.copy()

    for t in llm_terms:
        hid = t["hpo_id"]
        if hid not in merged:
            # LLM-only term — add directly
            merged[hid] = t.copy()
        else:
            existing = merged[hid]
            # Conflict resolution on negation — Doc2HPO wins
            if existing["present"] != t["present"]:
                logger.info(
                    f"Negation conflict on {hid} ({existing['label']}): "
                    f"doc2hpo={existing['present']}, llm={t['present']} → "
                    "keeping doc2hpo."
                )
                # existing already holds doc2hpo value; just update source + confidence
                best_conf = (
                    existing["confidence"]
                    if CONF_RANK.get(existing["confidence"], 0)
                    >= CONF_RANK.get(t["confidence"], 0)
                    else t["confidence"]
                )
                merged[hid] = {**existing, "source": "doc2hpo+llm", "confidence": best_conf}
            else:
                # Agreement — merge source tag, pick highest confidence
                best_conf = (
                    existing["confidence"]
                    if CONF_RANK.get(existing["confidence"], 0)
                    >= CONF_RANK.get(t["confidence"], 0)
                    else t["confidence"]
                )
                merged[hid] = {
                    **existing,
                    "source":     "doc2hpo+llm",
                    "confidence": best_conf,
                }

    result = list(merged.values())
    logger.info(
        f"Merge: {len(doc2hpo_terms)} doc2hpo + {len(llm_terms)} llm "
        f"→ {len(result)} unique terms after deduplication."
    )
    return result


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

def hpo_nlp_node(state: VariantState) -> dict:
    """
    LangGraph node: extract, validate, and merge HPO terms from clinical_notes.

    Flow:
      1. Doc2HPO extraction  (primary; skipped gracefully if API unreachable)
      2. LLM extraction      (secondary; always attempted)
      3. hp.obo validation   (both term sets independently)
      4. Merge + deduplicate
      5. Write patient_hpo_terms to state
    """
    _load_hpo_obo()

    clinical_notes: str = state.get("clinical_notes") or ""
    if not clinical_notes.strip():
        logger.warning("hpo_nlp_node called with empty clinical_notes — returning [].")
        return {"patient_hpo_terms": []}

    # --- Source A: Doc2HPO ---
    doc2hpo_raw       = _doc2hpo_extract(clinical_notes)
    doc2hpo_validated = _validate_terms(doc2hpo_raw)

    # --- Source B: LLM ---
    llm_raw       = _llm_extract(clinical_notes)
    llm_validated = _validate_terms(llm_raw)

    # --- Merge ---
    if not doc2hpo_validated and not llm_validated:
        logger.warning("Both extraction sources returned no valid HPO terms.")
        return {"patient_hpo_terms": []}

    final_terms = _merge_terms(doc2hpo_validated, llm_validated)

    present_count = sum(1 for t in final_terms if t["present"])
    absent_count  = len(final_terms) - present_count
    logger.info(
        f"hpo_nlp_node complete: {len(final_terms)} terms "
        f"({present_count} present, {absent_count} absent/negated)."
    )

    return {"patient_hpo_terms": final_terms}
