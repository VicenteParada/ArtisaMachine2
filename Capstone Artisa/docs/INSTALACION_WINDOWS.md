# Instalación en un servidor Windows

Artisa corre en Linux con Docker. En Windows se usa **WSL2** (un Ubuntu dentro de Windows) con
Docker instalado dentro de ese Ubuntu. No hace falta Docker Desktop (que pide licencia pagada
en empresas grandes). **Ningún comando de esta guía borra datos.**

Requisitos: Windows 10 (22H2) / 11 o Windows Server 2022 o más nuevo, virtualización activada
en la BIOS, 8 GB de RAM o más, 30 GB libres, acceso a internet y a la red de la API de la empresa.

## Forma rápida: un solo comando

1. Descarga el proyecto en el equipo Windows: en GitHub, **Code → Download ZIP** (rama `main`), y
   descomprímelo, por ejemplo en `C:\Artisa`.
2. (Opcional, para traer los datos de otro equipo) copia en la carpeta `Capstone Artisa`:
   - su `.env` (mismas claves y la misma contraseña de `admin`), y
   - su respaldo más reciente `artisa_AAAA-MM-DD.sql.gz` dentro de `Capstone Artisa\respaldos`.
3. Doble clic en **`Capstone Artisa\INSTALAR_ARTISA.bat`** (o ejecútalo en una terminal) y acepta
   el aviso de administrador.

El instalador hace todo lo de esta guía: activa WSL2 (si Windows pide reiniciar, al volver a
iniciar sesión sigue solo), instala Ubuntu y Docker, trae el proyecto, levanta Artisa, carga los
datos (restaura el respaldo, o conecta la API desde el día que indiques, o carga el JSON
histórico), abre el acceso desde la red, deja el arranque automático y abre la web. Solo pregunta
la URL de la API y desde qué día traer datos. Se puede volver a ejecutar: no borra nada y sirve
para actualizar. Registro: `C:\ProgramData\Artisa\instalacion.log`.

Los pasos de abajo son lo mismo, a mano, por si algo falla.

En cada paso se indica dónde se escribe: **PowerShell (Administrador)** o **Ubuntu**.

---

## 1. Instalar Ubuntu (WSL2) — PowerShell (Administrador)

Clic derecho en Inicio → "Terminal (Administrador)" o "Windows PowerShell (Administrador)":

```powershell
wsl --install -d Ubuntu-24.04
```

Reinicia el equipo. Al volver, se abre Ubuntu y pide crear un **usuario y contraseña de Linux**
(anótalos). Si no se abre solo, búscalo en Inicio como "Ubuntu 24.04".

## 2. Activar systemd (para que Docker arranque solo) — Ubuntu

```bash
printf '[boot]\nsystemd=true\n' | sudo tee /etc/wsl.conf
```

Luego en **PowerShell (Administrador)**:

```powershell
wsl --shutdown
```

Vuelve a abrir Ubuntu desde Inicio.

## 3. Instalar Docker y Git — Ubuntu

```bash
sudo apt update && sudo apt install -y git python3 curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
sudo systemctl enable --now docker
```

Cierra Ubuntu, ábrelo de nuevo y comprueba:

```bash
docker run --rm hello-world
```

## 4. Descargar Artisa — Ubuntu

