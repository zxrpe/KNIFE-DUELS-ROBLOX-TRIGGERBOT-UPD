@echo off
rem Launch the triggerbot. Pass the same flags as triggerbot.py, e.g.
rem   run.bat --window "Roblox" --debug
py -3.12 "%~dp0triggerbot.py" %*
pause
