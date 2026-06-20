@echo off
echo ========================================
echo Uploading Updated Code to DGX Server
echo ========================================
echo.
echo This will upload your Windows code to DGX
echo Make sure the backend on DGX is STOPPED first!
echo.
pause
echo.
echo Uploading files via SCP...
echo.

scp -r "c:\Users\hp\OneDrive\Desktop\Molsys Internship\ai-native-acmg-pipeline\src" dgx-i-molsys@210.212.207.65:/tmp/acmg-pipeline-updated/
scp -r "c:\Users\hp\OneDrive\Desktop\Molsys Internship\ai-native-acmg-pipeline\frontend" dgx-i-molsys@210.212.207.65:/tmp/acmg-pipeline-updated/
scp "c:\Users\hp\OneDrive\Desktop\Molsys Internship\ai-native-acmg-pipeline\requirements.txt" dgx-i-molsys@210.212.207.65:/tmp/acmg-pipeline-updated/
scp "c:\Users\hp\OneDrive\Desktop\Molsys Internship\ai-native-acmg-pipeline\.env.example" dgx-i-molsys@210.212.207.65:/tmp/acmg-pipeline-updated/

echo.
echo ========================================
echo Upload Complete!
echo ========================================
echo.
echo Next steps on DGX:
echo 1. SSH to DGX: connect_dgx.bat
echo 2. Run: kubectl exec -it molsys-pod-a -- /bin/bash
echo 3. Run: cd /workspace/data/acmg-pipeline
echo 4. Run: cp -r /tmp/acmg-pipeline-updated/* .
echo 5. Run: python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
echo.
pause
