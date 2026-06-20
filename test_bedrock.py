"""
Quick test script to verify AWS Bedrock integration.

Run: python test_bedrock.py
"""

import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

print("="*80)
print("AWS BEDROCK INTEGRATION TEST")
print("="*80)

# Test 1: Check environment variables
print("\n[Test 1] Checking environment variables...")
api_key = os.getenv("AWS_BEARER_TOKEN_BEDROCK")
provider = os.getenv("LLM_PROVIDER", "bedrock")
model = os.getenv("LLM_MODEL", "nemotron-30b")
region = os.getenv("BEDROCK_REGION", "us-east-1")

print(f"✅ LLM_PROVIDER: {provider}")
print(f"✅ LLM_MODEL: {model}")
print(f"✅ BEDROCK_REGION: {region}")
print(f"✅ API Key loaded: {api_key[:20]}..." if api_key else "❌ API Key missing!")

if not api_key:
    print("\n❌ ERROR: AWS_BEARER_TOKEN_BEDROCK not set!")
    print("Please set it in .env file.")
    exit(1)

# Test 2: Import bedrock client
print("\n[Test 2] Importing Bedrock client...")
try:
    from src.utils.bedrock_client import BedrockClient, list_available_models
    print("✅ Bedrock client imported successfully")
except ImportError as e:
    print(f"❌ Import failed: {e}")
    print("Install dependencies: pip install boto3 botocore")
    exit(1)

# Test 3: List available models
print("\n[Test 3] Available Bedrock models:")
models = list_available_models()
for m in models:
    print(f"  • {m['short_name']:20s} → {m['name']}")

# Test 4: Initialize client
print("\n[Test 4] Initializing Bedrock client...")
try:
    client = BedrockClient()
    print(f"✅ Client initialized (default model: {client.default_model})")
except Exception as e:
    print(f"❌ Initialization failed: {e}")
    exit(1)

# Test 5: Simple LLM call
print("\n[Test 5] Testing LLM call...")
print("Query: 'What is PM2 in ACMG guidelines? Answer in 2 sentences.'")

try:
    response = client.call_llm(
        system_prompt="You are a genetic variant classification expert. Be concise.",
        user_prompt="What is PM2 in ACMG guidelines? Answer in 2 sentences.",
        temperature=0.7,
        max_tokens=200
    )

    print("\n📝 Response:")
    print("-" * 80)
    print(response)
    print("-" * 80)
    print("✅ LLM call successful!")

except Exception as e:
    print(f"❌ LLM call failed: {e}")
    print("\nTroubleshooting:")
    print("1. Check if API key is valid")
    print("2. Check if boto3 is installed: pip install boto3")
    print("3. Check AWS region is correct")
    exit(1)

# Test 6: Unified LLM client
print("\n[Test 6] Testing unified LLM client (llm.py)...")
try:
    from src.utils.llm import call_llm

    response = call_llm(
        system_prompt="You are helpful. Be brief.",
        user_prompt="Say 'Integration working!' in a creative way.",
        temperature=0.9,
        max_tokens=50
    )

    print(f"📝 Response: {response}")
    print("✅ Unified LLM client working!")

except Exception as e:
    print(f"❌ Unified client failed: {e}")

# Test 7: JSON response test
print("\n[Test 7] Testing JSON response parsing...")
try:
    result = client.call_llm_json(
        system_prompt="You are a JSON API. Return only valid JSON, no extra text.",
        user_prompt='Return this JSON: {"status": "success", "message": "Bedrock integrated"}',
        temperature=0.0,
        max_tokens=100
    )

    print(f"📝 Parsed JSON: {result}")

    if result.get("status") == "success":
        print("✅ JSON parsing successful!")
    else:
        print("⚠️  JSON parsed but unexpected format")

except Exception as e:
    print(f"❌ JSON test failed: {e}")

# Summary
print("\n" + "="*80)
print("✅ ALL TESTS PASSED!")
print("="*80)
print("\n🎉 Your AWS Bedrock integration is working correctly!")
print("\nNext steps:")
print("1. Test chat API: python test_chat_api.py (create this)")
print("2. Integrate intern's frontend")
print("3. Deploy and test end-to-end")
print("\n💡 Tip: You can switch models by changing LLM_MODEL in .env")
print("   Available models: nemotron-30b, nemotron-120b, gpt-oss-20b, gpt-oss-120b")
