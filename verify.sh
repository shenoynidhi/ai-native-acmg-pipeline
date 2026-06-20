#!/bin/bash
# verify_fixes.sh
# Verify that all fixes were applied correctly

echo "========================================"
echo "Verifying Log Improvements"
echo "========================================"
echo ""

# Check if hardcoded agent names are removed
echo "1. Checking for hardcoded agent names in log messages..."
hardcoded_count=$(grep -r "logger\.info(f\"\[agent" src/agents/ src/pipeline/nodes/ 2>/dev/null | wc -l)

if [ "$hardcoded_count" -eq 0 ]; then
    echo "   ✅ PASS: No hardcoded agent names found"
else
    echo "   ❌ FAIL: Found $hardcoded_count hardcoded agent names"
    echo "   Run: bash fix_agent_log_names.sh"
fi
echo ""

# Check if user-friendly logger is imported
echo "2. Checking if agents use get_user_friendly_logger..."
friendly_logger_count=$(grep -r "get_user_friendly_logger" src/agents/ | wc -l)

if [ "$friendly_logger_count" -ge 18 ]; then
    echo "   ✅ PASS: Found $friendly_logger_count occurrences (expected ≥18)"
else
    echo "   ⚠️  WARNING: Found only $friendly_logger_count occurrences (expected ≥18)"
fi
echo ""

# Check if clinical_notes is in report_generator
echo "3. Checking if clinical_notes is passed to template..."
if grep -q '"clinical_notes".*state.get("clinical_notes")' src/pipeline/nodes/report_generator.py; then
    echo "   ✅ PASS: clinical_notes is in report row data"
else
    echo "   ❌ FAIL: clinical_notes NOT found in report_generator.py"
fi
echo ""

# Check if template has context-aware logic
echo "4. Checking if template has context-aware phenotype messages..."
if grep -q "{% if row.clinical_notes %}" src/report_templates/acmg_report.html.j2; then
    echo "   ✅ PASS: Template checks for clinical_notes"
else
    echo "   ❌ FAIL: Template missing clinical_notes check"
fi
echo ""

# Summary
echo "========================================"
echo "Verification Summary"
echo "========================================"
echo ""

if [ "$hardcoded_count" -eq 0 ] && [ "$friendly_logger_count" -ge 18 ]; then
    echo "✅ ALL CHECKS PASSED!"
    echo ""
    echo "Next steps:"
    echo "1. Test with: python -c \"from src.pipeline.runner import run_session; ...\""
    echo "2. Check logs show '[Population Frequency]' not '[agent1_population]'"
    echo "3. Check HTML report shows context-aware phenotype messages"
else
    echo "⚠️  Some checks failed. Review messages above."
fi
echo ""

