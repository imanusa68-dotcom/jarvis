@echo off
rem run_tests.cmd -- full test run with the redirected-output channel fixed.
rem
rem Latin letters only. A .cmd file is read by cmd.exe in the OEM code page
rem (866 on this machine), so Cyrillic text inside would arrive as garbage.
rem
rem PYTHONUTF8 repairs the "python ... > file.txt" channel, which is cp1251 here
rem and cannot even hold our warning signs. The live console is already utf-8,
rem so chcp is deliberately NOT called: it would change the owner's console.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python -m pytest -q %*
set RC=%ERRORLEVEL%
endlocal & exit /b %RC%
