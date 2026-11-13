@echo off
rem Eager/spray mode: also fires at distant or uncertain targets.
rem Extra flags are passed through, e.g.  eager.bat --window "Roblox" --debug
py -3.12 "%~dp0triggerbot.py" --mode color --eager %*
pause
