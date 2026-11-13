@echo off
rem Colour-mode triggerbot. Extra flags are passed through, e.g.
rem   color.bat --window "Roblox" --debug
py -3.12 "%~dp0triggerbot.py" --mode color %*
pause
