# Instalación de Artisa en Windows con un solo comando (la ejecuta INSTALAR_ARTISA.bat).
#
# Deja todo listo desde un equipo nuevo: WSL2 con Ubuntu, Docker, el proyecto, la base de
# datos (desde un respaldo, la API de la empresa o el JSON histórico), el acceso desde la red y
# el arranque automático al reiniciar. Se puede volver a ejecutar cuando se quiera: lo que ya
# está hecho se salta y nunca se borran datos. Registro: C:\ProgramData\Artisa\instalacion.log
param([switch]$Continuar)

$ErrorActionPreference = "Stop"
$Distro = "Ubuntu-24.04"
$UsuarioLinux = "artisa"
$Puerto = 8080
$Proyecto = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path      # carpeta "Capstone Artisa"
$Bat = Join-Path $Proyecto "INSTALAR_ARTISA.bat"
$Registro = Join-Path $env:ProgramData "Artisa"
New-Item -ItemType Directory -Force -Path $Registro | Out-Null
Start-Transcript -Path (Join-Path $Registro "instalacion.log") -Append | Out-Null
$env:WSL_UTF8 = "1"

function Paso([int]$n, [string]$texto) {
    Write-Host ""
    Write-Host "[$n/9] $texto" -ForegroundColor Cyan
}
function Aviso([string]$texto) { Write-Host "   $texto" -ForegroundColor Yellow }
function Fallo([string]$texto) {
    Write-Host ""
    Write-Host "ERROR: $texto" -ForegroundColor Red
    Write-Host "Registro completo: $(Join-Path $Registro 'instalacion.log')"
    Stop-Transcript | Out-Null
    Read-Host "Presiona Enter para cerrar"
    exit 1
}
function Preguntar([string]$texto, [string]$porDefecto = "") {
    $sufijo = if ($porDefecto) { " [$porDefecto]" } else { "" }
    $respuesta = Read-Host "   $texto$sufijo"
    if ([string]::IsNullOrWhiteSpace($respuesta)) { return $porDefecto }
    return $respuesta.Trim()
}
function Distros {
    (& wsl.exe -l -q 2>$null) | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ }
}

# La parte Linux va en preparar_ubuntu.sh. Se copia con finales de línea Linux a una carpeta
# temporal (por si el ZIP o git lo dejaron con finales de Windows) y se ejecuta dentro de Ubuntu.
$scriptLinux = Join-Path $env:TEMP "artisa_preparar_ubuntu.sh"
$contenido = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "preparar_ubuntu.sh")) -replace "`r`n", "`n"
[IO.File]::WriteAllText($scriptLinux, $contenido, (New-Object Text.UTF8Encoding($false)))

# Ruta de Windows vista desde Ubuntu (C:\... -> /mnt/c/...). --exec: sin shell intermedio, así
# los espacios y las barras de la ruta llegan intactos.
function RutaLinux([string]$ruta) {
    return (& wsl.exe -d $Distro -u root --exec wslpath -a $ruta).Trim()
}

function Ubuntu([string]$usuario, [string[]]$argumentos) {
    $ruta = RutaLinux $scriptLinux
    & wsl.exe -d $Distro -u $usuario --exec bash $ruta @argumentos
    if ($LASTEXITCODE -ne 0) { Fallo "Falló el paso '$($argumentos[0])' dentro de Ubuntu." }
}

Write-Host ""
Write-Host "=== Instalación de Artisa ===" -ForegroundColor Green
Write-Host "Carpeta del proyecto: $Proyecto"

# --- 1. WSL2 -----------------------------------------------------------------------------
Paso 1 "Subsistema de Windows para Linux (WSL2)"
$faltan = @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform") | Where-Object {
    (Get-WindowsOptionalFeature -Online -FeatureName $_).State -ne "Enabled" }
