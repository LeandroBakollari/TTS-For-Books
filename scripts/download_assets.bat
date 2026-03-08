@echo off
setlocal

python "%~dp0download_offline_assets.py" %*
if errorlevel 1 (
  echo.
  echo Asset download failed.
  exit /b 1
)

echo.
echo Assets downloaded successfully.
exit /b 0
