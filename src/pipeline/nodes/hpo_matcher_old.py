"""
src/pipeline/nodes/hpo_matcher.py

HPO Matcher Node — maps patient HPO terms to diseases and genes.

For each variant flowing through the pipeline this node answers two questions:
  1. Does the variant's gene appear in diseases that match the patient's phenotype?
  2. Which Orphanet disease best explains the combination of gene + patient HPO terms?

Data sources (both already on pod under DATABASE_DIR):
  phenotype.hpoa   — HPO project annotations: disease_id ↔ HPO term mappings
                     Covers OMIM (8614 entries), Orphanet (4337), DECIPHER (47)
  genes_diseases.xml — Orphanet: gene symbol ↔ Orphanet disease + inheritance mode

Strategy:
  1. Load phenotype.hpoa at module level → two indexes:
       hpo_to_diseases:   HP:0001250 → {OMIM:123456, ORPHA:456, ...}
       disease_to_hpos:   OMIM:123456 → {HP:0001250, HP:0002187, ...}
  2. Load genes_diseases.xml at module level → two indexes:
       gene_to_diseases:  BRCA2 → [{orpha_id, disease_name, inheritance}, ...]
       disease_to_genes:  ORPHA:227535 → [{symbol, inheritance}, ...]
  3. Per variant:
     a. Get present patient HPO IDs from state.
     b. Look up which diseases involve state["gene"] via gene_to_diseases.
     c. For each such disease, score HPO overlap with patient terms.
     d. Pick the best-matching disease; populate state fields.
     e. Also build hpo_matched_genes: genes that share ≥1 patient HPO term
        regardless of whether they match the current variant's gene.

Scoring:
  Jaccard-style overlap: |patient_hpos ∩ disease_hpos| / |patient_hpos ∪ disease_hpos|
  Minimum overlap threshold: 1 shared term (score > 0.0) to include in results.
  Best disease = highest overlap score among diseases associated with the gene.

Node contract:
  Input fields read : patient_hpo_terms (List[Dict]), gene (str)
  Output fields set :
    hpo_matched_genes        (list)  — gene symbols sharing ≥1 patient HPO term
    gene_orphanet_diseases   (list)  — Orphanet disease names for this variant's gene
    matched_orphanet_disease (str)   — best-matching disease name (highest HPO overlap)
    orphanet_id              (str)   — e.g. "ORPHA:199"
    alternate_molecular_diagnosis (str) — top non-current-gene HPO-matched gene, if any
  Side effects: none (all data local)
"""

import logging
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from src.pipeline.state import VariantState
from src.config import DATABASE_PATHS, OPTIONAL_DATABASE_PATHS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level indexes — loaded once on first node invocation
# ---------------------------------------------------------------------------

# phenotype.hpoa indexes
_HPO_TO_DISEASES:    Dict[str, Set[str]] = defaultdict(set)
# disease_id (e.g. "OMIM:619340") → set of HPO IDs
_DISEASE_TO_HPOS:    Dict[str, Set[str]] = defaultdict(set)
# disease_id → disease name string
_DISEASE_ID_TO_NAME: Dict[str, str]      = {}

# Orphanet genes_diseases.xml indexes
# gene symbol (uppercase) → list of {orpha_id, disease_name, inheritance}
_GENE_TO_DISEASES:   Dict[str, List[dict]] = defaultdict(list)
# orpha_id (e.g. "ORPHA:199") → list of {symbol, inheritance}
_DISEASE_TO_GENES:   Dict[str, List[dict]] = defaultdict(list)

_HPOA_LOADED      = False
_ORPHANET_LOADED  = False

# Orphanet inheritance TSV
# gene symbol (upper) → set of inheritance strings
_GENE_TO_INHERITANCE: Dict[str, Set[str]] = defaultdict(set)
_INHERITANCE_LOADED = False

# OMIM morbidmap fallback
# gene symbol (upper) → set of inheritance strings
_OMIM_GENE_TO_INHERITANCE: Dict[str, Set[str]] = defaultdict(set)
_OMIM_LOADED = False

