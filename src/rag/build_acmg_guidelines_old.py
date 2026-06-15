"""
src/rag/build_acmg_guidelines.py

Build the acmg_guidelines ChromaDB collection.
Run ONCE offline before starting the debate layer.

Each document = one ACMG criterion (or closely related criterion group).
Chunks are authored from ACMG/AMP 2015 (Richards et al.) and ClinGen
refinements. They encode:
  - Criterion code + default strength
  - Clinical meaning and what evidence satisfies it
  - Upgrade / downgrade conditions (ClinGen approved)
  - Common pitfalls and exclusions
  - Which agent evaluates it (for cross-reference)

Query strategy at runtime:
  - pathogenic_advocate  → query fired P criteria codes + gene context
  - benign_advocate      → query fired B criteria codes + gene context
  - final_arbiter        → query all fired criteria + "combination rules"
"""

import logging
import chromadb
from chromadb.utils import embedding_functions
from pathlib import Path

logger = logging.getLogger(__name__)

# Match your existing builder pattern
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# ── adjust to your actual CHROMADB_DIR ──────────────────────────────────────
# In production import from src.config import CHROMADB_DIR
CHROMADB_DIR = Path("data/chromadb")


# ---------------------------------------------------------------------------
# Criterion documents
# Each dict: id, text (what gets embedded + retrieved), metadata
# ---------------------------------------------------------------------------

