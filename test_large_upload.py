#!/usr/bin/env python3
"""
Test script to verify large file upload support.
Creates a dummy VCF file and attempts to upload it.
"""

import os
import tempfile
import requests

# Configuration
API_URL = os.getenv("API_URL", "http://localhost:8080")
API_KEY = os.getenv("API_KEY", "your-api-key-here")

def create_dummy_vcf(size_mb: int = 16) -> str:
    """Create a dummy VCF file of specified size."""
    vcf_header = """##fileformat=VCFv4.2
##FILTER=<ID=PASS,Description="All filters passed">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##contig=<ID=chr1,length=248956422>
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE
"""

    # Create temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.vcf', delete=False) as f:
        f.write(vcf_header)

        # Calculate how many variants needed for target size
        variant_line = "chr1\t12345\t.\tA\tT\t100\tPASS\t.\tGT\t0/1\n"
        variant_size = len(variant_line.encode('utf-8'))
        header_size = len(vcf_header.encode('utf-8'))
        target_bytes = size_mb * 1024 * 1024
        num_variants = (target_bytes - header_size) // variant_size

        print(f"Creating VCF with ~{num_variants} variants ({size_mb} MB)...")

        for i in range(num_variants):
            pos = 10000 + i * 100
            f.write(f"chr1\t{pos}\t.\tA\tT\t100\tPASS\t.\tGT\t0/1\n")

        temp_path = f.name

    actual_size = os.path.getsize(temp_path) / (1024 * 1024)
    print(f"Created dummy VCF: {temp_path}")
    print(f"Actual size: {actual_size:.2f} MB")

    return temp_path

def test_upload(vcf_path: str, chat_id: str = "test_chat_123"):
    """Test uploading the VCF file."""
    print(f"\nTesting upload to {API_URL}/upload...")

    headers = {
        'X-API-Key': API_KEY
    }

    with open(vcf_path, 'rb') as f:
        files = {'file': ('test_large.vcf', f, 'text/plain')}
        data = {'chat_id': chat_id}

        try:
            print("Uploading... (this may take a while for large files)")
            response = requests.post(
                f"{API_URL}/upload",
                headers=headers,
                files=files,
                data=data,
                timeout=600  # 10 minutes timeout
            )

            print(f"\nResponse Status: {response.status_code}")
            print(f"Response Body: {response.text}")

            if response.status_code == 200:
                print("✅ Upload successful!")
                return True
            else:
                print("❌ Upload failed!")
                return False

        except requests.exceptions.Timeout:
            print("❌ Upload timed out!")
            return False
        except Exception as e:
            print(f"❌ Upload error: {e}")
            return False

def main():
    # Check if API key is set
    if API_KEY == "your-api-key-here":
        print("❌ Please set API_KEY environment variable")
        print("   export API_KEY=your-actual-api-key")
        return

    # Create dummy VCF
    vcf_path = create_dummy_vcf(size_mb=16)

    try:
        # Test upload
        success = test_upload(vcf_path)

        if success:
            print("\n✅ All tests passed!")
        else:
            print("\n❌ Tests failed!")

    finally:
        # Cleanup
        if os.path.exists(vcf_path):
            os.unlink(vcf_path)
            print(f"\nCleaned up: {vcf_path}")

if __name__ == "__main__":
    main()
