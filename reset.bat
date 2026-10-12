@echo off
rem Borra los datos de EpiGraph para repetir cargas: SQLite, medios, indice
rem local y la coleccion de EpiGraph en Qdrant. No toca la carpeta vigilada
rem ni _procesados: las cargas se ponen a mano. EpiGraph debe estar parado.
rem   reset.bat          pide confirmacion
rem   reset.bat --yes    sin preguntar
setlocal
cd /d "%~dp0"
call "scripts\launcher_env.bat" || exit /b 1
"%PY%" -m episodic reset-data %*
exit /b %errorlevel%
