@echo off
chcp 65001 >nul
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
rem --timeout-graceful-shutdown: uvicorn의 기본값은 무제한이라, 브라우저 탭이
rem 열려서 연결(폴링/WebSocket)이 계속 살아있으면 Ctrl-C를 눌러도 그 연결들이
rem 전부 끊어질 때까지 무한 대기한다 -- 실사용 중 Ctrl-C로 서버가 안 꺼지는
rem 증상의 실제 원인(Requirement.md 참고). 5초로 제한해 그 시점 이후엔 남은
rem 연결/작업을 강제로 취소하고 애플리케이션 종료(lifespan shutdown)로 넘어가게 한다.
.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5
goto :eof

:error
echo.
echo 실패했습니다. Python 3.11 이상이 설치되어 있고 PATH에 등록되어 있는지 확인하세요.
echo 설치: https://www.python.org/downloads/  (설치 시 "Add python.exe to PATH" 체크)
pause
