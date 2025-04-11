@echo off
setlocal EnableDelayedExpansion

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

:: Navigate to the project directory
cd /d "%~dp0"

:: Activate the virtual environment
echo Activating Python virtual environment...
call ".venv\Scripts\activate"

:: Run your Python script in a new command shell.
:: This ensures that Ctrl+C only stops the Python process,
:: not the batch script itself.
echo Running main.py...
cmd /c ".venv\Scripts\python.exe main.py"
set "EXIT_CODE=%ERRORLEVEL%"

:: Check and log the exit code from the Python application
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
