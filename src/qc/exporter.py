import csv
import os
import json
from typing import Dict, Any, List
from datetime import datetime
from src.config import OUTPUT_DIR

EXPORT_DIR = os.path.join(OUTPUT_DIR, "qc_exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

def export_qc_results_csv(records: List[Dict[str, Any]], filepath: str = None) -> str:
    if not filepath:
        filepath = os.path.join(EXPORT_DIR, "qc_results.csv")
        
    columns = [
        "session_id", "patient_id", "analysis_mode", "qc_status", "qc_score",
        "confidence", "input_qc", "annotation_qc", "evidence_qc", "classification_qc",
        "report_qc", "issues", "created_at"
    ]
    
    file_exists = os.path.exists(filepath)
    
    with open(filepath, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if not file_exists:
            writer.writeheader()
            
        for record in records:
            row = {
                "session_id": record.get("session_id", ""),
                "patient_id": record.get("patient_id", ""),
                "analysis_mode": record.get("analysis_mode", ""),
                "qc_status": record.get("qc_status", ""),
                "qc_score": record.get("qc_score", 0.0),
                "confidence": record.get("confidence", 0.0),
                "input_qc": record.get("input_qc", ""),
                "annotation_qc": record.get("annotation_qc", ""),
                "evidence_qc": record.get("evidence_qc", ""),
                "classification_qc": record.get("classification_qc", ""),
                "report_qc": record.get("report_qc", ""),
                "issues": json.dumps(record.get("issues", [])),
                "created_at": record.get("created_at", datetime.now().isoformat())
            }
            writer.writerow(row)
            
    return filepath

def export_validation_report(records: List[Dict[str, Any]], filepath: str = None) -> str:
    if not filepath:
        filepath = os.path.join(EXPORT_DIR, "qc_validation_report.csv")
        
    columns = ["test_id", "classification_match", "criteria_match", "qc_status"]
    
    file_exists = os.path.exists(filepath)
    
    with open(filepath, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if not file_exists:
            writer.writeheader()
            
        for record in records:
            row = {
                "test_id": record.get("test_id", ""),
                "classification_match": record.get("classification_match", ""),
                "criteria_match": record.get("criteria_match", ""),
                "qc_status": record.get("qc_status", "")
            }
            writer.writerow(row)
            
    return filepath
