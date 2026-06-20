import os
from typing import Dict, Any

def evaluate_report(reports_data: Dict[str, Any]) -> Dict[str, Any]:
    issues = []
    
    required_formats = ["html", "excel", "tsv"]
    
    for fmt in required_formats:
        path = reports_data.get(f"{fmt}_report_path")
        if not path:
            issues.append(f"{fmt.capitalize()} report path not provided")
            continue
            
        if not os.path.exists(path):
            issues.append(f"{fmt.capitalize()} report missing at {path}")
            continue
            
        if os.path.getsize(path) == 0:
            issues.append(f"{fmt.capitalize()} report is empty")
            
    if issues:
        return {"report_qc": "FAIL", "issues": issues}
        
    return {"report_qc": "PASS", "issues": []}
