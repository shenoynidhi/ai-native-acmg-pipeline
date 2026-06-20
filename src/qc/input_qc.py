import os
from typing import Dict, Any

def evaluate_input(session_data: Dict[str, Any]) -> Dict[str, Any]:
    issues = []
    critical = False
    warning = False

    # Check VCF
    vcf_path = session_data.get("vcf_path")
    if not vcf_path or not os.path.exists(vcf_path) or os.path.getsize(vcf_path) == 0:
        issues.append("VCF file missing or empty")
        critical = True

    # Check basic metadata
    if not session_data.get("genome_build"):
        issues.append("Genome build missing")
        critical = True
    
    if not session_data.get("patient_sex"):
        issues.append("Patient sex missing")
        critical = True

    # Check optional metadata
    if not session_data.get("clinical_notes"):
        issues.append("Clinical notes missing")
        warning = True
        
    if not session_data.get("hpo_terms"):
        issues.append("HPO terms missing")
        warning = True

    # Check Trio mode
    if session_data.get("analysis_mode") == "trio":
        proband_vcf = session_data.get("proband_vcf")
        mother_vcf = session_data.get("mother_vcf")
        father_vcf = session_data.get("father_vcf")
        
        if not proband_vcf:
            issues.append("Proband VCF missing")
            critical = True
        if not mother_vcf:
            issues.append("Mother VCF missing")
            critical = True
        if not father_vcf:
            issues.append("Father VCF missing")
            critical = True

        # Check BAMs if enabled
        if session_data.get("use_bam"):
            if not session_data.get("proband_bam"):
                issues.append("Proband BAM missing")
                critical = True
            if not session_data.get("mother_bam"):
                issues.append("Mother BAM missing")
                critical = True
            if not session_data.get("father_bam"):
                issues.append("Father BAM missing")
                critical = True

    if critical:
        return {"input_qc": "FAIL", "issues": issues}
    if warning:
        return {"input_qc": "WARNING", "issues": issues}
    
    return {"input_qc": "PASS", "issues": issues}
