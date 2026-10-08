@echo off
setlocal EnableDelayedExpansion

:: Resolve relative paths against this launcher, including the first venv check.
cd /d "%~dp0"
if errorlevel 1 (
    echo ERROR: Cannot enter the project directory.
    exit /b 1
)

:: Verify that the virtual environment exists
if not exist ".venv\Scripts\activate" (
    echo ERROR: Virtual environment not found.
    echo Please create a virtual environment in the .venv folder.
    pause
    exit /b 1
)

:MAIN_LOOP
cls
echo ==============================================
echo         Starting the application
echo ==============================================
echo.

:: Activate the virtual environment
echo Activating Python virtual environment...
call ".venv\Scripts\activate"

echo Running main.py...
cmd /c ".venv\Scripts\python.exe main.py"
set "EXIT_CODE=%ERRORLEVEL%"


if %EXIT_CODE% NEQ 0 (
    echo.
    echo Application exited with error code %EXIT_CODE%.
) else (
    echo.
    echo Application stopped normally.
)

echo.
:ASK_RESTART
choice /M "Do you want to restart the application?" /C YN
if errorlevel 2 goto END
if errorlevel 1 goto MAIN_LOOP

:END
endlocal
exit /b 0