Se clona dentro de Ubuntu (no en `C:\`): así es más rápido y no hay problemas de permisos.

```bash
cd ~
git clone https://github.com/angelomanque12-jpg/Artisa.git
cd "Artisa/Capstone Artisa"
git checkout main
```

Si GitHub pide usuario y contraseña (repositorio privado), la contraseña es un *token*: en
GitHub → Settings → Developer settings → Personal access tokens → Fine-grained, con permiso de
solo lectura sobre el repositorio.

## 5. Levantar Artisa — Ubuntu

```bash
python3 scripts/artisa.py iniciar
```

La primera vez muestra la **contraseña del usuario `admin`**: guárdala. Tarda varios minutos.
Al terminar, en el navegador de ese servidor: http://localhost:8080

## 6. Conectar la API de la empresa — Ubuntu

```bash
python3 scripts/artisa.py conectar-api http://IP_DE_LA_API:3003 --desde 2026-04-01
```

`--desde` es desde qué día traer el historial la primera vez. Avance:
`python3 scripts/artisa.py logs telemetria` (Ctrl+C para salir, no detiene nada).

## 7. Asistente (opcional) — Ubuntu

```bash
nano .env
```

Completa `GEMINI_API_KEY=tu_clave` (sin espacios ni comillas), guarda con Ctrl+O, Enter, Ctrl+X, y:

```bash
python3 scripts/artisa.py iniciar
```

## 8. Entrar desde otros equipos de la red

En **Ubuntu**:

```bash
python3 scripts/artisa.py acceso-red si
```

En **PowerShell (Administrador)**. Primero mira la IP del servidor en la red (`ipconfig`,
"Dirección IPv4", por ejemplo 192.168.1.50) y úsala en lugar de `IP_DEL_SERVIDOR`:

```powershell
New-NetFirewallRule -DisplayName "Artisa 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow
netsh interface portproxy add v4tov4 listenaddress=IP_DEL_SERVIDOR listenport=8080 connectaddress=127.0.0.1 connectport=8080
```

Desde otro equipo: `http://IP_DEL_SERVIDOR:8080`. La regla y el redireccionamiento quedan
guardados aunque se reinicie el servidor. (Si cambia la IP del servidor, borra el anterior con
`netsh interface portproxy delete v4tov4 listenaddress=IP_ANTERIOR listenport=8080` y crea el nuevo.)

## 9. Que arranque solo al reiniciar el servidor — PowerShell (Administrador)

Windows apaga Ubuntu cuando nadie lo usa. Esta tarea lo deja encendido desde que arranca el
equipo, aunque nadie inicie sesión (Docker y Artisa arrancan solos dentro de él):

```powershell
$accion = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu-24.04 --exec sleep infinity"
$inicio = New-ScheduledTaskTrigger -AtStartup
$usuario = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
$ajustes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "Artisa (Ubuntu WSL)" -Action $accion -Trigger $inicio -Principal $usuario -Settings $ajustes
```

**Pruébalo:** reinicia el servidor, no inicies sesión en Ubuntu, espera 3 minutos y abre
`http://IP_DEL_SERVIDOR:8080` desde otro equipo.

## 10. Comprobar — Ubuntu

```bash
cd ~/"Artisa/Capstone Artisa"
python3 scripts/artisa.py estado
```

Todos los servicios deben decir `healthy` y la telemetría sin errores.

---

## Uso diario

Todo se hace en **Ubuntu**, dentro de la carpeta del proyecto (`cd ~/"Artisa/Capstone Artisa"`).
Los comandos son los mismos de [INSTALACION.md](INSTALACION.md) (sección 4).

| Para | Comando |
|---|---|
| Actualizar a la última versión | `git pull origin main` y luego `python3 scripts/artisa.py iniciar` |
| Ver estado | `python3 scripts/artisa.py estado` |
| Cambiar la contraseña de la web | `python3 scripts/artisa.py contrasena` |
| Detener (sin borrar datos) | `python3 scripts/artisa.py detener` |

**Respaldos:** quedan en Ubuntu, en `~/Artisa/Capstone Artisa/respaldos`. Desde el Explorador de
Windows se abren en `\\wsl$\Ubuntu-24.04\home\TU_USUARIO_LINUX\Artisa\Capstone Artisa\respaldos`;
conviene copiarlos de vez en cuando a otro equipo.

**Nunca** usar `docker compose down -v`, `wsl --unregister` ni desinstalar Ubuntu: borran la base de datos.

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| `wsl --install` dice que falta virtualización | Activar "Virtualization / VT-x / SVM" en la BIOS del servidor |
| `docker: permission denied` | Cerrar y volver a abrir Ubuntu (el grupo `docker` se aplica al reabrir) |
| Funciona en el servidor pero no desde otro equipo | Revisar el paso 8 (firewall y `netsh`) y que la IP sea la actual (`ipconfig`) |
| Tras reiniciar no responde | Abrir Ubuntu una vez; si así funciona, revisar la tarea del paso 9 en el Programador de tareas |
| No llega telemetría | El servidor debe estar en la red de la API: `python3 scripts/artisa.py estado` |
