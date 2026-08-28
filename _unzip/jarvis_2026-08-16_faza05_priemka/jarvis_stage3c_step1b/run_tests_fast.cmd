@echo off
rem run_tests_fast.cmd -- same run, plus the 15 slowest tests printed at the end.
rem
rem There is no fast/slow split yet, on purpose. Splitting the suite before
rem measuring it hides the real cost: we would guess which tests are slow and
rem guess wrong. First look at --durations, then cut with numbers in hand.
rem Latin letters only, same reason as in run_tests.cmd.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python -m pytest -q --durations=15 %*
set RC=%ERRORLEVEL%
endlocal & exit /b %RC%
