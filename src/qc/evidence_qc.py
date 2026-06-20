from typing import Dict, Any, List

def evaluate_evidence(variants_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    issues = []
    fail = False
    
    for variant in variants_data:
        variant_id = variant.get("id", "Unknown Variant")
        classification = variant.get("classification")
        criteria = variant.get("criteria", [])
        
        # Check criteria assigned
        if classification and classification != "VUS" and not criteria:
            issues.append(f"{variant_id}: Classification={classification} but Criteria=[]")
            fail = True
            
        # Check supporting evidence available
        if not variant.get("supporting_evidence"):
            issues.append(f"{variant_id}: Missing supporting evidence")
            fail = True
            
        # Check required ACMG evidence fields exist
        if "acmg_fields" not in variant or not variant.get("acmg_fields"):
            issues.append(f"{variant_id}: Required ACMG evidence fields missing")
            
    if fail:
        return {"evidence_qc": "FAIL", "issues": issues}
    if issues:
        return {"evidence_qc": "WARNING", "issues": issues}
        
    return {"evidence_qc": "PASS", "issues": []}
