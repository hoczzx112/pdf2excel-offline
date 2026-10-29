@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ============================================
echo   PDF -^> Excel  (chay tai may nay)
echo   Mo trinh duyet: http://127.0.0.1:8765
echo   Nhan Ctrl+C de dung
echo ============================================
echo.

start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8765/"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765

echo.
echo Server da dung.
pause
