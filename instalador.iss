; Instalador de Mishop Monitor (Inno Setup 6).
; Lo arma GitHub Actions (.github/workflows/build.yml) a partir de la carpeta
; dist\MishopMonitor que produce PyInstaller en modo --onedir.
;
; - Se instala por usuario en %LOCALAPPDATA%\Programs\Mishop Monitor: no pide
;   contraseña de administrador (muchos trabajadores no son admin de su PC).
; - Queda en "Aplicaciones instaladas" con desinstalador.
; - Arranca con Windows (clave Run del usuario) y se abre al terminar.
; - El CRM pega al final de este instalador el bloque de vinculación
;   MISHOPCFG1{...}MISHOPEND1; al terminar, la app se abre con
;   --instalador "<ruta del instalador>" y lee el bloque de ahí.

#define MyAppName "Mishop Monitor"
#define MyAppPublisher "Mishop"
#define MyAppExeName "Mishop Monitor.exe"
#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif

[Setup]
AppId={{7C2E0B7A-4D6B-4B7E-9C31-2F0A1E5D80A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=MishopMonitorSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no
SetupIconFile=icono.ico

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
spanish.WelcomeLabel1=Instalar {#MyAppName}
spanish.WelcomeLabel2=Este programa registra tu actividad de trabajo y la muestra en tu CRM, en "Mi rendimiento". Tú lo controlas desde el iconito junto al reloj: Iniciar turno, Pausar, Terminar turno.%n%nSe instala solo en tu carpeta de usuario y se abre cada vez que prendes la computadora.
spanish.FinishedHeadingLabel=¡Listo!
spanish.FinishedLabel={#MyAppName} quedó instalado. Al cerrar esta ventana se abre solo: busca el iconito gris junto al reloj (abajo a la derecha; si no lo ves, toca la flechita ^), haz clic derecho y elige "Iniciar turno".

[Files]
Source: "dist\MishopMonitor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MishopMonitor"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--instalador ""{srcexe}"""; Flags: nowait postinstall skipifsilent; Description: "Abrir {#MyAppName} ahora"

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM ""{#MyAppExeName}"""; Flags: runhidden; RunOnceId: "CerrarMonitor"

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\MishopMonitor"
