# -*- coding: utf-8 -*-
"""
Mishop Monitor — app de bandeja del sistema para Windows.

Se descarga desde el CRM ya vinculada a la persona (el CRM le pega al final
del .exe un bloque con su token). Al abrirla por primera vez se instala sola:
se copia a %LOCALAPPDATA%\\MishopMonitor, guarda el token en
%APPDATA%\\MishopMonitor\\config.json, se registra para arrancar con Windows
y muestra una ventanita de "Listo". Desde ahí vive como iconito en la bandeja
con tres estados: Activo (verde) / En pausa (ámbar) / Turno terminado (gris).
"""
import os, io, sys, json, time, base64, socket, shutil, threading, ctypes, subprocess
from ctypes import wintypes
from datetime import datetime, timezone
from urllib import request as urlrequest

APP_NAME = "Mishop Monitor"
EXE_NAME = "Mishop Monitor.exe"
VERSION = "1.0.0"

CONFIG_BASE = {
    "supabase_url": "https://dxokmvqqjfbxgqlhcire.supabase.co",
    "anon_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR4b2ttdnFxamZieGdxbGhjaXJlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk2NjA0NjUsImV4cCI6MjA5NTIzNjQ2NX0.CPgX8-gk6lErosTie8V8rS2Uxf0lSOVf_GJRyvz4BZM",
    "device_token": "",
    "sample_interval_seconds": 15, "flush_interval_seconds": 60,
    "idle_threshold_seconds": 300, "screenshot_interval_minutes": 5,
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


def leer_trailer():
    """Bloque de configuración que el CRM pega al final del .exe descargado."""
    if not es_exe_congelado():
        return None
    try:
        with open(ruta_exe(), "rb") as f:
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
    base = cfg["supabase_url"].rstrip("/") + "/rest/v1/rpc/"
    for k, fn in (("ingest_url", "ingest_activity"), ("settings_url", "get_monitor_settings"),
                  ("screenshot_url", "ingest_screenshot"), ("pausa_iniciar_url", "pausa_iniciar"),
                  ("pausa_terminar_url", "pausa_terminar")):
        cfg[k] = base + fn
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
        subprocess.run(["taskkill", "/F", "/IM", EXE_NAME], capture_output=True,
                       creationflags=0x08000000)
        time.sleep(1.0)
    except Exception:
        pass


def instalar_desde_descarga():
    """Primera apertura del .exe descargado del CRM: copia, config, arranque automático."""
    trailer = leer_trailer() or {}
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


def ventana_listo(persona, empresa):
    """Pantalla final del instalador: 'Listo' con flecha al iconito."""
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except Exception:
        ventana_mensaje("Listo", "Mishop Monitor quedó instalado.\nBusca el iconito en la bandeja (abajo a la derecha) y elige \"Iniciar turno\".")
        return
    root = tk.Tk()
    root.title(APP_NAME)
    root.configure(bg="#ffffff")
    root.resizable(False, False)
    ancho, alto = 460, 330
    sx, sy = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("%dx%d+%d+%d" % (ancho, alto, (sx - ancho) // 2, (sy - alto) // 2))
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    lienzo = tk.Canvas(root, width=72, height=72, bg="#ffffff", highlightthickness=0)
    lienzo.pack(pady=(28, 8))
    lienzo.create_oval(4, 4, 68, 68, fill="#12a150", outline="")
    lienzo.create_line(22, 38, 32, 48, 52, 26, fill="#ffffff", width=6, capstyle="round", joinstyle="round")

    f_titulo = tkfont.Font(family="Segoe UI", size=16, weight="bold")
    f_texto = tkfont.Font(family="Segoe UI", size=10)
    tk.Label(root, text="¡Listo! Mishop Monitor quedó instalado", font=f_titulo, bg="#ffffff", fg="#111827").pack()
    quien = (persona or "").strip()
    donde = (empresa or "").strip()
    sub = "Vinculado a %s" % quien if quien else "Vinculado a tu cuenta"
    if donde:
        sub += " · " + donde
    tk.Label(root, text=sub, font=f_texto, bg="#ffffff", fg="#6b7280").pack(pady=(4, 14))
    tk.Label(root, text="Busca el iconito gris en la bandeja del sistema (abajo a la derecha,\n"
                        "junto al reloj; si no lo ves, toca la flechita ^).\n"
                        "Haz clic derecho en él y elige \"Iniciar turno\" para empezar.\n\n"
                        "Se abrirá solo cada vez que prendas la computadora.",
             font=f_texto, bg="#ffffff", fg="#374151", justify="center").pack()
    tk.Button(root, text="Entendido", command=root.destroy, font=f_texto, bg="#12a150", fg="#ffffff",
              activebackground="#0e8a44", activeforeground="#ffffff", relief="flat", padx=22, pady=6,
              cursor="hand2").pack(pady=(18, 0))
    root.mainloop()


def instancia_unica():
    """Evita dos copias corriendo a la vez (mutex de Windows)."""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, "Local\\MishopMonitorSingleton")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


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


def enviar_muestras(m): return _post(CFG["ingest_url"], {"p_device_token": CFG["device_token"], "p_samples": m})
def enviar_captura(ts, b): return _post(CFG["screenshot_url"], {"p_device_token": CFG["device_token"], "p_captured_at": ts, "p_image_b64": base64.b64encode(b).decode("ascii")})
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


def bucle_monitoreo():
    pendientes = []; ultimo_flush = time.time(); ultima_captura = 0; ultimo_settings = 0
    shot_enabled = True; shot_interval = CFG["screenshot_interval_minutes"] * 60
    host = socket.gethostname()
    while True:
        with _lock:
            activo = (estado == Estado.ACTIVO)
        if not activo:
            pendientes = []; time.sleep(2); continue
        if time.time() - ultimo_settings >= 300:
            s = leer_settings()
            if s:
                shot_enabled = bool(s.get("screenshots_enabled", True))
                shot_interval = max(1, int(s.get("screenshot_interval_minutes", 5))) * 60
            ultimo_settings = time.time()
        app_name, titulo = ventana_activa(); idle = segundos_inactivo()
        pendientes.append({"captured_at": datetime.now(timezone.utc).isoformat(),
            "active_app": app_name, "window_title": titulo, "idle_seconds": round(idle, 1),
            "is_idle": idle >= CFG["idle_threshold_seconds"], "hostname": host, "os": "windows"})
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


def imagen_icono():
    from PIL import Image, ImageDraw
    with _lock:
        color = COLORES[estado]; apag = (estado == Estado.APAGADO)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    if apag: d.rounded_rectangle([8, 8, 56, 56], radius=14, outline=color, width=5)
    else: d.rounded_rectangle([8, 8, 56, 56], radius=14, fill=color)
    d.line([(16, 34), (25, 34), (30, 46), (38, 20), (43, 34), (50, 34)], fill=color if apag else (255, 255, 255), width=5, joint="curve")
    return img


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
    icono.icon = imagen_icono(); icono.title = titulo_estado(); icono.update_menu()


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
        pystray.MenuItem("Salir", accion_salir),
    )
    icono = pystray.Icon("mishop_monitor", icon=imagen_icono(), title=titulo_estado(), menu=MENU)
    threading.Thread(target=bucle_monitoreo, daemon=True).start()
    icono.run()


def main():
    global CFG
    log("arranque v%s desde %s" % (VERSION, ruta_exe()))
    corriendo_instalado = os.path.normcase(ruta_exe()) == os.path.normcase(INSTALLED_EXE)

    if es_exe_congelado() and not corriendo_instalado:
        # Abierto desde Descargas: instalar y salir. La copia instalada sigue sola.
        instalar_desde_descarga()
        return

    if not instancia_unica():
        log("ya hay una instancia; salgo")
        return

    CFG = armar_config()
    if not CFG.get("device_token"):
        ventana_mensaje("Falta vincular",
                        "Mishop Monitor no está vinculado a tu cuenta.\n\n"
                        "Descárgalo otra vez desde tu CRM (\"Mi rendimiento\" →\n"
                        "\"Instalar Mishop Monitor en esta PC\") y ábrelo.")
        return
    correr_bandeja()


if __name__ == "__main__":
    main()
