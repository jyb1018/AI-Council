@echo off
rem Windows wrapper: use Python already on PATH; never install an interpreter.
python "%~dp0lifecycle.py" --project "%~dp0.." %*
exit /b %errorlevel%
