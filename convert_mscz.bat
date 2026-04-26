@echo off
setlocal EnableDelayedExpansion

:: =======================================================
:: CONFIGURATION
:: =======================================================

:: Path to MuseScore 4 Executable
:: Default location is usually: "C:\Program Files\MuseScore 4\bin\MuseScore4.exe"
set "MS4_PATH=C:\Program Files\MuseScore 4\bin\MuseScore4.exe"

:: Directory containing your .mscz files
:: "." means the current folder where this script is located
set "SOURCE_DIR=C:\Users\fiery\GitHub\Animenz-LM\00_mscz_raw"

:: Directory where you want the .musicxml files to go
set "DEST_DIR=C:\Users\fiery\GitHub\Animenz-LM\01_xml_raw"

:: File extension for output (uncompressed is recommended)
set "OUT_EXT=.musicxml"

:: =======================================================
:: SCRIPT EXECUTION
:: =======================================================

echo Checking for MuseScore 4 at: "%MS4_PATH%"
if not exist "%MS4_PATH%" (
    echo [ERROR] MuseScore 4 executable not found. 
    echo Please edit the MS4_PATH variable in this script.
    pause
    exit /b
)

if not exist "%DEST_DIR%" (
    echo Creating destination directory...
    mkdir "%DEST_DIR%"
)

echo Starting batch conversion...
echo -------------------------------------------------------

:: Loop through every .mscz file in the source directory
for %%f in ("%SOURCE_DIR%\*.mscz") do (
    set "FILENAME=%%~nf"
    
    echo Converting: "%%~nxf"
    
    :: CALL MuseScore 4 CLI
    :: Syntax: MuseScore4.exe "input" -o "output"
    "%MS4_PATH%" "%%f" -o "%DEST_DIR%\!FILENAME!%OUT_EXT%"
    
    if !errorlevel! equ 0 (
        echo    [OK] Exported to %DEST_DIR%\!FILENAME!%OUT_EXT%
    ) else (
        echo    [FAIL] Could not convert %%~nxf
    )
)

echo -------------------------------------------------------
echo Conversion complete.
pause