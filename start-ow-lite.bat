@echo off
setlocal
set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "D:\OW-Bridge\ow-lite"
echo Starting ow-lite proxy ...
"%PY%" ow-lite.py start
echo.
echo If you see the ready banner above, the proxy is now running in background.
echo You can close this window. To stop it later: python ow-lite.py stop
echo.
pause