# Orphanet inheritance TSV
# gene symbol (upper) → set of inheritance strings
_GENE_TO_INHERITANCE: Dict[str, Set[str]] = defaultdict(set)
_INHERITANCE_LOADED = False

# OMIM morbidmap fallback
# gene symbol (upper) → set of inheritance strings
_OMIM_GENE_TO_INHERITANCE: Dict[str, Set[str]] = defaultdict(set)
_OMIM_LOADED = False


# ---------------------------------------------------------------------------
# Loader A — phenotype.hpoa
# ---------------------------------------------------------------------------

def _load_hpoa() -> None:
    """
    Parse phenotype.hpoa (TSV) into _HPO_TO_DISEASES and _DISEASE_TO_HPOS.

    Relevant columns (0-based after stripping comment lines):
      0  database_id   e.g. "OMIM:619340"
      1  disease_name  e.g. "Developmental and epileptic encephalopathy 96"
      2  qualifier     e.g. "" or "NOT" (negated associations — skip these)
      3  hpo_id        e.g. "HP:0011097"

    Lines beginning with '#' are header/comment lines — skip.
    The column-header line itself starts with 'database_id' — skip.
    """
    global _HPOA_LOADED

    if _HPOA_LOADED:
        return

    hpoa_path = DATABASE_PATHS.get("hpo_annotations")
    if not hpoa_path or not Path(hpoa_path).exists():
        logger.error(
            f"phenotype.hpoa not found at {hpoa_path}. "
            "HPO→disease matching will be unavailable."
        )
        _HPOA_LOADED = True
        return

    loaded = 0
    skipped_negated = 0

    with open(hpoa_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("#") or line.startswith("database_id"):
                continue

            parts = line.split("\t")
            if len(parts) < 4:
                continue

            disease_id   = parts[0].strip()
            disease_name = parts[1].strip()
            qualifier    = parts[2].strip()
            hpo_id       = parts[3].strip()

            # Skip negated associations ("NOT seizure")
            if qualifier.upper() == "NOT":
                skipped_negated += 1
                continue

            if not disease_id or not hpo_id:
                continue

            _HPO_TO_DISEASES[hpo_id].add(disease_id)
            _DISEASE_TO_HPOS[disease_id].add(hpo_id)
            _DISEASE_ID_TO_NAME[disease_id] = disease_name
            loaded += 1

    logger.info(
        f"phenotype.hpoa loaded: {loaded} associations, "
        f"{len(_DISEASE_TO_HPOS)} diseases, "
        f"{len(_HPO_TO_DISEASES)} HPO terms, "
        f"{skipped_negated} negated associations skipped."
    )
    _HPOA_LOADED = True


# ---------------------------------------------------------------------------
# Loader B — Orphanet genes_diseases.xml
# ---------------------------------------------------------------------------

def _load_orphanet() -> None:
    """
    Parse Orphanet genes_diseases.xml into _GENE_TO_DISEASES and _DISEASE_TO_GENES.

    XML structure (en_product6 format):
      <JDBOR>
        <DisorderList>
          <Disorder id="...">
            <OrphaCode>199</OrphaCode>
            <Name lang="en">Disease name</Name>
            <DisorderGeneAssociationList>
              <DisorderGeneAssociation>
                <Gene id="...">
                  <Symbol>BRCA2</Symbol>
                </Gene>
                <DisorderGeneAssociationType>
                  <Name lang="en">Disease-causing germline mutation(s) in</Name>
                </DisorderGeneAssociationType>
              </DisorderGeneAssociation>
            </DisorderGeneAssociationList>
          </Disorder>
        </DisorderList>
      </JDBOR>

    Inheritance is stored at the Disorder level in some versions under
    <TypeOfInheritanceList>; we extract it if present, else "unknown".
    """
    global _ORPHANET_LOADED

    if _ORPHANET_LOADED:
        return

    orphanet_path = OPTIONAL_DATABASE_PATHS.get("orphanet_genes")
    if not orphanet_path or not Path(orphanet_path).exists():
        logger.warning(
            f"genes_diseases.xml not found at {orphanet_path}. "
            "Orphanet gene-disease mapping will be unavailable."
        )
        _ORPHANET_LOADED = True
        return

    try:
        tree = ET.parse(str(orphanet_path))
        root = tree.getroot()
    except ET.ParseError as exc:
        logger.error(f"Failed to parse genes_diseases.xml: {exc}")
        _ORPHANET_LOADED = True
        return

    disorder_list = root.find(".//DisorderList")
    if disorder_list is None:
        logger.error("genes_diseases.xml: no <DisorderList> element found.")
        _ORPHANET_LOADED = True
        return

    disorder_count = 0
    gene_assoc_count = 0

    for disorder in disorder_list.findall("Disorder"):
        # OrphaCode → ORPHA:XXXX
        orpha_code_el = disorder.find("OrphaCode")
        if orpha_code_el is None or not orpha_code_el.text:
            continue
        orpha_id     = f"ORPHA:{orpha_code_el.text.strip()}"
        disease_name_el = disorder.find("Name")
        disease_name = disease_name_el.text.strip() if disease_name_el is not None else ""

        # Inheritance — may be absent in this product file
        inheritance = "unknown"
        for inh_el in disorder.findall(".//TypeOfInheritance/Name"):
            if inh_el.text:
                inheritance = inh_el.text.strip()
                break   # take first listed

        # Gene associations
        for assoc in disorder.findall(".//DisorderGeneAssociation"):
            gene_el = assoc.find("Gene/Symbol")
            if gene_el is None or not gene_el.text:
                continue
            gene_symbol = gene_el.text.strip().upper()

            _GENE_TO_DISEASES[gene_symbol].append({
                "orpha_id":     orpha_id,
                "disease_name": disease_name,
                "inheritance":  inheritance,
            })
            _DISEASE_TO_GENES[orpha_id].append({
                "symbol":      gene_symbol,
                "inheritance": inheritance,
            })
            gene_assoc_count += 1

        disorder_count += 1

    logger.info(
        f"genes_diseases.xml loaded: {disorder_count} disorders, "
        f"{gene_assoc_count} gene-disease associations, "
        f"{len(_GENE_TO_DISEASES)} unique genes."
    )
    _ORPHANET_LOADED = True


# ---------------------------------------------------------------------------
# Loader C — Orphanet inheritance TSV
# ---------------------------------------------------------------------------

def _load_orphanet_inheritance_tsv() -> None:
    """
    Load Orphanet gene-inheritance mappings from TSV.
    Columns: geneSymbol, inheritance (may contain comma-separated modes).
    """
    global _INHERITANCE_LOADED

    if _INHERITANCE_LOADED:
        return

    tsv_path = OPTIONAL_DATABASE_PATHS.get("orphanet_inheritance_tsv")
    if not tsv_path or not Path(tsv_path).exists():
        logger.warning(
            f"orphanet_inheritance_tsv not found at {tsv_path}. "
            "Will fall back to OMIM morbidmap."
        )
        _INHERITANCE_LOADED = True
        return

    import csv
    loaded = 0

    with open(tsv_path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            if row is None:
                continue
            gene = (row.get("geneSymbol") or "").strip().upper()
            inh  = (row.get("inheritance") or "").strip()
            if gene and inh and inh not in ("", "Not applicable"):
                # TSV may have comma-separated multiple inheritance modes
                for mode in inh.split(","):
                    mode = mode.strip()
                    if mode:
                        _GENE_TO_INHERITANCE[gene].add(mode)
                        loaded += 1

    logger.info(
        f"orphanet_inheritance_tsv loaded: {loaded} rows, "
        f"{len(_GENE_TO_INHERITANCE)} unique genes with inheritance."
    )
    _INHERITANCE_LOADED = True


# ---------------------------------------------------------------------------
# Loader D — OMIM morbidmap (fallback)
# ---------------------------------------------------------------------------

def _load_omim_morbidmap() -> None:
    """
    Load OMIM gene-inheritance mappings from morbidmap.txt as fallback.
    Extracts inheritance keywords from phenotype descriptions.
    """
    global _OMIM_LOADED

    if _OMIM_LOADED:
        return

    path = OPTIONAL_DATABASE_PATHS.get("omim_morbidmap")
    if not path or not Path(path).exists():
        logger.warning(
            f"morbidmap.txt not found at {path}. "
            "OMIM inheritance fallback will be unavailable."
        )
        _OMIM_LOADED = True
        return

    # Map OMIM inheritance keywords to standard terms
    _OMIM_INH_MAP = {
        "autosomal dominant":   "Autosomal dominant",
        "autosomal recessive":  "Autosomal recessive",
        "x-linked dominant":    "X-linked dominant",
        "x-linked recessive":   "X-linked recessive",
        "x-linked":             "X-linked recessive",  # default XL to XLR
        "mitochondrial":        "Mitochondrial inheritance",
        "y-linked":             "Y-linked",
    }

    loaded = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue

            phenotype = (parts[0] or "").strip().lower()
            gene = (parts[2] or "").strip().upper()

            if not gene:
                continue

            # Extract inheritance keywords from phenotype text
            for keyword, standard_term in _OMIM_INH_MAP.items():
                if keyword in phenotype:
                    _OMIM_GENE_TO_INHERITANCE[gene].add(standard_term)
                    loaded += 1
                    break

    logger.info(
        f"morbidmap.txt loaded: {loaded} gene-inheritance mappings, "
        f"{len(_OMIM_GENE_TO_INHERITANCE)} unique genes."
    )
    _OMIM_LOADED = True


# ---------------------------------------------------------------------------
# Loader C — Orphanet inheritance TSV
# ---------------------------------------------------------------------------

def _load_orphanet_inheritance_tsv() -> None:
    """
    Load Orphanet gene-inheritance mappings from TSV.
    Columns: geneSymbol, inheritance (may contain comma-separated modes).
    """
    global _INHERITANCE_LOADED

    if _INHERITANCE_LOADED:
        return

    tsv_path = OPTIONAL_DATABASE_PATHS.get("orphanet_inheritance_tsv")
    if not tsv_path or not Path(tsv_path).exists():
        logger.warning(
            f"orphanet_inheritance_tsv not found at {tsv_path}. "
            "Will fall back to OMIM morbidmap."
        )
        _INHERITANCE_LOADED = True
        return

    import csv
    loaded = 0

    with open(tsv_path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            if row is None:
                continue
            gene = (row.get("geneSymbol") or "").strip().upper()
            inh  = (row.get("inheritance") or "").strip()
            if gene and inh and inh not in ("", "Not applicable"):
                # TSV may have comma-separated multiple inheritance modes
                for mode in inh.split(","):
                    mode = mode.strip()
                    if mode:
                        _GENE_TO_INHERITANCE[gene].add(mode)
                        loaded += 1

    logger.info(
        f"orphanet_inheritance_tsv loaded: {loaded} rows, "
        f"{len(_GENE_TO_INHERITANCE)} unique genes with inheritance."
    )
    _INHERITANCE_LOADED = True


# ---------------------------------------------------------------------------
# Loader D — OMIM morbidmap (fallback)
# ---------------------------------------------------------------------------

def _load_omim_morbidmap() -> None:
    """
    Load OMIM gene-inheritance mappings from morbidmap.txt as fallback.
    Extracts inheritance keywords from phenotype descriptions.
    """
    global _OMIM_LOADED

    if _OMIM_LOADED:
        return

    path = OPTIONAL_DATABASE_PATHS.get("omim_morbidmap")
    if not path or not Path(path).exists():
        logger.warning(
            f"morbidmap.txt not found at {path}. "
            "OMIM inheritance fallback will be unavailable."
        )
        _OMIM_LOADED = True
        return

    # Map OMIM inheritance keywords to standard terms
    _OMIM_INH_MAP = {
        "autosomal dominant":   "Autosomal dominant",
        "autosomal recessive":  "Autosomal recessive",
        "x-linked dominant":    "X-linked dominant",
        "x-linked recessive":   "X-linked recessive",
        "x-linked":             "X-linked recessive",  # default XL to XLR
        "mitochondrial":        "Mitochondrial inheritance",
        "y-linked":             "Y-linked",
    }

    loaded = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue

            phenotype = (parts[0] or "").strip().lower()
            gene = (parts[2] or "").strip().upper()

            if not gene:
                continue

            # Extract inheritance keywords from phenotype text
            for keyword, standard_term in _OMIM_INH_MAP.items():
                if keyword in phenotype:
                    _OMIM_GENE_TO_INHERITANCE[gene].add(standard_term)
                    loaded += 1
                    break

    logger.info(
        f"morbidmap.txt loaded: {loaded} gene-inheritance mappings, "
        f"{len(_OMIM_GENE_TO_INHERITANCE)} unique genes."
    )
    _OMIM_LOADED = True


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _get_gene_inheritance(gene: str) -> Optional[str]:
    """
    Return inheritance mode string for a gene.
    Priority: Orphanet TSV → OMIM morbidmap → None
    Collapses multiple modes to a joined string.
    """
    gene_upper = gene.upper()

    modes = _GENE_TO_INHERITANCE.get(gene_upper)
    if not modes:
        modes = _OMIM_GENE_TO_INHERITANCE.get(gene_upper)
    if not modes:
        return None

    # If only one mode, return it directly
    if len(modes) == 1:
        return next(iter(modes))

    # Multiple modes — return joined string
    return ", ".join(sorted(modes))


def _jaccard_overlap(patient_hpos: Set[str], disease_hpos: Set[str]) -> float:
    """
    Jaccard index: |intersection| / |union|.
    Returns 0.0 if either set is empty.
    """
    if not patient_hpos or not disease_hpos:
        return 0.0
    intersection = len(patient_hpos & disease_hpos)
    union        = len(patient_hpos | disease_hpos)
    return intersection / union if union > 0 else 0.0


def _score_gene_diseases(
    gene: str,
    patient_hpo_ids: Set[str],
) -> List[Tuple[float, str, str, str]]:
    """
    Score all Orphanet diseases associated with `gene` against patient HPO terms.

    Returns list of (score, orpha_id, disease_name, inheritance) sorted
    descending by score. Only entries with score > 0 are returned.
    """
    gene_upper = gene.upper()
    diseases   = _GENE_TO_DISEASES.get(gene_upper, [])

    scored = []
    for d in diseases:
        orpha_id     = d["orpha_id"]
        disease_name = d["disease_name"]
        inheritance  = d["inheritance"]

        disease_hpos = _DISEASE_TO_HPOS.get(orpha_id, set())

        # phenotype.hpoa uses OMIM/ORPHA disease IDs; try both formats
        # Some Orphanet diseases also have OMIM cross-refs — use union of hpos
        # for the best coverage. For now use direct ORPHA lookup only.
        score = _jaccard_overlap(patient_hpo_ids, disease_hpos)

        if score > 0.0:
            scored.append((score, orpha_id, disease_name, inheritance))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

def hpo_matcher_node(state: VariantState) -> dict:
    """
    LangGraph node: match patient HPO terms to Orphanet diseases for this variant's gene.

    Returns dict with keys:
      hpo_matched_genes        — gene symbols sharing ≥1 patient HPO term
      gene_orphanet_diseases   — disease names linked to this variant's gene
      matched_orphanet_disease — best-matching disease name (highest Jaccard score)
      orphanet_id              — e.g. "ORPHA:199"
      alternate_molecular_diagnosis — top non-current-gene HPO-matched gene
      gene_orphanet_inheritance — inheritance mode for this gene (e.g. "Autosomal dominant")
    """
    _load_hpoa()
    _load_orphanet()
    _load_orphanet_inheritance_tsv()
    _load_omim_morbidmap()

    patient_hpo_terms: List[dict] = state.get("patient_hpo_terms") or []
    gene: str = (state.get("gene") or "").strip().upper()

    # ---- No HPO terms → nothing to match ------------------------------------
    if not patient_hpo_terms:
        logger.info("hpo_matcher_node: no patient HPO terms — returning empty matches.")
        return {
            "hpo_matched_genes":        [],
            "gene_orphanet_diseases":   [],
            "matched_orphanet_disease": None,
            "orphanet_id":              None,
            "alternate_molecular_diagnosis": None,
            "gene_orphanet_inheritance": None,
        }

    # Present terms only — negated terms don't drive prioritisation
    present_hpo_ids: Set[str] = {
        t["hpo_id"]
        for t in patient_hpo_terms
        if t.get("present", True) and t.get("hpo_id")
    }

    if not present_hpo_ids:
        logger.info("hpo_matcher_node: all HPO terms negated — returning empty matches.")
        return {
            "hpo_matched_genes":        [],
            "gene_orphanet_diseases":   [],
            "matched_orphanet_disease": None,
            "orphanet_id":              None,
            "alternate_molecular_diagnosis": None,
            "gene_orphanet_inheritance": None,
        }

    # ---- 1. Find all genes sharing ≥1 patient HPO term ----------------------
    # For each patient HPO ID, look up diseases; for each disease look up genes.
    hpo_matched_gene_set: Set[str] = set()
    for hpo_id in present_hpo_ids:
        diseases_for_hpo = _HPO_TO_DISEASES.get(hpo_id, set())
        for disease_id in diseases_for_hpo:
            # Check both Orphanet and OMIM disease IDs in gene index
            # _GENE_TO_DISEASES is keyed by gene symbol — we need reverse lookup
            genes_for_disease = _DISEASE_TO_GENES.get(disease_id, [])
            for g in genes_for_disease:
                hpo_matched_gene_set.add(g["symbol"])

    hpo_matched_genes = sorted(hpo_matched_gene_set)

    # ---- 2. Diseases associated with this variant's gene --------------------
    gene_diseases = _GENE_TO_DISEASES.get(gene, [])
    gene_orphanet_diseases = [d["disease_name"] for d in gene_diseases]

    # ---- 3. Score diseases for this gene against patient HPO terms ----------
    scored = _score_gene_diseases(gene, present_hpo_ids)

    matched_orphanet_disease: Optional[str] = None
    orphanet_id:              Optional[str] = None

    if scored:
        best_score, best_orpha_id, best_disease_name, _ = scored[0]
        matched_orphanet_disease = best_disease_name
        orphanet_id              = best_orpha_id
        logger.info(
            f"hpo_matcher_node: best match for gene {gene} → "
            f"{best_disease_name} ({best_orpha_id}), "
            f"Jaccard={best_score:.3f}"
        )
    else:
        logger.info(
            f"hpo_matcher_node: gene {gene} has no HPO-overlapping Orphanet disease "
            f"for the {len(present_hpo_ids)} patient terms."
        )

    # ---- 4. Alternate molecular diagnosis -----------------------------------
    # Top HPO-matched gene that is NOT the current variant's gene.
    # Useful for the report: "consider also checking GENE_X".
    alternate_molecular_diagnosis: Optional[str] = None
    for g in hpo_matched_genes:
        if g != gene:
            # Score this gene's diseases too so we report a meaningful candidate
            alt_scored = _score_gene_diseases(g, present_hpo_ids)
            if alt_scored:
                _, _, alt_disease, _ = alt_scored[0]
                alternate_molecular_diagnosis = f"{g} ({alt_disease})"
                break

    # ---- 5. Gene inheritance -----------------------------------------------
    gene_inheritance = _get_gene_inheritance(gene)
    if gene_inheritance:
        logger.info(f"hpo_matcher_node: {gene} inheritance → {gene_inheritance}")
    else:
        logger.info(f"hpo_matcher_node: no inheritance found for {gene}")

    logger.info(
        f"hpo_matcher_node: {len(hpo_matched_genes)} HPO-matched genes, "
        f"{len(gene_orphanet_diseases)} Orphanet diseases for {gene}."
    )

    return {
        "hpo_matched_genes":             hpo_matched_genes,
        "gene_orphanet_diseases":        gene_orphanet_diseases,
        "matched_orphanet_disease":      matched_orphanet_disease,
        "orphanet_id":                   orphanet_id,
        "alternate_molecular_diagnosis": alternate_molecular_diagnosis,
        "gene_orphanet_inheritance":     gene_inheritance,
    }