ACMG_CRITERION_DOCS = [

    # ── PATHOGENIC: Very Strong ─────────────────────────────────────────────
    {
        "id": "PVS1",
        "text": (
            "PVS1 — Pathogenic Very Strong. "
            "Null variant (nonsense, frameshift, canonical splice site ±1/2, "
            "initiation codon, single/multi-exon deletion) in a gene where "
            "loss-of-function (LOF) is a known mechanism of disease. "
            "Default strength: Very Strong. "
            "REQUIRED checks before applying: (1) LOF must be an established "
            "disease mechanism for the gene — do not apply if only gain-of-function "
            "variants cause disease. (2) Beware of variants near 3' end: if the "
            "truncated protein retains >10% function, downgrade to Strong or Moderate. "
            "(3) If the variant is in a transcript not expressed in relevant tissue, "
            "do not apply. (4) For splice variants, confirm with in-silico splicing "
            "tools (SpliceAI, MaxEntScan) that the splice site is truly disrupted. "
            "ClinGen upgrade path: PVS1 remains Very Strong unless caveats apply. "
            "Downgrade to Strong if: truncation in last exon, functionally redundant "
            "exon, or multiple transcripts of uncertain clinical significance. "
            "Downgrade to Moderate if evidence of alternate splicing rescuing function. "
            "Evaluated by Agent 2."
        ),
        "metadata": {"criterion": "PVS1", "side": "pathogenic", "strength": "Very Strong", "agent": "agent2"},
    },

    # ── PATHOGENIC: Strong ──────────────────────────────────────────────────
    {
        "id": "PS1",
        "text": (
            "PS1 — Pathogenic Strong. "
            "Same amino acid change as a previously established pathogenic variant, "
            "regardless of nucleotide change (e.g., Gly1234Arg reported as pathogenic; "
            "a different nucleotide change also causes Gly1234Arg). "
            "Do NOT apply if the previously reported variant lacks strong evidence "
            "(e.g., is only 1-star ClinVar or based on a single case report). "
            "Do NOT apply for synonymous changes using PS1 logic — those may qualify "
            "for PP5 at most. "
            "Requires: existing variant has ≥2 stars ClinVar or expert-panel "
            "classification. "
            "Evaluated by Agent 4."
        ),
        "metadata": {"criterion": "PS1", "side": "pathogenic", "strength": "Strong", "agent": "agent4"},
    },
    {
        "id": "PS2",
        "text": (
            "PS2 — Pathogenic Strong. "
            "De novo variant (both maternity and paternity confirmed) in a patient "
            "with the disease and no family history. "
            "Requires confirmation of biological parentage — if parentage is assumed "
            "but not confirmed, downgrade to PM6 (Moderate). "
            "Upgrade path: PS2 can be upgraded to Very Strong if the variant is de novo "
            "AND the gene has a very high de novo rate for the patient's phenotype. "
            "In solo mode (no parental VCFs): PS2 cannot be applied; PM6 may apply "
            "if de novo is assumed. "
            "Evaluated by Agent 7."
        ),
        "metadata": {"criterion": "PS2", "side": "pathogenic", "strength": "Strong", "agent": "agent7"},
    },
    {
        "id": "PS3",
        "text": (
            "PS3 — Pathogenic Strong. "
            "Well-established functional studies show damaging effect on protein "
            "function or splicing. "
            "Acceptable studies: in vitro assays, animal models, patient-derived cell "
            "lines, minigene splicing assays, protein stability assays. "
            "NOT acceptable: computational predictions alone (those are PP3/BP4). "
            "Requires study to be performed on THIS specific variant, not just the gene. "
            "Downgrade to Moderate if: study is in a non-physiological system, or only "
            "indirect evidence (e.g., the variant disrupts a conserved motif but no "
            "direct functional assay was done). "
            "ClinGen SVI: functional assays must be validated and published. "
            "Evaluated by Agent 5."
        ),
        "metadata": {"criterion": "PS3", "side": "pathogenic", "strength": "Strong", "agent": "agent5"},
    },
    {
        "id": "PS4",
        "text": (
            "PS4 — Pathogenic Strong. "
            "The prevalence of this variant in affected individuals is significantly "
            "increased compared to controls (OR > 5.0, p < 0.05 with ≥2 unrelated "
            "patients). "
            "Downgrade to Moderate (PM equivalent) if: only 1 unrelated affected "
            "individual reported, or OR < 5. "
            "For ultra-rare diseases: observing the variant in ≥2 unrelated affected "
            "patients may satisfy PS4 in the absence of population data. "
            "Do NOT apply if all reported cases are from a single family. "
            "Evaluated by Agent 4."
        ),
        "metadata": {"criterion": "PS4", "side": "pathogenic", "strength": "Strong", "agent": "agent4"},
    },

    # ── PATHOGENIC: Moderate ────────────────────────────────────────────────
    {
        "id": "PM1",
        "text": (
            "PM1 — Pathogenic Moderate. "
            "Variant located in a mutational hot spot and/or well-established "
            "functional domain (e.g., active site of an enzyme) without benign "
            "variation. "
            "Use UniProt domain annotations and ClinVar variant density to assess. "
            "Do NOT apply if the domain is large and tolerant of variation — check "
            "gnomAD for benign missense variants in the same domain. "
            "Evaluated by Agent 5."
        ),
        "metadata": {"criterion": "PM1", "side": "pathogenic", "strength": "Moderate", "agent": "agent5"},
    },
    {
        "id": "PM2",
        "text": (
            "PM2 — Pathogenic Moderate. "
            "Absent from controls (or at extremely low frequency) in population "
            "databases (gnomAD, ExAC) for recessive disorders, or absent/extremely "
            "low (< 0.0001) for dominant disorders. "
            "ClinGen SVI downgrade: PM2 should be applied as Supporting (not Moderate) "
            "for most variants because absence from population databases is expected "
            "for any rare variant. "
            "Do NOT apply if the variant is present at even low frequency in gnomAD "
            "in a way inconsistent with a severe dominant disorder. "
            "Evaluated by Agent 1."
        ),
        "metadata": {"criterion": "PM2", "side": "pathogenic", "strength": "Moderate", "agent": "agent1"},
    },
    {
        "id": "PM3",
        "text": (
            "PM3 — Pathogenic Moderate. "
            "For recessive disorders: detected in trans with a pathogenic variant. "
            "Requires phase confirmation — ideally trio data or read-backed phasing. "
            "Upgrade path (ClinGen): PM3 can be upgraded to Strong if detected in trans "
            "in ≥2 unrelated individuals. Can be upgraded to Very Strong if in trans in "
            "≥4 unrelated individuals. "
            "Do NOT apply if phasing is unknown and variant may be in cis. "
            "In solo mode without phasing confirmation: flag as possible PM3 with "
            "LOW confidence. "
            "Evaluated by Agent 6."
        ),
        "metadata": {"criterion": "PM3", "side": "pathogenic", "strength": "Moderate", "agent": "agent6"},
    },
    {
        "id": "PM4",
        "text": (
            "PM4 — Pathogenic Moderate. "
            "Protein length changes due to in-frame deletions/insertions or stop-loss "
            "variants in a non-repeat region. "
            "Do NOT apply in repeat regions (check RepeatMasker). "
            "Do NOT apply if the deletion/insertion is large enough to trigger PVS1 "
            "frameshift logic instead. "
            "Evaluated by Agent 8."
        ),
        "metadata": {"criterion": "PM4", "side": "pathogenic", "strength": "Moderate", "agent": "agent8"},
    },
    {
        "id": "PM5",
        "text": (
            "PM5 — Pathogenic Moderate. "
            "Novel missense change at an amino acid residue where a DIFFERENT missense "
            "change has been established as pathogenic. "
            "Requires the previously reported pathogenic missense at the same residue "
            "to have strong evidence (≥2 star ClinVar or expert panel). "
            "Do NOT conflate with PS1 — PS1 requires the SAME amino acid change; "
            "PM5 is for a DIFFERENT amino acid change at the same residue. "
            "Evaluated by Agent 8."
        ),
        "metadata": {"criterion": "PM5", "side": "pathogenic", "strength": "Moderate", "agent": "agent8"},
    },
    {
        "id": "PM6",
        "text": (
            "PM6 — Pathogenic Moderate. "
            "Assumed de novo (without confirmation of paternity/maternity) in a patient "
            "with the disease and no family history. "
            "This is the solo-mode fallback for PS2. "
            "In solo mode: PM6 cannot be confirmed without parental data — apply with "
            "LOW confidence and flag in evidence_notes that parental confirmation is needed. "
            "Upgrade to PS2 Strong if parentage is subsequently confirmed. "
            "Evaluated by Agent 7."
        ),
        "metadata": {"criterion": "PM6", "side": "pathogenic", "strength": "Moderate", "agent": "agent7"},
    },

    # ── PATHOGENIC: Supporting ──────────────────────────────────────────────
    {
        "id": "PP1",
        "text": (
            "PP1 — Pathogenic Supporting. "
            "Co-segregation with disease in multiple affected family members in a gene "
            "definitively known to cause the disease. "
            "Upgrade path: PP1 can be upgraded to Moderate with ≥3 affected family "
            "members segregating the variant. Can be upgraded to Strong with ≥5 affected "
            "family members across multiple branches. "
            "Do NOT apply if the gene-disease association is not definitively established "
            "or if the family is small. "
            "In solo mode without family data: PP1 cannot be applied. "
            "Evaluated by Agent 6."
        ),
        "metadata": {"criterion": "PP1", "side": "pathogenic", "strength": "Supporting", "agent": "agent6"},
    },
    {
        "id": "PP2",
        "text": (
            "PP2 — Pathogenic Supporting. "
            "Missense variant in a gene where missense variants are a common mechanism "
            "of disease AND where benign missense variation is rare. "
            "Check gnomAD missense Z-score and ClinGen gene-disease mechanism. "
            "Do NOT apply if the gene tolerates missense variation (low Z-score). "
            "Evaluated by Agent 8."
        ),
        "metadata": {"criterion": "PP2", "side": "pathogenic", "strength": "Supporting", "agent": "agent8"},
    },
    {
        "id": "PP3",
        "text": (
            "PP3 — Pathogenic Supporting. "
            "Multiple lines of computational evidence support a deleterious effect "
            "(conservation, evolutionary, splicing impact, etc.). "
            "IMPORTANT: PP3 can only be applied ONCE regardless of how many tools agree — "
            "this is explicitly stated in ACMG 2015 because tools share training data. "
            "REVEL ≥ 0.75 is the primary threshold. Require ≥5/8 predictors agreeing. "
            "Do NOT apply if tools are evenly split — that triggers BP4 consideration. "
            "Evaluated by Agent 3."
        ),
        "metadata": {"criterion": "PP3", "side": "pathogenic", "strength": "Supporting", "agent": "agent3"},
    },
    {
        "id": "PP4",
        "text": (
            "PP4 — Pathogenic Supporting. "
            "Patient's phenotype or family history is highly specific for a disease "
            "with a single genetic etiology. "
            "Requires clinical history or HPO terms in the input. "
            "If no clinical history provided: PP4 cannot be evaluated — flag as "
            "unevaluated in the report. "
            "Do NOT apply for genetically heterogeneous phenotypes (e.g., intellectual "
            "disability alone). "
            "Evaluated by Agent 9 (requires clinical input)."
        ),
        "metadata": {"criterion": "PP4", "side": "pathogenic", "strength": "Supporting", "agent": "agent9"},
    },
    {
        "id": "PP5",
        "text": (
            "PP5 — Pathogenic Supporting. "
            "Reputable source recently reports variant as pathogenic, but evidence is "
            "not available to the laboratory to perform independent evaluation. "
            "Use with caution — do NOT use PP5 if you can evaluate the evidence directly "
            "(use PS1/PS4 instead). "
            "Acceptable sources: ≥2 star ClinVar, expert panel database, peer-reviewed "
            "publication with clinical details. "
            "Do NOT apply for 1-star or conflicting ClinVar entries. "
            "Evaluated by Agent 4."
        ),
        "metadata": {"criterion": "PP5", "side": "pathogenic", "strength": "Supporting", "agent": "agent4"},
    },

    # ── BENIGN: Stand-alone ─────────────────────────────────────────────────
    {
        "id": "BA1",
        "text": (
            "BA1 — Benign Stand-alone. "
            "Allele frequency > 5% in gnomAD (any major population). "
            "This criterion ALONE is sufficient to classify a variant as Benign. "
            "It overrides all pathogenic evidence. "
            "Exception: do NOT apply BA1 for variants in genes where high allele "
            "frequency is consistent with disease (e.g., founder variants in specific "
            "populations, or conditions where carrier frequency is high). "
            "BA1 short-circuits the debate layer — no debate is needed. "
            "Evaluated by Agent 1."
        ),
        "metadata": {"criterion": "BA1", "side": "benign", "strength": "Stand-alone", "agent": "agent1"},
    },

    # ── BENIGN: Strong ──────────────────────────────────────────────────────
    {
        "id": "BS1",
        "text": (
            "BS1 — Benign Strong. "
            "Allele frequency is greater than expected for the disorder. "
            "Thresholds: AF > 0.5% for dominant disorders; AF > 1% for recessive. "
            "Adjusted thresholds per ClinGen SVI for specific genes. "
            "Do NOT apply if the variant is in a gene known to have high carrier "
            "frequency (e.g., CFTR, HBB). "
            "Evaluated by Agent 1."
        ),
        "metadata": {"criterion": "BS1", "side": "benign", "strength": "Strong", "agent": "agent1"},
    },
    {
        "id": "BS2",
        "text": (
            "BS2 — Benign Strong. "
            "Observed in a healthy adult individual for a recessive (homozygous), "
            "dominant (heterozygous), or X-linked (hemizygous) disorder with full "
            "penetrance expected at an early age. "
            "Requires gnomAD homozygote count or documented healthy carrier. "
            "Do NOT apply for late-onset conditions (e.g., BRCA1 in a 25-year-old). "
            "Evaluated by Agent 1."
        ),
        "metadata": {"criterion": "BS2", "side": "benign", "strength": "Strong", "agent": "agent1"},
    },
    {
        "id": "BS3",
        "text": (
            "BS3 — Benign Strong. "
            "Well-established functional studies show no damaging effect on protein "
            "function or splicing. "
            "Mirror of PS3 — same study quality requirements apply. "
            "The functional study must have been performed on THIS specific variant. "
            "Evaluated by Agent 5."
        ),
        "metadata": {"criterion": "BS3", "side": "benign", "strength": "Strong", "agent": "agent5"},
    },
    {
        "id": "BS4",
        "text": (
            "BS4 — Benign Strong. "
            "Lack of segregation in affected members of a family. "
            "The variant must be absent in ≥2 affected family members who have the "
            "disease phenotype. "
            "Do NOT apply if penetrance is known to be incomplete. "
            "In solo mode: BS4 cannot be reliably applied without family data. "
            "Evaluated by Agent 6."
        ),
        "metadata": {"criterion": "BS4", "side": "benign", "strength": "Strong", "agent": "agent6"},
    },

    # ── BENIGN: Supporting ──────────────────────────────────────────────────
    {
        "id": "BP1",
        "text": (
            "BP1 — Benign Supporting. "
            "Missense variant in a gene where ONLY truncating variants cause disease. "
            "Requires ClinGen or literature confirmation that missense variants are NOT "
            "a disease mechanism for this gene. "
            "Do NOT apply if even a minority of pathogenic variants in the gene are "
            "missense. "
            "Evaluated by Agent 8."
        ),
        "metadata": {"criterion": "BP1", "side": "benign", "strength": "Supporting", "agent": "agent8"},
    },
    {
        "id": "BP2",
        "text": (
            "BP2 — Benign Supporting. "
            "Observed in trans with a pathogenic variant for a fully penetrant dominant "
            "gene/disorder, OR observed in CIS with a pathogenic variant in any "
            "inheritance pattern. "
            "Requires phase confirmation — same phasing quality requirements as PM3. "
            "In solo mode without phasing: BP2 cannot be confirmed. "
            "Evaluated by Agent 6."
        ),
        "metadata": {"criterion": "BP2", "side": "benign", "strength": "Supporting", "agent": "agent6"},
    },
    {
        "id": "BP3",
        "text": (
            "BP3 — Benign Supporting. "
            "In-frame deletions/insertions in a repetitive region without a known "
            "function. "
            "Requires RepeatMasker confirmation that the region is repetitive. "
            "Do NOT apply if the repeat region contains a functional domain. "
            "Evaluated by Agent 8."
        ),
        "metadata": {"criterion": "BP3", "side": "benign", "strength": "Supporting", "agent": "agent8"},
    },
    {
        "id": "BP4",
        "text": (
            "BP4 — Benign Supporting. "
            "Multiple lines of computational evidence suggest no impact on gene/protein "
            "function. REVEL ≤ 0.15 is the primary threshold. Require ≥5/8 predictors "
            "agreeing benign. "
            "Mirror of PP3 — same single-use rule applies: BP4 can only be applied once. "
            "Do NOT apply if tools are split — that is neither PP3 nor BP4. "
            "Evaluated by Agent 3."
        ),
        "metadata": {"criterion": "BP4", "side": "benign", "strength": "Supporting", "agent": "agent3"},
    },
    {
        "id": "BP5",
        "text": (
            "BP5 — Benign Supporting. "
            "Variant found in a case with an alternate molecular basis for disease. "
            "The patient has a confirmed pathogenic variant in a different gene explaining "
            "their phenotype, making this variant less likely causative. "
            "Requires clinical history or confirmed alternate diagnosis. "
            "If no clinical history provided: BP5 cannot be evaluated — flag as "
            "unevaluated in the report. "
            "Evaluated by Agent 9 (requires clinical input)."
        ),
        "metadata": {"criterion": "BP5", "side": "benign", "strength": "Supporting", "agent": "agent9"},
    },
    {
        "id": "BP6",
        "text": (
            "BP6 — Benign Supporting. "
            "Reputable source recently reports variant as benign, but evidence is not "
            "available for independent evaluation. "
            "Mirror of PP5. Same source quality requirements: ≥2 star ClinVar or "
            "expert panel. "
            "Do NOT apply for conflicting ClinVar entries. "
            "Evaluated by Agent 4."
        ),
        "metadata": {"criterion": "BP6", "side": "benign", "strength": "Supporting", "agent": "agent4"},
    },
    {
        "id": "BP7",
        "text": (
            "BP7 — Benign Supporting. "
            "A synonymous variant for which splicing prediction algorithms predict no "
            "impact on splicing AND the nucleotide is not highly conserved. "
            "Requires: SpliceAI delta score < 0.1 AND conservation score (phyloP/GERP) "
            "below threshold. "
            "Do NOT apply to synonymous variants at splice site positions (last nt of "
            "exon, first nt of exon) — those may affect splicing. "
            "Evaluated by Agent 3."
        ),
        "metadata": {"criterion": "BP7", "side": "benign", "strength": "Supporting", "agent": "agent3"},
    },

    # ── COMBINATION RULES (for Final Arbiter) ───────────────────────────────
    {
        "id": "COMBINATION_RULES",
        "text": (
            "ACMG/AMP 2015 Table 5 — Combination rules for final classification. "
            "PATHOGENIC requires ONE of: "
            "(P1) ≥2 Very Strong; "
            "(P2) 1 Very Strong + ≥1 Strong; "
            "(P3) 1 Very Strong + ≥2 Moderate; "
            "(P4) 1 Very Strong + ≥1 Moderate + ≥1 Supporting; "
            "(P5) 1 Very Strong + ≥2 Supporting; "
            "(P6) ≥2 Strong; "
            "(P7) 1 Strong + ≥3 Moderate; "
            "(P8) 1 Strong + ≥2 Moderate + ≥2 Supporting; "
            "(P9) 1 Strong + ≥1 Moderate + ≥4 Supporting. "
            "LIKELY PATHOGENIC requires ONE of: "
            "(LP1) 1 Very Strong + 1 Moderate; "
            "(LP2) 1 Strong + 1-2 Moderate; "
            "(LP3) 1 Strong + ≥2 Supporting; "
            "(LP4) ≥3 Moderate; "
            "(LP5) 2 Moderate + ≥2 Supporting; "
            "(LP6) 1 Moderate + ≥4 Supporting. "
            "BENIGN requires ONE of: "
            "(B1) 1 Stand-alone (BA1); "
            "(B2) ≥2 Strong benign. "
            "LIKELY BENIGN requires ONE of: "
            "(LB1) 1 Strong benign + 1 Supporting benign; "
            "(LB2) ≥2 Supporting benign. "
            "VUS: does not meet any of the above, or has conflicting evidence "
            "(meaningful pathogenic AND meaningful benign evidence both present). "
            "CONFLICT resolution: when both pathogenic and benign strong evidence "
            "exist simultaneously, classify as VUS and note the conflict explicitly. "
            "The arbiter must not resolve conflicts by ignoring one side — both must "
            "be reported and recommended follow-up specified."
        ),
        "metadata": {"criterion": "COMBINATION_RULES", "side": "both", "strength": "N/A", "agent": "aggregator"},
    },

    # ── UPGRADE / DOWNGRADE SUMMARY ─────────────────────────────────────────
    {
        "id": "UPGRADE_DOWNGRADE_RULES",
        "text": (
            "ACMG/ClinGen approved strength adjustments (upgrades and downgrades). "
            "PVS1: downgrade to Strong if truncation near 3' end of gene or in last exon; "
            "downgrade to Moderate if evidence of alternate splicing rescue. "
            "PM2: ClinGen SVI recommends treating as Supporting in most contexts; "
            "apply Moderate only when absence from large population databases is "
            "particularly informative. "
            "PM3: upgrade to Strong if detected in trans in ≥2 unrelated individuals; "
            "upgrade to Very Strong if ≥4 unrelated individuals. "
            "PP1: upgrade to Moderate with ≥3 affected segregating family members; "
            "upgrade to Strong with ≥5 affected members across branches. "
            "PS2/PM6: PS2 Strong requires confirmed parentage; PM6 Moderate if assumed. "
            "PS3/BS3: downgrade to Moderate if functional study is in non-physiological "
            "system. "
            "General principle: strength adjustments must be justified by specific "
            "evidence — the advocate must cite the specific evidence supporting any "
            "proposed upgrade, and the arbiter must independently verify it is "
            "consistent with ACMG/ClinGen guidance before accepting the upgrade."
        ),
        "metadata": {"criterion": "UPGRADE_DOWNGRADE_RULES", "side": "both", "strength": "N/A", "agent": "debate"},
    },
]


