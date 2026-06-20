@echo off
echo ========================================
echo Connecting to DGX Server
echo ========================================
echo.
echo Step 1: SSH to DGX headnode
echo Step 2: Enter password when prompted
echo Step 3: Run: kubectl exec -it molsys-pod-a -- /bin/bash
echo Step 4: Run: cd /workspace/data/acmg-pipeline
echo.
ssh dgx-i-molsys@210.212.207.65
