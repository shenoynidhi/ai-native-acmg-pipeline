from typing import Dict, Any, List, Tuple

def evaluate_trio_checks(variants_data: List[Dict[str, Any]], session_data: Dict[str, Any]) -> Tuple[str, List[str]]:
    issues = []
    status = "PASS"
    
    is_trio = session_data.get("analysis_mode") == "trio"
    has_bams = session_data.get("use_bam", False)
    
    for variant in variants_data:
        variant_id = variant.get("id", "Unknown Variant")
        criteria = variant.get("criteria", [])
        
        if "PS2" in criteria:
            if not variant.get("absent_in_parents"):
                issues.append(f"{variant_id}: PS2 applied but variant not confirmed absent in both parents")
                status = "FAIL"
                
        if "PM3" in criteria:
            if not variant.get("two_variants_found") or not variant.get("inheritance_validated"):
                issues.append(f"{variant_id}: PM3 applied but requirements (two variants, inheritance validated) not met")
                if status != "FAIL": status = "WARNING"
                
            if not variant.get("phasing_evidence_exists"):
                if has_bams:
                    if not variant.get("phasing_completed"):
                        issues.append(f"{variant_id}: PM3 applied and BAMs supplied, but phasing not completed")
                        if status != "FAIL": status = "WARNING"
                else:
                    issues.append(f"{variant_id}: PM3 applied without BAM phasing evidence")
                    if status != "FAIL": status = "WARNING"
                    
        if "PP1" in criteria:
            if not variant.get("segregation_evidence_exists"):
                issues.append(f"{variant_id}: PP1 applied but segregation evidence missing")
                if status != "FAIL": status = "WARNING"
                
    return status, issues

def calculate_score(qc_results: Dict[str, Any]) -> Tuple[float, str]:
    # Convert PASS/WARNING/FAIL to numeric scores for each category
    def get_component_score(status: str) -> float:
        if status == "PASS": return 100.0
        if status == "WARNING": return 75.0
        return 0.0

    input_score = get_component_score(qc_results.get("input_qc", "FAIL"))
    annotation_score = get_component_score(qc_results.get("annotation_qc", "FAIL"))
    evidence_score = get_component_score(qc_results.get("evidence_qc", "FAIL"))
    classification_score = get_component_score(qc_results.get("classification_qc", "FAIL"))
    report_score = get_component_score(qc_results.get("report_qc", "FAIL"))
    
    # Weights
    score = (
        input_score * 0.20 +
        annotation_score * 0.20 +
        evidence_score * 0.25 +
        classification_score * 0.20 +
        report_score * 0.15
    )
    
    # Overall Status based on thresholds
    if score >= 90:
        overall_status = "PASS"
    elif score >= 70:
        overall_status = "WARNING"
    else:
        overall_status = "FAIL"
        
    # Any critical sub-failure might force an overall FAIL or WARNING
    if any(qc_results.get(k) == "FAIL" for k in ["input_qc", "annotation_qc", "evidence_qc", "classification_qc", "report_qc"]):
        overall_status = "FAIL"
    elif overall_status == "PASS" and any(qc_results.get(k) == "WARNING" for k in ["input_qc", "annotation_qc", "evidence_qc", "classification_qc", "report_qc"]):
        overall_status = "WARNING"

    return score, overall_status