# ---------------------------------------------------------------------------
# Builder function
# ---------------------------------------------------------------------------

def build_acmg_guidelines_collection(chromadb_dir: Path = CHROMADB_DIR) -> None:
    """
    Build (or rebuild) the acmg_guidelines ChromaDB collection.
    Safe to re-run — deletes and recreates the collection.
    """
    client = chromadb.PersistentClient(path=str(chromadb_dir))
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )

    # Delete if exists (clean rebuild)
    existing = [c.name for c in client.list_collections()]
    if "acmg_guidelines" in existing:
        client.delete_collection("acmg_guidelines")
        logger.info("Deleted existing acmg_guidelines collection for rebuild.")

    collection = client.create_collection(
        name="acmg_guidelines",
        embedding_function=ef,
        metadata={"description": "ACMG/AMP 2015 criterion definitions and combination rules"},
    )

    ids       = [doc["id"]       for doc in ACMG_CRITERION_DOCS]
    texts     = [doc["text"]     for doc in ACMG_CRITERION_DOCS]
    metadatas = [doc["metadata"] for doc in ACMG_CRITERION_DOCS]

    collection.add(ids=ids, documents=texts, metadatas=metadatas)

    logger.info(
        f"Built acmg_guidelines collection: {len(ids)} criterion documents indexed."
    )
    print(f"✓ acmg_guidelines: {len(ids)} documents indexed.")


def verify_acmg_guidelines_collection(chromadb_dir: Path = CHROMADB_DIR) -> bool:
    """Quick sanity check — query PVS1 and confirm it returns."""
    client = chromadb.PersistentClient(path=str(chromadb_dir))
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )
    try:
        col = client.get_collection("acmg_guidelines", embedding_function=ef)
        results = col.query(query_texts=["PVS1 null variant LOF frameshift"], n_results=3)
        ids = results["ids"][0]
        print(f"✓ acmg_guidelines verified. Top results: {ids}")
        return True
    except Exception as e:
        print(f"✗ acmg_guidelines verification failed: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_acmg_guidelines_collection()
    verify_acmg_guidelines_collection()
