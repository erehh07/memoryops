@echo off
setlocal EnableDelayedExpansion

echo ==============================
echo  MemoryOps AP Agent
echo ==============================

REM --- Python venv -------------------------------------------------------
if not exist ".venv" (
    echo Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 ( echo ERROR: python not found. Install Python 3.11+. && pause && exit /b 1 )
)

echo Installing / verifying Python dependencies...
.venv\Scripts\pip install -q -r requirements.txt
if errorlevel 1 ( echo ERROR: pip install failed. && pause && exit /b 1 )

REM --- .env --------------------------------------------------------------
if not exist ".env" (
    echo Copying .env.example to .env ...
    copy .env.example .env >nul
    echo NOTE: Edit .env to add optional GROQ / Hindsight keys.
)

REM --- Frontend ----------------------------------------------------------
if not exist "frontend\dist\index.html" (
    echo Building frontend ^(one-time^)...
    if not exist "frontend\node_modules" (
        pushd frontend
        call npm install
        popd
    )
    pushd frontend
    call npm run build
    popd
    if errorlevel 1 ( echo ERROR: frontend build failed. && pause && exit /b 1 )
)

REM --- Launch ------------------------------------------------------------
echo.
echo Starting MemoryOps at http://localhost:8000
echo Press Ctrl+C to stop.
echo.
.venv\Scripts\python -m uvicorn backend.app.main:app --port 8000
