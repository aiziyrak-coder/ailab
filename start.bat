@echo off
chcp 65001 > nul
cd /d "%~dp0backend"

echo ============================================================
echo   MedLab AI — Django REST + frontend
echo ============================================================
echo.

if not exist ".env" (
  echo [!] backend\.env topilmadi. backend\.env.example ni .env ga nusxalang.
  echo.
)

python -c "import django" 2>nul
if errorlevel 1 (
    echo [*] Kutubxonalar o'rnatilmoqda...
    pip install -r requirements.txt
    echo.
)

set PORT=%PORT%
if "%PORT%"=="" set PORT=8000

if not exist "staticfiles\" mkdir staticfiles 2>nul

echo [*] Server: http://127.0.0.1:%PORT%
echo [*] To'xtatish: Ctrl+C
echo.
python manage.py runserver 0.0.0.0:%PORT%

pause
