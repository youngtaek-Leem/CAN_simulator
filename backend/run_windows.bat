@echo off
rem CAN Simulator backend launcher (Windows)
rem First run: creates the venv and installs dependencies automatically.
cd /d %~dp0

if not exist .venv (
    echo [1/3] Python venv 생성 중...
    python -m venv .venv
    if errorlevel 1 goto :error
)

echo [2/3] 의존성 설치 확인 중...
.venv\Scripts\python -m pip install -q -r requirements.txt
if errorlevel 1 goto :error

echo [3/3] 서버 시작 - 브라우저에서 http://127.0.0.1:8000 접속
.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000
goto :eof

:error
echo.
echo 실패했습니다. Python 3.11 이상이 설치되어 있고 PATH에 등록되어 있는지 확인하세요.
echo 설치: https://www.python.org/downloads/  (설치 시 "Add python.exe to PATH" 체크)
pause