if ($faltan) {
    Aviso "Activando: $($faltan -join ', ')"
    $ok = $false
    try { & wsl.exe --install --no-distribution 2>$null; $ok = ($LASTEXITCODE -eq 0) } catch { $ok = $false }
    if (-not $ok) {
        foreach ($f in $faltan) { Enable-WindowsOptionalFeature -Online -FeatureName $f -NoRestart -All | Out-Null }
    }
    # Al volver a iniciar sesión, la instalación sigue sola.
    Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "ArtisaInstalacion" -Value "`"$Bat`""
    Write-Host ""
    Write-Host "Windows necesita reiniciar para terminar de activar WSL2." -ForegroundColor Yellow
    Write-Host "Al iniciar sesión de nuevo, la instalación continúa sola (acepta el aviso de administrador)."
    if ((Preguntar "¿Reiniciar ahora? (S/N)" "S") -match "^[sS]") { Stop-Transcript | Out-Null; Restart-Computer -Force }
    Stop-Transcript | Out-Null
    exit 0
}
& wsl.exe --update 2>$null | Out-Null
& wsl.exe --set-default-version 2 2>$null | Out-Null
Write-Host "   WSL2 listo."

# --- 2. Ubuntu ---------------------------------------------------------------------------
Paso 2 "Ubuntu ($Distro)"
if ((Distros) -notcontains $Distro) {
    Aviso "Descargando e instalando Ubuntu (varios minutos)..."
    & wsl.exe --install -d $Distro --no-launch
    if ($LASTEXITCODE -ne 0) { Fallo "No se pudo instalar Ubuntu. ¿Hay internet? ¿Está activada la virtualización en la BIOS?" }
    $lanzador = Get-Command "ubuntu2404.exe" -ErrorAction SilentlyContinue
    if ($lanzador) { & $lanzador.Source install --root | Out-Null }
}
& wsl.exe -d $Distro -u root --exec true
if ($LASTEXITCODE -ne 0) { Fallo "Ubuntu no arranca. Revisa que la virtualización esté activada en la BIOS." }
Ubuntu "root" @("configurar", $UsuarioLinux)
& wsl.exe --terminate $Distro | Out-Null     # aplica systemd y el usuario por defecto
Write-Host "   Ubuntu listo."

# --- 3. Docker ---------------------------------------------------------------------------
Paso 3 "Docker, Git y Python dentro de Ubuntu"
Ubuntu "root" @("docker", $UsuarioLinux)

# --- 4. Proyecto -------------------------------------------------------------------------
Paso 4 "Proyecto Artisa"
$rutaWindows = RutaLinux $Proyecto
Ubuntu $UsuarioLinux @("proyecto", $rutaWindows)

# --- 5. Datos: qué cargar ----------------------------------------------------------------
Paso 5 "Datos"
$respaldo = ""
$respaldos = @(Get-ChildItem -Path (Join-Path $Proyecto "respaldos") -Filter "artisa_*.sql.gz" -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending)
if ($respaldos.Count -gt 0) {
    $ultimo = $respaldos[0].Name
    if ((Preguntar "Hay un respaldo de la base ($ultimo). ¿Restaurarlo? Es lo más rápido (S/N)" "S") -match "^[sS]") {
        $respaldo = $ultimo
    }
} else {
    Write-Host "   (Para traer los datos de otro equipo, copia su respaldo artisa_AAAA-MM-DD.sql.gz en"
    Write-Host "    '$Proyecto\respaldos' y su .env en '$Proyecto' antes de instalar.)"
}
$urlActual = ""
$envWindows = Join-Path $Proyecto ".env"
if (Test-Path $envWindows) {
    $linea = Select-String -Path $envWindows -Pattern "^COMPANY_API_URL=(.+)$" | Select-Object -First 1
    if ($linea) { $urlActual = $linea.Matches[0].Groups[1].Value.Trim() }
}
$url = Preguntar "URL de la API de la empresa, ej. http://10.0.0.5:3003 (Enter = omitir)" $urlActual
$desde = ""
if ($url -and -not $respaldo -and $url -ne $urlActual) {
    $desde = Preguntar "Desde qué día traer el historial de la API (AAAA-MM-DD)" "2026-04-01"
}
$historico = Preguntar "Carpeta con el JSON histórico, solo desarrollo (Enter = omitir)" ""

# --- 6. Levantar -------------------------------------------------------------------------
Paso 6 "Levantando Artisa (la primera vez tarda varios minutos)"
$argumentos = @("iniciar")
if ($respaldo) { $argumentos += $respaldo }
Ubuntu $UsuarioLinux $argumentos
if ($url -and $url -ne $urlActual -or $desde) {
    $argumentos = @("api", $url)
    if ($desde) { $argumentos += $desde }
    Ubuntu $UsuarioLinux $argumentos
}
if ($historico) {
    $rutaHistorico = RutaLinux $historico
    Ubuntu $UsuarioLinux @("historico", $rutaHistorico)
}

# --- 7. Acceso desde la red ---------------------------------------------------------------
Paso 7 "Acceso desde otros equipos de la red"
Ubuntu $UsuarioLinux @("red")
$ip = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
    Select-Object -First 1).IPv4Address.IPAddress
if (-not (Get-NetFirewallRule -DisplayName "Artisa $Puerto" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Artisa $Puerto" -Direction Inbound -Protocol TCP -LocalPort $Puerto -Action Allow | Out-Null
}
if ($ip) {
    & netsh interface portproxy delete v4tov4 listenaddress=$ip listenport=$Puerto 2>$null | Out-Null
    & netsh interface portproxy add v4tov4 listenaddress=$ip listenport=$Puerto connectaddress=127.0.0.1 connectport=$Puerto | Out-Null
    Write-Host "   Desde la red: http://${ip}:$Puerto"
} else {
    Aviso "No se encontró la IP de red de este equipo; por ahora solo funciona en http://localhost:$Puerto"
}

# --- 8. Arranque automático --------------------------------------------------------------
Paso 8 "Arranque automático al reiniciar el equipo"
if (-not (Get-ScheduledTask -TaskName "Artisa (Ubuntu WSL)" -ErrorAction SilentlyContinue)) {
    $accion = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro --exec sleep infinity"
    $inicio = New-ScheduledTaskTrigger -AtStartup
    $usuario = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
    $ajustes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName "Artisa (Ubuntu WSL)" -Action $accion -Trigger $inicio -Principal $usuario -Settings $ajustes | Out-Null
}
Start-ScheduledTask -TaskName "Artisa (Ubuntu WSL)"
Write-Host "   Ubuntu, Docker y Artisa arrancan solos cuando se enciende el equipo."

# --- 9. Listo ----------------------------------------------------------------------------
Paso 9 "Listo"
Ubuntu $UsuarioLinux @("estado")
Write-Host ""
Write-Host "=== Artisa instalado ===" -ForegroundColor Green
Write-Host "Web en este equipo:  http://localhost:$Puerto   (usuario: admin)"
if ($ip) { Write-Host "Web desde la red:    http://${ip}:$Puerto" }
Write-Host "La contraseña de admin se mostró arriba la primera vez (líneas con '>>>'). Si se usó un"
Write-Host ".env de otro equipo, es la misma de ese equipo."
Write-Host "Los días se procesan solos por detrás; con muchos meses de datos puede tardar unas horas."
Write-Host "Para comandos: abre 'Ubuntu 24.04' desde Inicio y entra con: cd ~/'Artisa/Capstone Artisa'"
Start-Process "http://localhost:$Puerto"
Stop-Transcript | Out-Null
Read-Host "Presiona Enter para cerrar"
