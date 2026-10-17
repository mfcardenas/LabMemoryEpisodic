@echo off
rem Prueba de escala sin intervencion (Fase S): dos perfiles seguidos.
rem   1) 200 000 eventos con vectores hash: mide SQLite, FTS y Qdrant.
rem   2) 50 000 eventos con Nomic y el reordenador reales (lento, horas en CPU).
rem Usa su propio directorio de datos y sus propias colecciones
rem (episodic_memory_scale_*); nunca toca las capturas reales.
rem Argumentos opcionales: EVENTS_HASH EVENTS_REAL (p. ej. scale_test.bat 200000 50000)
setlocal
cd /d "%~dp0\.."
call "scripts\launcher_env.bat" || exit /b 1
set "EVENTS_HASH=%~1"
if "%EVENTS_HASH%"=="" set "EVENTS_HASH=200000"
set "EVENTS_REAL=%~2"
if "%EVENTS_REAL%"=="" set "EVENTS_REAL=50000"
set PYTHONIOENCODING=utf-8

echo [escala] perfil hash: %EVENTS_HASH% eventos
"%PY%" scripts\scale_test.py --events %EVENTS_HASH% --fake-encoders --reset || exit /b 1
echo [escala] perfil real: %EVENTS_REAL% eventos (Nomic + reordenador)
"%PY%" scripts\scale_test.py --events %EVENTS_REAL% --reset --profile real || exit /b 1
echo [escala] informes en data\eval\escala-*.json
