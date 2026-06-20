@echo off
echo ========================================
echo Setting Up Port Forwarding to DGX
echo ========================================
echo.
echo This will forward port 8000 from DGX to your local machine
echo Keep this window OPEN while using the application
echo.
echo Press Ctrl+C to stop port forwarding
echo.
echo ========================================
echo.

ssh -L 8000:localhost:8000 dgx-i-molsys@210.212.207.65 "kubectl port-forward molsys-pod-a 8000:8000"
