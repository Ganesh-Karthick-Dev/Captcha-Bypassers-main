@echo off
title SERP Rank Tracker Microservice
echo Starting SERP Rank Tracker Microservice on port 5001...
cd /d "C:\Users\admin\Downloads\Captcha-Bypassers-main\Captcha-Bypassers-main"
:loop
python server.py
echo [WARNING] Server crashed or stopped unexpectedly! Restarting in 3 seconds...
timeout /t 3 /nobreak >nul
goto loop
