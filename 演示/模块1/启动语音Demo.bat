@echo off
chcp 65001 >nul
title 智能厨房 AI 助手 - 语音交互 Demo
echo.
echo  使用真实语音环境 (F:\python3.12) 启动...
echo  注意: 不要用 base 环境的 python, 那里没有 fastapi, 会变成模拟模式
echo.
F:\python3.12\python.exe -u "%~dp0demo_cooking.py"
pause
