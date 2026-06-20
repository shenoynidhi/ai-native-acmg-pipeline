#!/bin/bash
# fix_agent_log_names_v2.sh
# More robust version - removes ALL hardcoded agent names from log messages

echo "Fixing hardcoded agent names in log messages..."
echo ""

# Fix all agent files at once
for file in src/agents/agent*.py; do
    if [ -f "$file" ]; then
        echo "Processing $file..."

        # Remove [agent1_population] style names for all log levels
        sed -i 's/logger\.info(f"\[agent[0-9]_[a-z_]*\] /logger.info(f"/' "$file"
        sed -i 's/logger\.warning(f"\[agent[0-9]_[a-z_]*\] /logger.warning(f"/' "$file"
        sed -i 's/logger\.error(f"\[agent[0-9]_[a-z_]*\] /logger.error(f"/' "$file"
        sed -i 's/logger\.debug(f"\[agent[0-9]_[a-z_]*\] /logger.debug(f"/' "$file"

        # Remove [agent1] style short names for all log levels
        sed -i 's/logger\.info(f"\[agent[0-9]\] /logger.info(f"/' "$file"
        sed -i 's/logger\.warning(f"\[agent[0-9]\] /logger.warning(f"/' "$file"
        sed -i 's/logger\.error(f"\[agent[0-9]\] /logger.error(f"/' "$file"
        sed -i 's/logger\.debug(f"\[agent[0-9]\] /logger.debug(f"/' "$file"
    fi
done

# Fix debate nodes
echo "Processing debate nodes..."
for file in src/pipeline/nodes/debate_*.py; do
    if [ -f "$file" ]; then
        sed -i 's/logger\.info(f"\[pathogenic_advocate\] /logger.info(f"/' "$file"
        sed -i 's/logger\.warning(f"\[pathogenic_advocate\] /logger.warning(f"/' "$file"
        sed -i 's/logger\.info(f"\[benign_advocate\] /logger.info(f"/' "$file"
        sed -i 's/logger\.warning(f"\[benign_advocate\] /logger.warning(f"/' "$file"
        sed -i 's/logger\.info(f"\[final_arbiter\] /logger.info(f"/' "$file"
        sed -i 's/logger\.warning(f"\[final_arbiter\] /logger.warning(f"/' "$file"
    fi
done

# Fix other pipeline nodes
echo "Processing other pipeline nodes..."
for file in src/pipeline/nodes/evidence_aggregator.py src/pipeline/nodes/hpo_matcher.py; do
    if [ -f "$file" ]; then
        sed -i 's/logger\.info(f"\[evidence_aggregator\] /logger.info(f"/' "$file"
        sed -i 's/logger\.warning(f"\[evidence_aggregator\] /logger.warning(f"/' "$file"
        sed -i 's/logger\.info(f"\[hpo_matcher\] /logger.info(f"/' "$file"
        sed -i 's/logger\.warning(f"\[hpo_matcher\] /logger.warning(f"/' "$file"
    fi
done

echo ""
echo "Done! Hardcoded agent names removed."
echo ""
echo "Verify with: bash verify_fixes.sh"

