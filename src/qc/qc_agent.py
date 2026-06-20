from typing import Dict, Any, List
from .input_qc import evaluate_input
from .annotation_qc import evaluate_annotation
from .evidence_qc import evaluate_evidence
from .classification_qc import evaluate_classification
from .report_qc import evaluate_report
from .scoring import evaluate_trio_checks, calculate_score
from .qc_store import QCStore
from .exporter import export_qc_results_csv

class QCAgent:
    def __init__(self):
        self.store = QCStore()

    def run_qc(self, session_data: Dict[str, Any], variants_data: List[Dict[str, Any]], annotation_data: Dict[str, Any], reports_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the full QC pipeline. Does not modify ACMG classifications.
        """
        all_issues = []
        
        # 1. Input QC
        input_res = evaluate_input(session_data)
        if input_res.get("issues"):
            all_issues.extend([f"Input: {i}" for i in input_res["issues"]])
            
        # 2. Annotation QC
        annot_res = evaluate_annotation(annotation_data)
        if annot_res.get("issues"):
            all_issues.extend([f"Annotation: {i}" for i in annot_res["issues"]])
            
        # 3. Evidence QC
        evidence_res = evaluate_evidence(variants_data)
        if evidence_res.get("issues"):
            all_issues.extend([f"Evidence: {i}" for i in evidence_res["issues"]])
            
        # 4. Classification QC
        class_res = evaluate_classification(variants_data)
        if class_res.get("issues"):
            all_issues.extend([f"Classification: {i}" for i in class_res["issues"]])
            
        # 5. Report QC
        report_res = evaluate_report(reports_data)
        if report_res.get("issues"):
            all_issues.extend([f"Report: {i}" for i in report_res["issues"]])
            
        # Compile results
        qc_results = {
            "input_qc": input_res["input_qc"],
            "annotation_qc": annot_res["annotation_qc"],
            "evidence_qc": evidence_res["evidence_qc"],
            "classification_qc": class_res["classification_qc"],
            "report_qc": report_res["report_qc"],
            "confidence": class_res.get("confidence", 0.0)
        }
        
        # Calculate Score
        score, status = calculate_score(qc_results)
        
        # Trio checks
        if session_data.get("analysis_mode") == "trio":
            trio_status, trio_issues = evaluate_trio_checks(variants_data, session_data)
            if trio_issues:
                all_issues.extend([f"Trio: {i}" for i in trio_issues])
            if trio_status == "FAIL":
                status = "FAIL"
            elif trio_status == "WARNING" and status == "PASS":
                status = "WARNING"
                
        # Final Record
        record = {
            "session_id": session_data.get("session_id", "unknown_session"),
            "patient_id": session_data.get("patient_id", "unknown_patient"),
            "analysis_mode": session_data.get("analysis_mode", "solo"),
            "qc_status": status,
            "qc_score": round(score, 2),
            "confidence": round(class_res.get("confidence", 0.0), 2),
            "input_qc": qc_results["input_qc"],
            "annotation_qc": qc_results["annotation_qc"],
            "evidence_qc": qc_results["evidence_qc"],
            "classification_qc": qc_results["classification_qc"],
            "report_qc": qc_results["report_qc"],
            "issues": all_issues
        }
        
        # Save to DB
        self.store.save_qc_result(record)
        
        # Export to CSV automatically
        export_qc_results_csv([record])
        
        return record
