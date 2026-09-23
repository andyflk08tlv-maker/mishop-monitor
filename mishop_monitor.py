# -*- coding: utf-8 -*-
"""
Mishop Monitor — app de bandeja del sistema para Windows.

Llega como instalador estándar (Inno Setup, ver instalador.iss) descargado
desde el CRM ya vinculado a la persona: el CRM le pega al final del instalador
un bloque MISHOPCFG1{...}MISHOPEND1 con su token. Al terminar de instalar, el
instalador abre la app con `--instalador "<ruta>"`; la app lee el bloque de
ese archivo, guarda %APPDATA%\\MishopMonitor\\config.json y muestra "Listo".
Desde ahí vive como iconito en la bandeja con tres estados:
Activo (verde) / En pausa (ámbar) / Turno terminado (gris).
(Se conserva el modo antiguo: si el .exe se abre desde fuera de su carpeta de
instalación y trae el bloque pegado, se instala solo en %LOCALAPPDATA%.)
"""
import os, io, sys, json, time, uuid, base64, socket, shutil, tempfile, threading, ctypes, subprocess
from ctypes import wintypes
from datetime import datetime, timezone
from urllib import request as urlrequest
from urllib.error import HTTPError

APP_NAME = "Mishop Monitor"
EXE_NAME = "Mishop Monitor.exe"
VERSION = "1.9.2"

CONFIG_BASE = {
    "supabase_url": "https://dxokmvqqjfbxgqlhcire.supabase.co",
    "anon_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR4b2ttdnFxamZieGdxbGhjaXJlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk2NjA0NjUsImV4cCI6MjA5NTIzNjQ2NX0.CPgX8-gk6lErosTie8V8rS2Uxf0lSOVf_GJRyvz4BZM",
    "device_token": "",
    # El CRM recibe las capturas y las guarda en Storage (no dentro de la base).
    "crm_url": "https://mishopapp.com",
    "sample_interval_seconds": 15, "flush_interval_seconds": 60,
    "idle_threshold_seconds": 300, "screenshot_interval_minutes": 5,
    "auto_end_idle_minutes": 60,  # cierra el turno solo si no hay actividad por este tiempo
    "limits_check_seconds": 180,  # cada cuánto relee los límites que el dueño configuró en el CRM
    "resume_window_minutes": 30,  # si reinició estando Activo hace poco, reanuda sin preguntar
    # Auto-actualización: la app se mantiene al día sola (el trabajador no hace nada).
    "version_url": "https://github.com/andyflk08tlv-maker/mishop-monitor/releases/download/latest/version.json",
    "setup_url": "https://github.com/andyflk08tlv-maker/mishop-monitor/releases/download/latest/MishopMonitorSetup.exe",
    "update_check_hours": 6,
}

TRAILER_INICIO = b"MISHOPCFG1"
TRAILER_FIN = b"MISHOPEND1"

APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", APPDATA)
DATA_DIR = os.path.join(APPDATA, "MishopMonitor")
INSTALL_DIR = os.path.join(LOCALAPPDATA, "MishopMonitor")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "monitor.log")
INSTALLED_EXE = os.path.join(INSTALL_DIR, EXE_NAME)
INSTALL_DIR_INNO = os.path.join(LOCALAPPDATA, "Programs", APP_NAME)


def corre_desde_instalacion():
    """True si el .exe está en alguna de las carpetas de instalación."""
    aqui = os.path.normcase(os.path.dirname(ruta_exe()))
    return aqui in (os.path.normcase(INSTALL_DIR), os.path.normcase(INSTALL_DIR_INNO))


# ----------------------------------------------------------------- utilidades
def log(msg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + str(msg) + "\n")
    except Exception:
        pass


def ruta_exe():
    return os.path.abspath(sys.executable)


def es_exe_congelado():
    return bool(getattr(sys, "frozen", False))


def leer_trailer(ruta=None):
    """Bloque de configuración que el CRM pega al final del archivo descargado."""
    ruta = ruta or ruta_exe()
    if ruta == ruta_exe() and not es_exe_congelado():
        return None
    try:
        with open(ruta, "rb") as f:
            f.seek(0, os.SEEK_END)
            tam = f.tell()
            f.seek(max(0, tam - 8192))
            cola = f.read()
        fin = cola.rfind(TRAILER_FIN)
        ini = cola.rfind(TRAILER_INICIO, 0, fin if fin >= 0 else None)
        if ini < 0 or fin < 0 or fin <= ini:
            return None
        cuerpo = cola[ini + len(TRAILER_INICIO):fin].strip()
        datos = json.loads(cuerpo.decode("utf-8"))
        return datos if isinstance(datos, dict) else None
    except Exception as e:
        log("trailer ilegible: %r" % (e,))
        return None


def leer_config_guardada():
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def guardar_config(extra):
    os.makedirs(DATA_DIR, exist_ok=True)
    actual = leer_config_guardada()
    actual.update({k: v for k, v in extra.items() if v not in (None, "")})
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(actual, f, ensure_ascii=False, indent=2)


