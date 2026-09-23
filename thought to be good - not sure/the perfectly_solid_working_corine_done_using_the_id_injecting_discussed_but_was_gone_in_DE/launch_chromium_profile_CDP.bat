@echo off
echo ========================================
echo  Launch Chrome + CDP interceptor
echo  (existing profile -- DO NOT WIPE)
echo ========================================

set CDP_PORT=9222
set PROFILE=C:\Users\MTHG\Desktop\Claude-code-sessions\Working-Corinefresh\chromium-profile

set BROWSER=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%BROWSER%" set BROWSER=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
if not exist "%BROWSER%" set BROWSER=C:\Users\MTHG\AppData\Local\Google\Chrome\Application\chrome.exe
if not exist "%BROWSER%" (
    echo [ERROR] Chrome not found.
    pause & exit /b 1
)
echo [OK] Chrome: %BROWSER%

start "" "%BROWSER%" ^
    --remote-debugging-port=%CDP_PORT% ^
    --disable-blink-features=AutomationControlled ^
    --user-data-dir="%PROFILE%" ^
    --no-first-run ^
    --no-default-browser-check ^
    --disable-extensions ^
    --disable-features=WebAuthentication,CredentialManagement ^
    https://badoo.com

echo.
echo Chrome launched. Now run:
echo   py -3 Working-Corinefresh\badoo_cdp_interceptor.py
echo.
pause
