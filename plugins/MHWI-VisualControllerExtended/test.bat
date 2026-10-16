@echo off
setlocal
cd /d "%~dp0"
call build.bat --no-pause
if errorlevel 1 exit /b 1
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -prerelease -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do call "%%i\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
if not exist "build\safetyhook\tests" mkdir "build\safetyhook\tests"
cl /nologo /std:c++latest /utf-8 /O2 /MT /W4 /EHsc /DVCE_TEST ^
 /DZYDIS_STATIC_BUILD /DZYCORE_STATIC_BUILD /Ithird_party\safetyhook ^
 MHWI-VisualControllerExtended.cpp build\safetyhook\safetyhook.obj build\safetyhook\Zydis.obj ^
 /Fo:build\safetyhook\tests\rules.obj /Fe:build\safetyhook\tests\rules.exe /link psapi.lib user32.lib
if errorlevel 1 exit /b 1
cl /nologo /std:c++latest /utf-8 /O2 /MT /W4 /EHsc ^
 /DZYDIS_STATIC_BUILD /DZYCORE_STATIC_BUILD /Ithird_party\safetyhook ^
 tests\safetyhook_smoke.cpp build\safetyhook\safetyhook.obj build\safetyhook\Zydis.obj ^
 /Fo:build\safetyhook\tests\smoke.obj /Fe:build\safetyhook\tests\smoke.exe /link psapi.lib user32.lib
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /utf-8 /O2 /MT /W4 /WX /EHsc ^
 tests\charge_burst.cpp /Fo:build\safetyhook\tests\charge-burst.obj ^
 /Fe:build\safetyhook\tests\charge-burst.exe
if errorlevel 1 exit /b 1
cl /nologo /std:c++latest /utf-8 /O2 /MT /W4 /EHsc ^
 /DZYDIS_STATIC_BUILD /DZYCORE_STATIC_BUILD /Ithird_party\safetyhook ^
 tests\charge_probe.cpp build\safetyhook\safetyhook.obj build\safetyhook\Zydis.obj ^
 /Fo:build\safetyhook\tests\charge-probe.obj /Fe:build\safetyhook\tests\charge-probe.exe /link psapi.lib user32.lib
if errorlevel 1 exit /b 1
cl /nologo /std:c++latest /utf-8 /O2 /MT /W4 /EHsc ^
 /DZYDIS_STATIC_BUILD /DZYCORE_STATIC_BUILD /Ithird_party\safetyhook ^
 tests\charge_integration.cpp build\safetyhook\safetyhook.obj build\safetyhook\Zydis.obj ^
 /Fo:build\safetyhook\tests\charge-integration.obj /Fe:build\safetyhook\tests\charge-integration.exe /link psapi.lib user32.lib
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /utf-8 /O2 /MT /W4 /WX /EHsc ^
 tests\random_damage.cpp /Fo:build\safetyhook\tests\random-damage.obj ^
 /Fe:build\safetyhook\tests\random-damage.exe
if errorlevel 1 exit /b 1
cl /nologo /std:c++latest /utf-8 /O2 /MT /W4 /EHsc ^
 /DZYDIS_STATIC_BUILD /DZYCORE_STATIC_BUILD /Ithird_party\safetyhook ^
 tests\damage_integration.cpp build\safetyhook\safetyhook.obj build\safetyhook\Zydis.obj ^
 /Fo:build\safetyhook\tests\damage-integration.obj /Fe:build\safetyhook\tests\damage-integration.exe /link psapi.lib user32.lib
if errorlevel 1 exit /b 1
copy /y test_rules.ini build\safetyhook\tests\test_rules.ini >nul
if errorlevel 1 exit /b 1
pushd build\safetyhook\tests
rules.exe >rules-results.txt 2>&1
set "RULES_RESULT=%ERRORLEVEL%"
smoke.exe >smoke-results.txt 2>&1
set "SMOKE_RESULT=%ERRORLEVEL%"
charge-burst.exe >charge-burst-results.txt 2>&1
set "BURST_RESULT=%ERRORLEVEL%"
charge-probe.exe >charge-probe-results.txt 2>&1
set "PROBE_RESULT=%ERRORLEVEL%"
charge-integration.exe >charge-integration-results.txt 2>&1
set "CHARGE_INTEGRATION_RESULT=%ERRORLEVEL%"
random-damage.exe >random-damage-results.txt 2>&1
set "RANDOM_RESULT=%ERRORLEVEL%"
damage-integration.exe >damage-integration-results.txt 2>&1
set "DAMAGE_INTEGRATION_RESULT=%ERRORLEVEL%"
type rules-results.txt
type smoke-results.txt
type charge-burst-results.txt
type charge-probe-results.txt
type charge-integration-results.txt
type random-damage-results.txt
type damage-integration-results.txt
popd
if not "%RULES_RESULT%"=="0" exit /b 1
if not "%SMOKE_RESULT%"=="0" exit /b 1
if not "%BURST_RESULT%"=="0" exit /b 1
if not "%PROBE_RESULT%"=="0" exit /b 1
if not "%CHARGE_INTEGRATION_RESULT%"=="0" exit /b 1
if not "%RANDOM_RESULT%"=="0" exit /b 1
if not "%DAMAGE_INTEGRATION_RESULT%"=="0" exit /b 1
exit /b 0
