"""
src/utils/disease_matcher.py

Disease name fuzzy matching for PS1/PM5 ClinVar disease cross-validation.

Used to check if ClinVar disease matches patient's Orphanet-matched disease,
preventing false PS1/PM5 application when variant is pathogenic for a different
disease than the patient has.

Example:
    TP53 variant pathogenic for "Li-Fraumeni syndrome" (cancer predisposition)
    vs patient with "Autism spectrum disorder" → NO MATCH → PS1 with caution
"""

import re
from typing import Optional


# Common disease name stopwords to remove before comparison
_STOPWORDS = {
    "syndrome", "disease", "disorder", "type", "familial", "hereditary",
    "autosomal", "dominant", "recessive", "x-linked", "linked",
    "early", "late", "onset", "adult", "juvenile", "infantile", "congenital",
    "susceptibility", "to", "predisposition", "risk", "for", "of", "the", "and",
}


def diseases_match(
    clinvar_disease: Optional[str],
    orphanet_disease: Optional[str],
    threshold: float = 0.5,
) -> bool:
    """
    Fuzzy match two disease names with synonym handling.

    Args:
        clinvar_disease: Disease name from ClinVar CLNDN field
        orphanet_disease: Disease name from Orphanet/HPO matching
        threshold: Jaccard similarity threshold (default 0.5 = 50% word overlap)

    Returns:
        True if diseases refer to the same condition

    Examples:
        >>> diseases_match("Breast-ovarian cancer, familial 2",
        ...                "Hereditary breast and ovarian cancer syndrome")
        True  # Same condition, different wording

        >>> diseases_match("Li-Fraumeni syndrome",
        ...                "Autism spectrum disorder")
        False  # Different conditions

        >>> diseases_match("Dravet syndrome",
        ...                "Severe myoclonic epilepsy of infancy")
        True  # Synonym
    """
    if not clinvar_disease or not orphanet_disease:
        return False

    # Normalize both disease names
    clinvar_words = _normalize_disease_name(clinvar_disease)
    orphanet_words = _normalize_disease_name(orphanet_disease)

    if not clinvar_words or not orphanet_words:
        return False

    # Jaccard similarity: intersection / union
    intersection = clinvar_words & orphanet_words
    union = clinvar_words | orphanet_words

    similarity = len(intersection) / len(union)

    return similarity >= threshold


def _normalize_disease_name(name: str) -> set:
    """
    Normalize disease name for comparison.

    Steps:
    1. Lowercase
    2. Remove punctuation (keep spaces and letters)
    3. Split into words
    4. Remove common stopwords
    5. Apply synonym mapping
    6. Return set of normalized words

    Args:
        name: Disease name string

    Returns:
        Set of normalized words
    """
    if not name:
        return set()

    # Lowercase
    name = name.lower()

    # Remove punctuation (keep spaces, letters, numbers)
    name = re.sub(r'[^\w\s]', ' ', name)

    # Split into words
    words = name.split()

    # Remove stopwords
    words = [w for w in words if w not in _STOPWORDS]

    # Apply synonyms
    words = [_SYNONYM_MAP.get(w, w) for w in words]

    return set(words)