def armar_config():
    cfg = dict(CONFIG_BASE)
    cfg.update(leer_config_guardada())
    # Identificador estable de ESTA computadora (una por instalación). Sirve para el
    # candado de "una sola PC activa por persona". Se guarda y sobrevive a las
    # actualizaciones; solo se borra al desinstalar.
    if not cfg.get("machine_id"):
        mid = uuid.uuid4().hex
        try: guardar_config({"machine_id": mid})
        except Exception: pass
        cfg["machine_id"] = mid
    base = cfg["supabase_url"].rstrip("/") + "/rest/v1/rpc/"
    for k, fn in (("ingest_url", "ingest_activity"), ("settings_url", "get_monitor_settings"),
                  ("limits_url", "get_monitor_limits"), ("reclamar_url", "reclamar_equipo"),
                  ("screenshot_url", "ingest_screenshot"), ("pausa_iniciar_url", "pausa_iniciar"),
                  ("pausa_terminar_url", "pausa_terminar"),
                  ("confirmar_url", "confirmar_comando"), ("reportar_url", "reportar_estado")):
        cfg[k] = base + fn
    cfg["captura_url"] = cfg["crm_url"].rstrip("/") + "/api/monitor/captura"
    return cfg


# ------------------------------------------------------------ instalación
def registrar_arranque(ruta):
    """Arranca con Windows (clave Run del usuario; no necesita ser admin)."""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, "MishopMonitor", 0, winreg.REG_SZ, '"%s"' % ruta)
        winreg.CloseKey(k)
        return True
    except Exception as e:
        log("registro arranque fallo: %r" % (e,))
        return False


def cerrar_instancia_instalada():
    """Si ya hay una copia corriendo (versión anterior), se le pide cerrar."""
    try:
        # Solo las otras copias: nunca este mismo proceso (mismo nombre de archivo).
        subprocess.run(["taskkill", "/F", "/FI", "PID ne %d" % os.getpid(), "/IM", EXE_NAME],
                       capture_output=True, creationflags=0x08000000)
        time.sleep(1.0)
    except Exception as e:
        log("taskkill fallo: %r" % (e,))


def vincular_desde_instalador(ruta_instalador):
    """Primera apertura tras el instalador Inno: lee el bloque del instalador."""
    trailer = leer_trailer(ruta_instalador) or {}
    log("instalador %s → trailer %s" % (ruta_instalador, "encontrado (%s)" % trailer.get("persona", "") if trailer else "ausente"))
    token = trailer.get("device_token", "")
    if token:
        guardar_config({
            "device_token": token,
            "supabase_url": trailer.get("supabase_url", ""),
            "anon_key": trailer.get("anon_key", ""),
            "empresa": trailer.get("empresa", ""),
            "persona": trailer.get("persona", ""),
        })
        return trailer
    if leer_config_guardada().get("device_token"):
        return leer_config_guardada()  # reinstalación: ya estaba vinculado
    ventana_mensaje("No se pudo vincular",
                    "Este instalador no viene vinculado a tu cuenta.\n\n"
                    "Descarga Mishop Monitor desde tu CRM, en la sección\n"
                    "\"Mi rendimiento\" → \"Instalar Mishop Monitor en esta PC\".")
    return None


