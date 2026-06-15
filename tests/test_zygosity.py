"""
Quick test for zygosity extraction
"""
import sys
sys.path.insert(0, "src")

from pipeline.nodes.post_process import _extract_zygosity_from_vcf

# Test with check_13 VCF
vcf_path = "data/input/check_13.vcf"

# Test variant 1: 7:117548628:A:G (CFTR)
zyg1 = _extract_zygosity_from_vcf(
    vcf_path, "7", 117548628, "A", "G", "Unknown"
)
print(f"Variant 1 (7:117548628:A:G): {zyg1}")

# Test variant 2: 13:32338080:A:C (BRCA2)
zyg2 = _extract_zygosity_from_vcf(
    vcf_path, "13", 32338080, "A", "C", "Unknown"
)
print(f"Variant 2 (13:32338080:A:C): {zyg2}")

# Test variant 3: 13:32355250:ACC:A (BRCA2)
zyg3 = _extract_zygosity_from_vcf(
    vcf_path, "13", 32355250, "ACC", "A", "Unknown"
)
print(f"Variant 3 (13:32355250:ACC:A): {zyg3}")

print("\nZygosity extraction test complete!")