# Synonym mapping for common disease term variations
# Maps alternative terms → canonical term
_SYNONYM_MAP = {
    # Breast-ovarian cancer
    "brca": "breast",
    "ovary": "ovarian",
    "mammary": "breast",

    # Epilepsy/seizure
    "seizure": "epilepsy",
    "seizures": "epilepsy",
    "epileptic": "epilepsy",
    "convulsion": "epilepsy",
    "convulsions": "epilepsy",
    "myoclonic": "myoclonus",

    # Cancer
    "carcinoma": "cancer",
    "tumor": "cancer",
    "tumour": "cancer",
    "neoplasm": "cancer",
    "malignancy": "cancer",
    "adenocarcinoma": "cancer",

    # Intellectual disability
    "intellectual": "developmental",
    "mental": "developmental",
    "cognitive": "developmental",
    "retardation": "developmental",  # older term

    # Cardiac
    "heart": "cardiac",
    "cardiomyopathy": "cardiac",

    # Abbreviations
    "cf": "cystic_fibrosis",
    "sma": "spinal_muscular_atrophy",

    # Dravet syndrome (has multiple names)
    "dravet": "dravet_syndrome",
    "severe": "dravet_syndrome",
    "myoclonic": "dravet_syndrome",
    "infancy": "dravet_syndrome",
    "smei": "dravet_syndrome",  # abbreviation

    # Li-Fraumeni
    "li": "li_fraumeni",
    "fraumeni": "li_fraumeni",
    "lfs": "li_fraumeni",

    # Cystic fibrosis
    "cf": "cystic_fibrosis",
    "cystic": "cystic_fibrosis",
    "fibrosis": "cystic_fibrosis",

    # Spelling variations
    "favism": "g6pd",
    "deficiency": "deficient",
}


def get_disease_match_confidence(
    clinvar_disease: Optional[str],
    orphanet_disease: Optional[str],
) -> tuple[bool, float, str]:
    """
    Extended disease matching with confidence score and explanation.

    Args:
        clinvar_disease: Disease from ClinVar
        orphanet_disease: Disease from Orphanet

    Returns:
        (matches: bool, similarity_score: float, explanation: str)

    Example:
        >>> match, score, note = get_disease_match_confidence(
        ...     "Breast-ovarian cancer, familial 2",
        ...     "Hereditary breast and ovarian cancer syndrome"
        ... )
        >>> match
        True
        >>> score
        0.5
        >>> note
        'Matched words: {breast, ovarian, cancer} (3/6 total words)'
    """
    if not clinvar_disease or not orphanet_disease:
        return False, 0.0, "Missing disease name"

    clinvar_words = _normalize_disease_name(clinvar_disease)
    orphanet_words = _normalize_disease_name(orphanet_disease)

    if not clinvar_words or not orphanet_words:
        return False, 0.0, "Disease name normalization failed"

    intersection = clinvar_words & orphanet_words
    union = clinvar_words | orphanet_words

    similarity = len(intersection) / len(union) if union else 0.0
    matches = similarity >= 0.5

    if matches:
        note = (
            f"Matched words: {{{', '.join(sorted(intersection))}}} "
            f"({len(intersection)}/{len(union)} total words)"
        )
    else:
        note = (
            f"Low overlap: ClinVar={{{', '.join(sorted(clinvar_words))}}} "
            f"vs Orphanet={{{', '.join(sorted(orphanet_words))}}} "
            f"(similarity={similarity:.2f} < 0.5 threshold)"
        )

    return matches, similarity, note


# Test cases (for manual verification)
if __name__ == "__main__":
    test_cases = [
        # Should match
        ("Breast-ovarian cancer, familial 2", "Hereditary breast and ovarian cancer syndrome", True),
        ("Li-Fraumeni syndrome", "Li-Fraumeni syndrome type 1", True),
        ("Dravet syndrome", "Severe myoclonic epilepsy of infancy", True),
        ("Cystic fibrosis", "CF", True),

        # Should NOT match
        ("Li-Fraumeni syndrome", "Autism spectrum disorder", False),
        ("Dravet syndrome", "Familial febrile seizures", False),
        ("BRCA1-related cancer", "Lynch syndrome", False),
        ("Epilepsy", "Intellectual disability", False),
    ]

    print("Disease Matching Test Cases")
    print("=" * 80)

    for clinvar, orphanet, expected in test_cases:
        result, score, note = get_disease_match_confidence(clinvar, orphanet)
        status = "PASS" if result == expected else "FAIL"

        print(f"\n{status}: {result} (expected {expected})")
        print(f"  ClinVar:  {clinvar}")
        print(f"  Orphanet: {orphanet}")
        print(f"  Score:    {score:.3f}")
        print(f"  Note:     {note}")

    print("\n" + "=" * 80)

