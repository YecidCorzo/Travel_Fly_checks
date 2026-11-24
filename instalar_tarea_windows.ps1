# Programa el vigilante para que se ejecute cada hora en Windows.
# Uso (en PowerShell, dentro de la carpeta del proyecto):
#   powershell -ExecutionPolicy Bypass -File .\instalar_tarea_windows.ps1

$carpeta = Split-Path -Parent $MyInvocation.MyCommand.Path
$python  = Join-Path $carpeta ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $python)) { $python = (Get-Command pythonw.exe).Source }

$accion   = New-ScheduledTaskAction -Execute $python -Argument "`"$carpeta\vigilante.py`"" -WorkingDirectory $carpeta
$disparo  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1)
$ajustes  = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName "VigilanteVuelosBarcelona" -Action $accion -Trigger $disparo -Settings $ajustes -Force
Write-Host "Listo: la tarea 'VigilanteVuelosBarcelona' se ejecutará cada hora."
