# Mishop Monitor

App de bandeja del sistema (Windows 10/11) que reporta actividad al CRM Mishop:
app y pestaña activa, tiempo activo/inactivo, capturas de pantalla y pausas con motivo.
El trabajador la controla desde el iconito: Iniciar turno · Pausar… · Reanudar · Terminar turno.

## Cómo llega a cada trabajador

1. El trabajador entra a su CRM → **Mi rendimiento** → **Instalar Mishop Monitor en esta PC**.
2. El CRM le entrega el instalador (`MishopMonitorSetup.exe`, Inno Setup) con un pequeño bloque al
   final que lo vincula a su cuenta (`MISHOPCFG1{...}MISHOPEND1`). No hay códigos ni archivos aparte.
3. Doble clic → Siguiente → Listo: se instala por usuario en `%LOCALAPPDATA%\Programs\Mishop Monitor`
   (sin contraseña de administrador), queda en "Aplicaciones instaladas" con desinstalador, se registra
   para arrancar con Windows y se abre con `--instalador "<ruta>"` para leer el bloque y guardar el token
   en `%APPDATA%\MishopMonitor\config.json`.

## Cómo se arma el .exe

Automático: cada cambio en `main` corre `.github/workflows/build.yml` en una máquina Windows de GitHub:
PyInstaller en modo `--onedir` (carpeta, no `.exe` autoextraíble: menos falsos positivos de antivirus) y
luego Inno Setup (`instalador.iss`) arma el instalador, publicado en el release fijo **latest**:

`https://github.com/<cuenta>/mishop-monitor/releases/latest/download/MishopMonitorSetup.exe`

Manual (solo para probar en tu PC): `Construir-Windows.bat` (necesita Python 3.12 con "Add to PATH").

## Requisitos

Windows 10 u 11 de 64 bits. No necesita Python ni permisos de administrador.
El instalador no está firmado todavía: la primera vez Windows puede mostrar "Windows protegió su PC" →
**Más información → Ejecutar de todas formas**. Algunos antivirus pueden marcarlo como falso positivo;
la solución definitiva es firmarlo con un certificado de firma de código.