def instalar_desde_descarga():
    """Primera apertura del .exe descargado del CRM: copia, config, arranque automático."""
    trailer = leer_trailer() or {}
    log("trailer: %s" % ("encontrado (%s)" % trailer.get("persona", "") if trailer else "ausente"))
    token = trailer.get("device_token", "") or leer_config_guardada().get("device_token", "")
    if not token:
        ventana_mensaje("No se pudo vincular",
                        "Este archivo no viene vinculado a tu cuenta.\n\n"
                        "Descarga Mishop Monitor desde tu CRM, en la sección\n"
                        "\"Mi rendimiento\" → \"Instalar Mishop Monitor en esta PC\".")
        return False

    guardar_config({
        "device_token": token,
        "supabase_url": trailer.get("supabase_url", ""),
        "anon_key": trailer.get("anon_key", ""),
        "empresa": trailer.get("empresa", ""),
        "persona": trailer.get("persona", ""),
    })

    os.makedirs(INSTALL_DIR, exist_ok=True)
    origen = ruta_exe()
    if os.path.normcase(origen) != os.path.normcase(INSTALLED_EXE):
        cerrar_instancia_instalada()
        for intento in range(5):
            try:
                shutil.copy2(origen, INSTALLED_EXE)
                break
            except Exception as e:
                log("copia fallo (%d): %r" % (intento, e))
                time.sleep(1.0)
        else:
            ventana_mensaje("No se pudo instalar",
                            "No pude copiar el programa a tu carpeta de usuario.\n"
                            "Cierra Mishop Monitor si ya estaba abierto e inténtalo de nuevo.")
            return False

    registrar_arranque(INSTALLED_EXE)
    log("instalado en %s (persona=%s, empresa=%s)" % (INSTALLED_EXE, trailer.get("persona", ""), trailer.get("empresa", "")))

    try:
        subprocess.Popen([INSTALLED_EXE], close_fds=True,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_PROCESS_GROUP
    except Exception as e:
        log("no pude lanzar la copia instalada: %r" % (e,))

    ventana_listo(trailer.get("persona", ""), trailer.get("empresa", ""))
    return True


def ventana_mensaje(titulo, texto):
    try:
        ctypes.windll.user32.MessageBoxW(None, texto, APP_NAME + " — " + titulo, 0x40)
    except Exception:
        pass


def _rrect(cv, x1, y1, x2, y2, r, **kw):
    """Rectángulo redondeado en un Canvas de tkinter."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def _dibujar_pulso(cv, x, y, s, color, width):
    p = [(0.15, 0.52), (0.35, 0.52), (0.47, 0.32), (0.60, 0.76), (0.70, 0.48), (0.85, 0.48)]
    flat = []
    for px, py in p:
        flat += [x + px * s, y + py * s]
    cv.create_line(*flat, fill=color, width=width, capstyle="round", joinstyle="round")


def _dibujar_icono(cv, x, y, s, color="#15b36a"):
    """Dibuja el icono de la app (cuadrito redondeado + pulso) en un Canvas."""
    _rrect(cv, x, y, x + s, y + s, s * 0.24, fill=color, outline="")
    _dibujar_pulso(cv, x, y, s, "#ffffff", max(2, int(s * 0.09)))


def ventana_listo(persona, empresa):
    """Pantalla final del instalador: confirmación + iniciar turno + dónde está el iconito."""
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except Exception:
        ventana_mensaje("Listo", "Mishop Monitor quedó instalado.\nBusca el iconito verde en la bandeja (abajo a la derecha) y elige \"Iniciar turno\".")
        return

    VERDE = "#0E9A5A"; VERDE_OSC = "#0A7C46"; TXT = "#141b22"; GRIS = "#7a828b"
    root = tk.Tk()
    root.title(APP_NAME)
    root.configure(bg="#ffffff")
    root.resizable(False, False)
    ancho, alto = 468, 560
    sx, sy = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("%dx%d+%d+%d" % (ancho, alto, (sx - ancho) // 2, (sy - alto) // 3))
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    f_tit = tkfont.Font(family="Segoe UI", size=17, weight="bold")
    f_txt = tkfont.Font(family="Segoe UI", size=10)
    f_sub = tkfont.Font(family="Segoe UI", size=10)
    f_bold = tkfont.Font(family="Segoe UI", size=10, weight="bold")
    f_btn = tkfont.Font(family="Segoe UI", size=13, weight="bold")
    f_min = tkfont.Font(family="Segoe UI", size=9)

    # Check verde
    ck = tk.Canvas(root, width=76, height=76, bg="#ffffff", highlightthickness=0)
    ck.pack(pady=(30, 10))
    ck.create_oval(6, 6, 70, 70, fill=VERDE, outline="")
    ck.create_line(24, 40, 34, 50, 53, 28, fill="#ffffff", width=7, capstyle="round", joinstyle="round")

    tk.Label(root, text="¡Listo! Ya quedaste instalado", font=f_tit, bg="#ffffff", fg=TXT).pack()

    quien = (persona or "").strip()
    donde = (empresa or "").strip()
    fila_sub = tk.Frame(root, bg="#ffffff"); fila_sub.pack(pady=(5, 18))
    tk.Label(fila_sub, text="Vinculado a ", font=f_sub, bg="#ffffff", fg=GRIS).pack(side="left")
    tk.Label(fila_sub, text=(quien or "tu cuenta"), font=f_bold, bg="#ffffff", fg=VERDE_OSC).pack(side="left")
    if donde:
        tk.Label(fila_sub, text="  ·  " + donde, font=f_sub, bg="#ffffff", fg=GRIS).pack(side="left")

    estado_lbl = {"nota": None, "btn": None}

    def _iniciar(_=None):
        try:
            pausa_terminar()
        except Exception:
            pass
        try:
            set_estado(Estado.ACTIVO)
        except Exception:
            pass
        b = estado_lbl["btn"]
        if b is not None:
            b.config(text="✓  Tu turno está activo", bg="#e7f5ee", fg=VERDE_OSC,
                     activebackground="#e7f5ee", state="disabled", disabledforeground=VERDE_OSC, cursor="")
        if estado_lbl["nota"] is not None:
            estado_lbl["nota"].config(text="Ya estás trabajando. Puedes cerrar esta ventana.")

    btn = tk.Button(root, text="Iniciar mi turno ahora", font=f_btn, bg=VERDE, fg="#ffffff",
                    activebackground=VERDE_OSC, activeforeground="#ffffff", relief="flat",
                    cursor="hand2", command=_iniciar)
    btn.pack(fill="x", padx=40, ipady=9)
    estado_lbl["btn"] = btn

    nota = tk.Label(root, text="Se abrirá solo cada vez que prendas la computadora.",
                    font=f_min, bg="#ffffff", fg="#9aa0a6")
    nota.pack(pady=(11, 0))
    estado_lbl["nota"] = nota

    tk.Frame(root, bg="#eef1f4", height=1).pack(fill="x", padx=40, pady=(22, 18))

    # Info: dónde está el iconito
    fila = tk.Frame(root, bg="#ffffff"); fila.pack(fill="x", padx=40)
    ic = tk.Canvas(fila, width=40, height=40, bg="#ffffff", highlightthickness=0)
    ic.pack(side="left", anchor="n")
    _dibujar_icono(ic, 2, 2, 36)
    tk.Label(fila, text="Para pausar o terminar tu turno,\nusa el iconito verde junto al reloj:",
             font=f_txt, bg="#ffffff", fg="#5b6570", justify="left").pack(side="left", padx=(12, 0))

    # Ilustración de la bandeja de Windows
    tb = tk.Canvas(root, width=388, height=72, bg="#ffffff", highlightthickness=0)
    tb.pack(pady=(16, 0))
    _rrect(tb, 40, 16, 348, 60, 12, fill="#f3f5f7", outline="#e3e7ea")
    tb.create_text(66, 38, text="⌃", font=tkfont.Font(family="Segoe UI", size=13, weight="bold"), fill="#8a929b")
    _rrect(tb, 92, 30, 108, 46, 4, fill="#c2c8ce", outline="")
    _rrect(tb, 120, 30, 136, 46, 4, fill="#c2c8ce", outline="")
    # icono verde resaltado
    tb.create_oval(150, 22, 186, 58, outline="#17C776", width=3)
    _dibujar_icono(tb, 156, 28, 24)
    tb.create_text(232, 32, text="3:45 p.m.", font=f_min, fill="#4b5560")
    tb.create_text(232, 47, text="14/09/2026", font=tkfont.Font(family="Segoe UI", size=8), fill="#8a929b")
    # flechita que apunta al icono
    tb.create_line(168, 14, 168, 20, fill="#17C776", width=2)

    root.mainloop()


def instancia_unica():
    """Evita dos copias corriendo a la vez (mutex de Windows)."""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, "Local\\MishopMonitorSingleton")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


# ------------------------------------------------------- auto-actualización
def _ver_tupla(v):
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return ()


def _hay_version_nueva(remota, local):
    r, l = _ver_tupla(remota), _ver_tupla(local)
    return bool(r) and r > l


def _descargar(url, destino):
    req = urlrequest.Request(url, headers={"User-Agent": "MishopMonitor/%s" % VERSION})
    with urlrequest.urlopen(req, timeout=90) as resp, open(destino, "wb") as f:
        shutil.copyfileobj(resp, f)


def buscar_e_instalar_actualizacion():
    """Si hay una versión más nueva publicada, la instala sola en segundo plano
    y reabre la app. El trabajador no hace nada y nunca queda atrasado.

    Segura: el token vive en config.json (no se toca al actualizar), y salimos
    del proceso ANTES de instalar para que no haya archivos bloqueados; un .bat
    desatendido corre el instalador silencioso y vuelve a abrir la app."""
    if not es_exe_congelado() or not corre_desde_instalacion():
        return  # en desarrollo o .exe suelto: no auto-actualizar
    try:
        req = urlrequest.Request(CFG.get("version_url", ""),
                                 headers={"User-Agent": "MishopMonitor/%s" % VERSION})
        with urlrequest.urlopen(req, timeout=15) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        remota = info.get("version", "")
        if not _hay_version_nueva(remota, VERSION):
            return
        url_setup = info.get("url") or CFG.get("setup_url", "")
        if not url_setup:
            return
        log("actualización disponible: %s (tengo %s)" % (remota, VERSION))
        setup = os.path.join(tempfile.gettempdir(), "MishopMonitorSetup.exe")
        _descargar(url_setup, setup)
        if not (os.path.isfile(setup) and os.path.getsize(setup) > 500000):
            log("setup descargado inválido; abandono actualización")
            return
        exe = ruta_exe()
        bat = os.path.join(tempfile.gettempdir(), "mishop_update.bat")
        with open(bat, "w", encoding="ascii", errors="ignore") as f:
            f.write(
                "@echo off\r\n"
                "ping 127.0.0.1 -n 5 >nul\r\n"
                '"%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\r\n' % setup +
                "ping 127.0.0.1 -n 3 >nul\r\n"
                'start "" "%s"\r\n' % exe +
                'del "%s"\r\n' % setup +
                'del "%%~f0"\r\n'
            )
        DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED|NEW_GROUP|NO_WINDOW
        subprocess.Popen(["cmd", "/c", bat], creationflags=DETACHED, close_fds=True)
        log("instalando actualización %s y reabriendo; salgo" % remota)
        time.sleep(1.0)
        os._exit(0)
    except Exception as e:
        log("auto-actualización falló: %r" % (e,))


def bucle_actualizaciones():
    time.sleep(45)  # dejar que arranque todo antes de la primera revisión
    while True:
        buscar_e_instalar_actualizacion()
        try:
            horas = max(1, int(CFG.get("update_check_hours", 6)))
        except Exception:
            horas = 6
        time.sleep(horas * 3600)


# ----------------------------------------------------------- monitoreo
CFG = {}
MOTIVOS = ["Refrigerio", "Almuerzo", "Reunión", "Trámite personal", "Otro…"]


class Estado:
    APAGADO = "apagado"; ACTIVO = "activo"; PAUSA = "pausa"


estado = Estado.APAGADO
motivo_pausa = ""
_lock = threading.Lock()

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def segundos_inactivo():
    try:
        lii = LASTINPUTINFO(); lii.cbSize = ctypes.sizeof(lii)
        user32.GetLastInputInfo(ctypes.byref(lii))
        return max(0.0, (kernel32.GetTickCount() - lii.dwTime) / 1000.0)
    except Exception:
        return 0.0


def ventana_activa():
    app_name, titulo = "", ""
    try:
        hwnd = user32.GetForegroundWindow()
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        titulo = buf.value or ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)
        if h:
            buf2 = ctypes.create_unicode_buffer(512); size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf2, ctypes.byref(size)):
                app_name = os.path.basename(buf2.value)
            kernel32.CloseHandle(h)
    except Exception:
        pass
    return app_name, titulo


def _activar_conciencia_dpi():
    """Windows: sin esto, con el escalado de pantalla tipico (125 %/150 %),
    Windows le miente al proceso sobre el tamano real y la captura sale
    RECORTADA a la esquina superior izquierda. Declararse consciente del DPI
    hace que la captura sea la pantalla completa de verdad. En Mac no aplica.
    Pedido por Andy (15-sep-2026): la captura debe ser pantalla completa."""
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def capturar_pantalla_jpeg():
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True).convert("RGB")
        img.thumbnail((1280, 1280))
        buf = io.BytesIO(); img.save(buf, "JPEG", quality=35)
        return buf.getvalue()
    except Exception as e:
        log("captura fallo: %r" % (e,))
        return None


def _post(url, payload):
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json", "apikey": CFG["anon_key"],
            "Authorization": "Bearer " + CFG["anon_key"]})
        with urlrequest.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        log("post fallo %s: %r" % (url.rsplit("/", 1)[-1], e))
        return False


def _post_binario(url, datos):
    """Manda bytes tal cual (sin base64, que infla un 33%).

    v1.9.2: con la cabecera que Python pone por defecto ("Python-urllib/3.x")
    el CRM respondía 403 antes de llegar a la ruta (la ruta nunca contesta
    403), y todas las capturas volvían a guardarse dentro de la base. Ahora se
    presenta como Mishop Monitor, igual que la auto-actualización. Si vuelve a
    fallar, el registro guarda el código y el comienzo de la respuesta."""
    try:
        req = urlrequest.Request(url, data=datos, method="POST", headers={
            "Content-Type": "image/jpeg",
            "User-Agent": "MishopMonitor/%s" % VERSION})
        with urlrequest.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except HTTPError as e:
        cuerpo = ""
        try: cuerpo = e.read(200).decode("utf-8", "replace").replace("\n", " ")
        except Exception: pass
        log("captura a Storage fallo: HTTP %s %s | %s" % (e.code, e.headers.get("cf-ray", ""), cuerpo))
        return False
    except Exception as e:
        log("captura a Storage fallo: %r" % (e,))
        return False


def confirmar_comando(cid): return _post(CFG["confirmar_url"], {"p_device_token": CFG["device_token"], "p_comando_id": cid, "p_estado": estado})
def reportar_estado(): return _post(CFG["reportar_url"], {"p_device_token": CFG["device_token"], "p_estado": estado})
def _reportar_estado_seguro():
    try: reportar_estado()
    except Exception: pass
def enviar_muestras(m): return _post(CFG["ingest_url"], {"p_device_token": CFG["device_token"], "p_samples": m})
def enviar_captura(ts, b):
    """La captura va a Storage por el CRM; si eso falla, se usa el camino viejo."""
    from urllib.parse import quote
    url = "%s?token=%s&at=%s" % (CFG["captura_url"], CFG["device_token"], quote(ts))
    if _post_binario(url, b):
        return True
    return _post(CFG["screenshot_url"], {"p_device_token": CFG["device_token"],
                                         "p_captured_at": ts,
                                         "p_image_b64": base64.b64encode(b).decode("ascii")})
def pausa_iniciar(tipo, motivo): _post(CFG["pausa_iniciar_url"], {"p_device_token": CFG["device_token"], "p_tipo": tipo, "p_motivo": motivo or ""})
def pausa_terminar(): _post(CFG["pausa_terminar_url"], {"p_device_token": CFG["device_token"]})


def leer_settings():
    try:
        data = json.dumps({"p_device_token": CFG["device_token"]}).encode("utf-8")
        req = urlrequest.Request(CFG["settings_url"], data=data, method="POST", headers={
            "Content-Type": "application/json", "apikey": CFG["anon_key"], "Authorization": "Bearer " + CFG["anon_key"]})
        with urlrequest.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if isinstance(d, dict) and d.get("ok"):
                return d
    except Exception as e:
        log("settings fallo: %r" % (e,))
    return None


def leer_limites():
    """Lee del CRM los límites que el dueño configuró para su empresa:
    - idle_threshold_minutes: a los cuántos minutos sin mover mouse/teclado se marca "inactivo".
    - auto_end_idle_minutes: a los cuántos minutos de inactividad se cierra el turno solo.
    Si falla (sin conexión, versión vieja de la base, etc.), devuelve None y se usan los valores por defecto."""
    try:
        data = json.dumps({"p_device_token": CFG["device_token"]}).encode("utf-8")
        req = urlrequest.Request(CFG["limits_url"], data=data, method="POST", headers={
            "Content-Type": "application/json", "apikey": CFG["anon_key"], "Authorization": "Bearer " + CFG["anon_key"]})
        with urlrequest.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if isinstance(d, dict) and d.get("ok"):
                return d
    except Exception as e:
        log("limites fallo: %r" % (e,))
    return None


def reclamar_equipo():
    """Candado: una sola computadora activa por persona.
    Le dice al CRM 'soy esta PC (machine_id) y estoy trabajando'. El servidor
    responde {'ok':True,'granted':True} si me toca, o {'granted':False} si esa
    persona ya está reportando desde OTRA PC. Devuelve None si no se pudo pedir
    (sin conexión, o base sin la función todavía): en ese caso el que llama asume
    que SÍ le toca, para no romper lo que ya funcionaba."""
    try:
        data = json.dumps({"p_device_token": CFG["device_token"], "p_machine_id": CFG.get("machine_id", "")}).encode("utf-8")
        req = urlrequest.Request(CFG["reclamar_url"], data=data, method="POST", headers={
            "Content-Type": "application/json", "apikey": CFG["anon_key"], "Authorization": "Bearer " + CFG["anon_key"]})
        with urlrequest.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if isinstance(d, dict) and d.get("ok"):
                return d
    except Exception as e:
        log("reclamar fallo: %r" % (e,))
    return None


def notificar_otra_pc():
    """Avisa al trabajador, una vez, que ya está activo en otra computadora."""
    msg = ("Ya estás activo en otra computadora. Este equipo no contará tu "
           "actividad hasta que cierres el monitor en la otra PC.")
    try:
        if icono is not None and hasattr(icono, "notify"):
            icono.notify(msg, APP_NAME)
            return
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox
        def _w():
            try:
                root = tk.Tk(); root.withdraw()
                try: root.attributes("-topmost", True)
                except Exception: pass
                messagebox.showinfo(APP_NAME, msg)
                root.destroy()
            except Exception:
                pass
        threading.Thread(target=_w, daemon=True).start()
    except Exception:
        pass


def _cambio_equipo(tengo):
    """Se llama cuando cambia si esta PC tiene o no el control del monitoreo."""
    global _aviso_otra_pc
    if not tengo:
        log("candado: otra PC está activa; este equipo deja de contar")
        try:
            if icono is not None:
                icono.title = APP_NAME + " · Activo en otra PC"
        except Exception:
            pass
        if not _aviso_otra_pc:
            _aviso_otra_pc = True
            notificar_otra_pc()
    else:
        if _aviso_otra_pc:
            log("candado: este equipo toma el control del monitoreo")
        _aviso_otra_pc = False
        try:
            if icono is not None:
                icono.title = titulo_estado()
        except Exception:
            pass


def aplicar_comando(cmd, motivo=""):
    """Aplica una orden que llegó desde el CRM."""
    cmd = (cmd or "").strip().lower()
    if cmd in ("iniciar", "reanudar", "empezar"):
        try: pausa_terminar()
        except Exception: pass
        set_estado(Estado.ACTIVO)
    elif cmd == "pausar":
        try: pausa_iniciar("pausa", motivo)
        except Exception: pass
        set_estado(Estado.PAUSA, motivo or "Pausa")
    elif cmd in ("terminar", "fin", "fin_turno"):
        try: pausa_iniciar("fin_turno", "")
        except Exception: pass
        set_estado(Estado.APAGADO)
    else:
        return
    log("comando del CRM aplicado: %s %s" % (cmd, motivo))


def bucle_monitoreo():
    global _auto_terminado, _ultimo_comando_id, _tengo_equipo
    pendientes = []; ultimo_flush = time.time(); ultima_captura = 0; ultimo_comando = 0
    shot_enabled = True; shot_interval = CFG["screenshot_interval_minutes"] * 60
    # Estos dos los puede cambiar el dueño desde el CRM; arrancan con los valores por defecto
    # y se refrescan cada rato con leer_limites().
    idle_thr = max(30, int(CFG.get("idle_threshold_seconds", 300)))
    auto_end = max(5, int(CFG.get("auto_end_idle_minutes", 60))) * 60
    ultimo_limites = 0
    host = socket.gethostname()
    while True:
        # --- Chequeo periódico (~25s): candado de 1 PC, límites y órdenes del CRM ---
        if time.time() - ultimo_comando >= 25:
            ultimo_comando = time.time()
            # Candado: ¿me toca contar a MÍ, o esta persona ya está en otra PC?
            rc = reclamar_equipo()
            if rc is not None:
                nuevo = bool(rc.get("granted", True))
                if nuevo != _tengo_equipo:
                    _tengo_equipo = nuevo
                    _cambio_equipo(_tengo_equipo)
            if _tengo_equipo:
                # Límites configurados por el dueño (inactividad / cierre automático)
                if time.time() - ultimo_limites >= max(60, int(CFG.get("limits_check_seconds", 180))):
                    ultimo_limites = time.time()
                    lm = leer_limites()
                    if lm:
                        try: idle_thr = max(30, int(float(lm.get("idle_threshold_minutes", 5)) * 60))
                        except Exception: pass
                        try: auto_end = max(5 * 60, int(float(lm.get("auto_end_idle_minutes", 60)) * 60))
                        except Exception: pass
                # Órdenes desde el CRM (funciona en cualquier estado; casi inmediato)
                s = leer_settings()
                if s:
                    shot_enabled = bool(s.get("screenshots_enabled", True))
                    shot_interval = max(1, int(s.get("screenshot_interval_minutes", 5))) * 60
                    try:
                        cid = int(s.get("comando_id") or 0)
                    except Exception:
                        cid = 0
                    cmd = s.get("comando")
                    if cmd and cid > _ultimo_comando_id:
                        aplicar_comando(cmd, s.get("comando_motivo") or "")
                        _ultimo_comando_id = cid
                        try: confirmar_comando(cid)
                        except Exception: pass
                try: reportar_estado()
                except Exception: pass
        # Si esta persona ya está activa en otra PC, este equipo no cuenta nada.
        if not _tengo_equipo:
            pendientes = []; time.sleep(2); continue
        with _lock:
            activo = (estado == Estado.ACTIVO)
            apagado = (estado == Estado.APAGADO)
        if not activo:
            # Si el turno se cerró solo por inactividad y la persona volvió a la PC,
            # se le vuelve a ofrecer empezar (un clic), sin contar el tiempo que estuvo fuera.
            if apagado and _auto_terminado and segundos_inactivo() < 60:
                _auto_terminado = False
                mostrar_prompt_empezar()
            pendientes = []; time.sleep(2); continue
        idle = segundos_inactivo()
        # Cerrar el turno solo si lleva mucho rato sin actividad (no contar la noche
        # ni cuando dejan la PC prendida). Al volver, se ofrece empezar de nuevo.
        if idle >= auto_end:
            log("fin de turno por inactividad (%d min)" % (auto_end // 60))
            try:
                pausa_iniciar("fin_turno", "inactividad")
            except Exception:
                pass
            set_estado(Estado.APAGADO)
            _auto_terminado = True
            pendientes = []
            continue
        app_name, titulo = ventana_activa()
        pendientes.append({"captured_at": datetime.now(timezone.utc).isoformat(),
            "active_app": app_name, "window_title": titulo, "idle_seconds": round(idle, 1),
            "is_idle": idle >= idle_thr, "hostname": host, "os": "windows"})
        if time.time() - ultimo_flush >= CFG["flush_interval_seconds"] and pendientes:
            if enviar_muestras(pendientes):
                log("enviadas %d muestras" % len(pendientes)); pendientes = []
            ultimo_flush = time.time()
        if shot_enabled and (time.time() - ultima_captura >= shot_interval):
            img = capturar_pantalla_jpeg()
            if img and enviar_captura(datetime.now(timezone.utc).isoformat(), img):
                log("captura enviada")
            ultima_captura = time.time()
        time.sleep(CFG["sample_interval_seconds"])


# ------------------------------------------------------------- bandeja
COLORES = {Estado.ACTIVO: (18, 161, 80), Estado.PAUSA: (224, 160, 32), Estado.APAGADO: (150, 150, 140)}
icono = None
_auto_terminado = False   # el último fin de turno fue por inactividad (para volver a ofrecer empezar)
_prompt_abierto = False   # hay una ventana "Empezar mi turno" abierta ahora
_ultimo_comando_id = 0    # último comando del CRM ya aplicado
_tengo_equipo = True      # candado: esta PC tiene el control del monitoreo (1 por persona)
_aviso_otra_pc = False    # ya se avisó "estás activo en otra PC" (para no repetir)


def ventana_empezar_turno(persona=""):
    """Avisito diario: un botón grande para empezar el turno. Si no le dan, no cuenta nada."""
    global _prompt_abierto
    if _prompt_abierto:
        return
    _prompt_abierto = True
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except Exception:
        _prompt_abierto = False
        return
    VERDE = "#0E9A5A"; VERDE_OSC = "#0A7C46"
    root = tk.Tk()
    root.title(APP_NAME)
    root.configure(bg="#ffffff")
    root.resizable(False, False)
    ancho, alto = 400, 340
    sx, sy = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("%dx%d+%d+%d" % (ancho, alto, (sx - ancho) // 2, (sy - alto) // 3))
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    ic = tk.Canvas(root, width=64, height=64, bg="#ffffff", highlightthickness=0)
    ic.pack(pady=(30, 12))
    _dibujar_icono(ic, 4, 4, 56, color="#15b36a")

    tkfont.Font(family="Segoe UI", size=15, weight="bold")
    tk.Label(root, text="¿Empezamos tu turno?", font=tkfont.Font(family="Segoe UI", size=16, weight="bold"),
             bg="#ffffff", fg="#141b22").pack()
    quien = (persona or "").strip()
    tk.Label(root, text=("Hola %s" % quien) if quien else "Marca el inicio de tu jornada",
             font=tkfont.Font(family="Segoe UI", size=10), bg="#ffffff", fg="#7a828b").pack(pady=(4, 18))

    def _empezar(_=None):
        try:
            pausa_terminar()
        except Exception:
            pass
        set_estado(Estado.ACTIVO)
        root.destroy()

    tk.Button(root, text="Empezar mi turno", font=tkfont.Font(family="Segoe UI", size=13, weight="bold"),
              bg=VERDE, fg="#ffffff", activebackground=VERDE_OSC, activeforeground="#ffffff",
              relief="flat", cursor="hand2", command=_empezar).pack(fill="x", padx=40, ipady=8)
    tk.Label(root, text="Si prendiste la PC para otra cosa, cierra esta ventana:\nno se cuenta nada hasta que empieces.",
             font=tkfont.Font(family="Segoe UI", size=8), bg="#ffffff", fg="#9aa0a6", justify="center").pack(pady=(10, 0))
    tk.Button(root, text="Ahora no", font=tkfont.Font(family="Segoe UI", size=9), bg="#ffffff", fg="#7a828b",
              activebackground="#ffffff", relief="flat", cursor="hand2", command=root.destroy).pack(pady=(6, 0))

    try:
        root.mainloop()
    finally:
        _prompt_abierto = False


def mostrar_prompt_empezar():
    """Abre el avisito de empezar turno en su propio hilo (sin bloquear el monitoreo)."""
    if _prompt_abierto:
        return
    quien = (leer_config_guardada().get("persona") or "").strip()
    threading.Thread(target=ventana_empezar_turno, args=(quien,), daemon=True).start()


def imagen_icono():
    """Icono de la bandeja: cuadrito redondeado del color del estado + pulso blanco.
    Se dibuja en grande y se reduce para que quede con bordes suaves."""
    from PIL import Image, ImageDraw
    with _lock:
        color = COLORES[estado]
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([20, 20, S - 20, S - 20], radius=60, fill=color)
    p = [(0.15, 0.52), (0.35, 0.52), (0.47, 0.32), (0.60, 0.76), (0.70, 0.48), (0.85, 0.48)]
    pts = [(20 + px * (S - 40), 20 + py * (S - 40)) for px, py in p]
    d.line(pts, fill=(255, 255, 255, 255), width=16, joint="curve")
    rcap = 8
    for cx, cy in (pts[0], pts[-1]):
        d.ellipse([cx - rcap, cy - rcap, cx + rcap, cy + rcap], fill=(255, 255, 255, 255))
    return img.resize((64, 64), Image.LANCZOS)


def titulo_estado():
    with _lock:
        if estado == Estado.ACTIVO: return APP_NAME + " · Activo"
        if estado == Estado.PAUSA: return "En pausa · " + motivo_pausa
        return APP_NAME + " · Turno terminado"


def set_estado(nuevo, motivo=""):
    global estado, motivo_pausa
    with _lock:
        estado = nuevo; motivo_pausa = motivo
    log("estado -> %s %s" % (nuevo, motivo))
    # Recordar el estado para poder reanudar tras un reinicio.
    try:
        guardar_config({"ultimo_estado": nuevo, "ultimo_estado_at": datetime.now(timezone.utc).isoformat()})
    except Exception:
        pass
    if nuevo == Estado.ACTIVO:
        global _auto_terminado
        _auto_terminado = False
    if icono is not None:
        try:
            icono.icon = imagen_icono(); icono.title = titulo_estado(); icono.update_menu()
        except Exception as e:
            log("actualizar icono fallo: %r" % (e,))
    # Avisar al CRM al instante (no esperar al próximo chequeo de ~25s), en
    # segundo plano para no trabar el iconito.
    try:
        threading.Thread(target=_reportar_estado_seguro, daemon=True).start()
    except Exception:
        pass


def pedir_motivo_libre():
    """'Otro…': ventanita para escribir el motivo."""
    try:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk(); root.withdraw()
        try: root.attributes("-topmost", True)
        except Exception: pass
        r = simpledialog.askstring(APP_NAME, "¿Motivo de la pausa?", parent=root)
        root.destroy()
        return (r or "").strip()
    except Exception:
        return ""


def accion_iniciar(icon, item): pausa_terminar(); set_estado(Estado.ACTIVO)
def accion_reanudar(icon, item): pausa_terminar(); set_estado(Estado.ACTIVO)


def accion_pausar_motivo(motivo):
    def _h(icon, item):
        m = motivo
        if motivo.startswith("Otro"):
            m = pedir_motivo_libre() or "Otro"
        pausa_iniciar("pausa", m); set_estado(Estado.PAUSA, m)
    return _h


def accion_terminar(icon, item): pausa_iniciar("fin_turno", ""); set_estado(Estado.APAGADO)
def accion_salir(icon, item): icon.stop()


def _es(e):
    with _lock:
        return estado == e


def correr_bandeja():
    global icono
    import pystray
    cfg = leer_config_guardada()
    quien = (cfg.get("persona") or "").strip()
    _submenu_motivos = pystray.Menu(*[pystray.MenuItem(m, accion_pausar_motivo(m)) for m in MOTIVOS])
    MENU = pystray.Menu(
        pystray.MenuItem(lambda item: titulo_estado(), None, enabled=False),
        pystray.MenuItem(lambda item: quien, None, enabled=False, visible=bool(quien)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Pausar…", _submenu_motivos, visible=lambda item: _es(Estado.ACTIVO)),
        pystray.MenuItem("Iniciar turno", accion_iniciar, visible=lambda item: _es(Estado.APAGADO), default=True),
        pystray.MenuItem("Reanudar", accion_reanudar, visible=lambda item: _es(Estado.PAUSA)),
        pystray.MenuItem("Terminar turno", accion_terminar, visible=lambda item: _es(Estado.ACTIVO) or _es(Estado.PAUSA)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Versión " + VERSION, None, enabled=False),
        # "Salir" se quitó a propósito: el trabajador no debe poder apagar el
        # monitoreo por completo de un clic. Puede terminar su turno (sigue vivo
        # el iconito), pero no cerrar la app. La actualización y el desinstalador
        # cierran la app por su cuenta cuando toca.
    )
    icono = pystray.Icon("mishop_monitor", icon=imagen_icono(), title=titulo_estado(), menu=MENU)
    threading.Thread(target=bucle_monitoreo, daemon=True).start()
    icono.run()


def main():
    global CFG
    _activar_conciencia_dpi()
    log("arranque v%s desde %s" % (VERSION, ruta_exe()))
    args = sys.argv[1:]
    mostrar_listo = None

    if "--instalador" in args:
        # Nos abrió el instalador Inno al terminar: leer el bloque de vinculación.
        i = args.index("--instalador")
        ruta = args[i + 1] if i + 1 < len(args) else ""
        cerrar_instancia_instalada()
        datos = vincular_desde_instalador(ruta)
        if not datos:
            return
        mostrar_listo = datos
    elif es_exe_congelado() and not corre_desde_instalacion():
        # Modo antiguo: .exe suelto con bloque pegado → se instala solo y sale.
        instalar_desde_descarga()
        return

    if not instancia_unica():
        log("ya hay una instancia; salgo")
        return

    if mostrar_listo is not None:
        threading.Thread(target=ventana_listo, args=(mostrar_listo.get("persona", ""), mostrar_listo.get("empresa", "")), daemon=True).start()

    CFG = armar_config()
    if not CFG.get("device_token"):
        ventana_mensaje("Falta vincular",
                        "Mishop Monitor no está vinculado a tu cuenta.\n\n"
                        "Descárgalo otra vez desde tu CRM (\"Mi rendimiento\" →\n"
                        "\"Instalar Mishop Monitor en esta PC\") y ábrelo.")
        return

    # Arranque diario (no venimos del instalador): reanudar si estaba Activo hace poco
    # (reinicio en pleno trabajo) o, si no, ofrecer "Empezar mi turno" con un clic.
    if mostrar_listo is None:
        global estado
        cfg = leer_config_guardada()
        reanudar = False
        if cfg.get("ultimo_estado") == Estado.ACTIVO and cfg.get("ultimo_estado_at"):
            try:
                dt = datetime.fromisoformat(cfg["ultimo_estado_at"])
                ventana = CFG.get("resume_window_minutes", 30) * 60
                if (datetime.now(timezone.utc) - dt).total_seconds() < ventana:
                    reanudar = True
            except Exception:
                pass
        if reanudar:
            estado = Estado.ACTIVO
            log("reanudando turno activo tras reinicio")
        else:
            threading.Thread(target=ventana_empezar_turno,
                             args=((cfg.get("persona") or "").strip(),), daemon=True).start()

    # Se mantiene al día sola: revisa si hay versión nueva y se actualiza en
    # segundo plano, sin que el trabajador tenga que reinstalar nada.
    threading.Thread(target=bucle_actualizaciones, daemon=True).start()

    correr_bandeja()


if __name__ == "__main__":
    main()
