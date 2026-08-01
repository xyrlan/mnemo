: << 'CMDBLOCK'
@echo off
REM Cross-platform polyglot wrapper around bin/launch.
REM On Windows cmd.exe runs the batch block below; on Unix the shell treats
REM `:` as a no-op and skips to the shell section at the bottom.
REM
REM The launcher is extensionless (`launch`, not `launch.sh`) because Claude
REM Code's Windows handling prepends `bash` to any command containing `.sh`,
REM which would double-invoke it.

set "MNEMO_BIN_DIR=%~dp0"

if exist "C:\Program Files\Git\bin\bash.exe" (
    "C:\Program Files\Git\bin\bash.exe" "%MNEMO_BIN_DIR%launch" %*
    exit /b %ERRORLEVEL%
)
if exist "C:\Program Files (x86)\Git\bin\bash.exe" (
    "C:\Program Files (x86)\Git\bin\bash.exe" "%MNEMO_BIN_DIR%launch" %*
    exit /b %ERRORLEVEL%
)

where bash >nul 2>&1
if %ERRORLEVEL%==0 (
    bash "%MNEMO_BIN_DIR%launch" %*
    exit /b %ERRORLEVEL%
)

REM No bash anywhere. Exit 0 regardless: this runs as a hook, where a non-zero
REM exit reads as a session error rather than "mnemo is unavailable".
echo mnemo: bash not found (install Git for Windows), skipping. >&2
exit /b 0
CMDBLOCK

MNEMO_BIN_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
exec bash "$MNEMO_BIN_DIR/launch" "$@"
