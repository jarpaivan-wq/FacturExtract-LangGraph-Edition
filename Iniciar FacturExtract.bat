@echo off
rem Doble clic para abrir FacturExtract. La primera vez prepara todo solo.
where pyw >nul 2>nul
if errorlevel 1 (
  echo No se encontro Python en este equipo.
  echo Instale Python 3.10 o superior desde https://www.python.org/downloads/
  echo y marque la opcion "py launcher". Luego vuelva a abrir este archivo.
  pause
  exit /b 1
)
start "" pyw "%~dp0lanzador.py" %*
