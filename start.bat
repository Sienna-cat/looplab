@echo off
setlocal
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Please install Node.js 24 LTS from https://nodejs.org and try again.
  pause
  exit /b 1
)
node -e "if(Number(process.versions.node.split('.')[0]) < 24) { console.error('Node.js 24+ required'); process.exit(1); }"
if errorlevel 1 (pause & exit /b 1)
node --env-file-if-exists=.env server.mjs
pause
