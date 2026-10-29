@echo off
rem Opens a private SSH tunnel to the app running on your server, and opens its sign-in page.
rem Use this when you do not want to go through the public address. Leave this window open while you work.
rem Usage: connect-server.bat SERVER SSHUSER [KEYFILE]   e.g.  connect-server.bat 203.0.113.10 deploy
if "%~2"=="" (
  echo Usage: connect-server.bat SERVER SSHUSER [KEYFILE]
  exit /b 1
)
set SERVER=%~1
set SSHUSER=%~2
set KEY=%~3
if "%KEY%"=="" set KEY=%USERPROFILE%\.ssh\id_ed25519

start "" "http://127.0.0.1:8765/"
echo Tunnel open. Sign in at http://127.0.0.1:8765/ . Close this window to disconnect.
ssh -i "%KEY%" -N -L 8765:127.0.0.1:8765 %SSHUSER%@%SERVER%
