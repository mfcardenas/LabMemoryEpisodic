@echo off
rem Arranca EpiGraph: servicios del docker-compose (Qdrant, transcripcion) y
rem despues la API con el front, que vigila la carpeta de watch.toml. Los
rem argumentos se pasan a "epigraph serve", p. ej.:  start.bat --port 8200
rem Variables: ver scripts\launcher_env.bat
setlocal
cd /d "%~dp0"
call "scripts\launcher_env.bat" || exit /b 1

if /i "%EPIGRAPH_COMPOSE_SERVICES%"=="none" goto :app
where docker >nul 2>nul
if errorlevel 1 goto :no_docker
echo [start] Levantando servicios: %EPIGRAPH_COMPOSE_SERVICES%
docker compose up -d --wait --wait-timeout 180 %EPIGRAPH_COMPOSE_SERVICES%
if errorlevel 1 goto :compose_failed

:app
echo [start] EpiGraph con %PY%
"%PY%" -m episodic serve %*
exit /b %errorlevel%

:no_docker
echo [start] AVISO: docker no esta disponible; se arranca EpiGraph sin levantar %EPIGRAPH_COMPOSE_SERVICES%.
goto :app

:compose_failed
echo [start] ERROR: no se pudieron levantar %EPIGRAPH_COMPOSE_SERVICES%.
echo         Si el puerto de Qdrant lo ocupa otro contenedor, ver docs\qdrant.md.
echo         Para arrancar sin el compose: set EPIGRAPH_COMPOSE_SERVICES=none
exit /b 1
