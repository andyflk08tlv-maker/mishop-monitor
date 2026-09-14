# Mishop Monitor

App de bandeja del sistema (Windows 10/11) que reporta actividad al CRM Mishop:
app y pestaña activa, tiempo activo/inactivo, capturas de pantalla y pausas con motivo.
El trabajador la controla desde el iconito: Iniciar turno · Pausar… · Reanudar · Terminar turno.

## Cómo llega a cada trabajador

1. El trabajador entra a su CRM → **Mi rendimiento** → **Instalar Mishop Monitor en esta PC**.
2. El CRM le entrega este mismo `.exe` con un pequeño bloque al final que lo vincula a su cuenta
   (`MISHOPCFG1{...}MISHOPEND1`). No hay códigos ni archivos aparte.
3. Doble clic: la app se copia a `%LOCALAPPDATA%\MishopMonitor`, guarda el token en
   `%APPDATA%\MishopMonitor\config.json`, se registra para arrancar con Windows y muestra "Listo".

## Cómo se arma el .exe

Automático: cada cambio en `main` corre `.github/workflows/build.yml` en una máquina Windows de GitHub
y publica `MishopMonitor.exe` en el release fijo **latest**:

`https://github.com/<cuenta>/mishop-monitor/releases/latest/download/MishopMonitor.exe`

Manual (solo para probar en tu PC): `Construir-Windows.bat` (necesita Python 3.12 con "Add to PATH").

## Requisitos

Windows 10 u 11 de 64 bits. No necesita Python ni permisos de administrador.
El `.exe` no está firmado todavía: la primera vez Windows puede mostrar "Windows protegió su PC" →
**Más información → Ejecutar de todas formas**.
