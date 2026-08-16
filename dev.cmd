@echo off
setlocal enabledelayedexpansion
cd /d %~dp0

echo ============================================
echo   DocuMind - Dev Launcher
echo ============================================
echo.

echo --- Step 1/3: Check Environment ---
echo.

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERR] Node.js not found
    exit /b 1
)
for /f "tokens=*" %%i in ('node -v') do set NODE_VER=%%i
echo   [OK]  Node.js  !NODE_VER!

where pnpm >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERR] pnpm not found
    exit /b 1
)
for /f "tokens=*" %%i in ('pnpm -v') do set PNPM_VER=%%i
echo   [OK]  pnpm     v!PNPM_VER!

where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERR] uv not found
    exit /b 1
)
for /f "tokens=*" %%i in ('uv --version') do set UV_VER=%%i
echo   [OK]  uv       !UV_VER!

if exist "frontend\node_modules\" (
    echo   [OK]  frontend\node_modules\
) else (
    echo   [!!]  frontend\node_modules\ missing
)
if exist "backend\.venv\" (
    echo   [OK]  backend\.venv\
) else (
    echo   [!!]  backend\.venv\ missing
)
echo.

echo --- Step 2/3: Install Dependencies ---
echo.

if not exist "frontend\node_modules\" (
    echo   Installing frontend...
    cd frontend
    call pnpm install
    cd ..
    echo   [OK]  frontend done
)

if not exist "backend\.venv\" (
    echo   Installing backend...
    cd backend
    uv sync --dev
    cd ..
    echo   [OK]  backend done
)

echo.

echo --- Step 3/3: Launch Services ---
echo.

rem Clear leftover processes on :5172 (uvicorn reloader spawn can linger
rem after a previous run and cause WinError 2 on the next start)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5172" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)
if %errorlevel% equ 0 (
    echo   [OK]  Port 5172 cleared
) else (
    echo   [OK]  Port 5172 free
)

echo   Starting backend  ^(FastAPI :5172^)...
start "DocuMind-Backend" cmd /k "cd /d %~dp0backend  && title DocuMind-Backend  && uv run python src\main.py"

echo   Starting frontend ^(Vite :5173^)...
start "DocuMind-Frontend" cmd /k "cd /d %~dp0frontend && title DocuMind-Frontend && pnpm dev"

echo.
echo ============================================
echo   Backend   http://localhost:5172/docs
echo   Frontend  http://localhost:5173
echo ============================================
echo.
echo   Note: start Neo4j manually before this script if you need the graph.
echo   Close the two cmd windows to stop.
