@echo off
rem NOLGIA for DaVinci Resolve: copies the plugin into Resolve's Scripts folder for this user.
setlocal
set "SOURCE=%~dp0Utility"
set "TARGET=%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
if not exist "%SOURCE%\NOLGIA.py" (
  echo Could not find the Utility folder next to this installer. Unzip the whole download first.
  goto failed
)
if not exist "%TARGET%" mkdir "%TARGET%" || goto failed
copy /Y "%SOURCE%\NOLGIA.py" "%TARGET%\NOLGIA.py" >nul || goto failed
copy /Y "%SOURCE%\nolgia_resolve.zip" "%TARGET%\nolgia_resolve.zip" >nul || goto failed
echo NOLGIA is installed in:
echo   %TARGET%
echo Restart DaVinci Resolve, then open Workspace ^> Scripts ^> NOLGIA.
if /I not "%~1"=="/quiet" pause
exit /b 0
:failed
echo NOLGIA was not installed.
if /I not "%~1"=="/quiet" pause
exit /b 1
