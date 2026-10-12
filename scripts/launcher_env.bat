@echo off
rem Comun a start.bat y reset.bat (se invoca con "call", sin setlocal propio):
rem lee de .env las variables de los lanzadores, aplica los valores por
rem defecto y deja en PY el Python del entorno Conda.
rem   EPIGRAPH_PYTHON            python.exe a usar (por defecto, el del entorno Conda)
rem   EPIGRAPH_CONDA_ENV         entorno Conda (LabMemoryEpic01)
rem   EPIGRAPH_COMPOSE_SERVICES  servicios que levanta start.bat ("qdrant speech"; "none")

if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="EPIGRAPH_PYTHON" if not defined EPIGRAPH_PYTHON set "EPIGRAPH_PYTHON=%%B"
    if /i "%%A"=="EPIGRAPH_CONDA_ENV" if not defined EPIGRAPH_CONDA_ENV set "EPIGRAPH_CONDA_ENV=%%B"
    if /i "%%A"=="EPIGRAPH_COMPOSE_SERVICES" if not defined EPIGRAPH_COMPOSE_SERVICES set "EPIGRAPH_COMPOSE_SERVICES=%%B"
  )
)
if not defined EPIGRAPH_CONDA_ENV set "EPIGRAPH_CONDA_ENV=LabMemoryEpic01"
if not defined EPIGRAPH_COMPOSE_SERVICES set "EPIGRAPH_COMPOSE_SERVICES=qdrant speech"

set "PY=%EPIGRAPH_PYTHON%"
if not defined PY if exist "%USERPROFILE%\.conda\envs\%EPIGRAPH_CONDA_ENV%\python.exe" set "PY=%USERPROFILE%\.conda\envs\%EPIGRAPH_CONDA_ENV%\python.exe"
if not defined PY if defined CONDA_EXE for %%I in ("%CONDA_EXE%\..\..") do if exist "%%~fI\envs\%EPIGRAPH_CONDA_ENV%\python.exe" set "PY=%%~fI\envs\%EPIGRAPH_CONDA_ENV%\python.exe"
if not defined PY set "PY=python"
"%PY%" -c "import episodic" >nul 2>nul
if errorlevel 1 goto :no_python
exit /b 0

:no_python
echo [epigraph] ERROR: "%PY%" no puede importar EpiGraph. Defina EPIGRAPH_PYTHON o
echo            instale el paquete en el entorno %EPIGRAPH_CONDA_ENV%: python -m pip install -e ".[dev,video]"
exit /b 1
