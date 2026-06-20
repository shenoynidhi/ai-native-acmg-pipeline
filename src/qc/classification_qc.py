from typing import Dict, Any, List

VALID_CLASSIFICATIONS = {
    "Pathogenic",
    "Likely_Pathogenic",
    "VUS",
    "Likely_Benign",
    "Benign"
}

def evaluate_classification(variants_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    issues = []
    fail = False
    warning = False
    
    total_confidence = 0.0
    count = 0
    
    for variant in variants_data:
        variant_id = variant.get("id", "Unknown Variant")
        classification = variant.get("classification")
        
        if not classification:
            issues.append(f"{variant_id}: Classification missing")
            fail = True
            continue
            
        if classification not in VALID_CLASSIFICATIONS:
            issues.append(f"{variant_id}: Invalid classification '{classification}'")
            fail = True
            
        matched_criteria = variant.get("matched_criteria", 0)
        total_considered = variant.get("total_considered_criteria", 1)
        evidence_count = variant.get("evidence_count", 0)
        
        if evidence_count < 1 and classification != "VUS":
            issues.append(f"{variant_id}: Evidence count below minimum threshold")
            fail = True
            
        # Confidence score calculation
        confidence = matched_criteria / total_considered if total_considered > 0 else 0.0
        total_confidence += confidence
        count += 1
        
        if confidence < 0.70:
            issues.append(f"{variant_id}: Low confidence ({confidence:.2f})")
            fail = True
        elif confidence < 0.90:
            issues.append(f"{variant_id}: Marginal confidence ({confidence:.2f})")
            warning = True

    avg_confidence = total_confidence / count if count > 0 else 0.0
    
    if fail:
        return {"classification_qc": "FAIL", "issues": issues, "confidence": avg_confidence}
    if warning:
        return {"classification_qc": "WARNING", "issues": issues, "confidence": avg_confidence}
        
    return {"classification_qc": "PASS", "issues": [], "confidence": avg_confidence}
