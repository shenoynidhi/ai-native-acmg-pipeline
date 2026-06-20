from typing import Dict, Any

def evaluate_annotation(annotation_data: Dict[str, Any]) -> Dict[str, Any]:
    issues = []
    
    if not annotation_data.get("vep_completed"):
        issues.append("VEP completed flag is missing or false")
        
    if annotation_data.get("annotated_variant_count", 0) <= 0:
        issues.append("Annotated variant count is 0")
        
    if not annotation_data.get("gene_symbols_available"):
        issues.append("Gene symbols missing")
        
    if not annotation_data.get("population_frequency_available"):
        issues.append("Population frequency missing")
        
    if not annotation_data.get("consequence_available"):
        issues.append("Consequence annotation missing")
        
    if issues:
        # In this implementation, any missing annotation issue triggers a WARNING or FAIL.
        # Based on instructions: If any required annotation missing: WARNING or FAIL
        return {"annotation_qc": "WARNING", "issues": issues}
        
    return {"annotation_qc": "PASS", "issues": []}
