; Windows installer: per-user (no admin), embedded Python, desktop + startup shortcuts.
; Built by .github/workflows/windows-installer.yml (Inno Setup 6). Data is NOT in {app}:
; it lives in %LOCALAPPDATA%\PharmacyPlatform, so reinstalling/uninstalling never deletes it.
#define AppVersion GetEnv("APP_VERSION")
#if AppVersion == ""
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6EB0CA6E-A00E-4DCD-A029-D479BC641E8B}
AppName=منصة الصيدلية
AppVersion={#AppVersion}
AppPublisher=Abdelkawy Pharmacy
DefaultDirName={localappdata}\Programs\PharmacyPlatform
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PharmacySetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\program\installer\icon.ico
CloseApplications=yes

[Languages]
; Inno Setup has no official Arabic translation: wizard buttons in English, our texts in Arabic
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "تشغيل البرنامج تلقائيًا مع الجهاز"; GroupDescription: "اختيارات:"

[Files]
Source: "..\build\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion
Source: "..\build\program\*"; DestDir: "{app}\program"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userdesktop}\منصة الصيدلية"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\program\launcher.py"""; WorkingDir: "{app}\program"; IconFilename: "{app}\program\installer\icon.ico"
Name: "{userprograms}\منصة الصيدلية"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\program\launcher.py"""; WorkingDir: "{app}\program"; IconFilename: "{app}\program\installer\icon.ico"
Name: "{userstartup}\منصة الصيدلية"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\program\launcher.py"""; WorkingDir: "{app}\program"; IconFilename: "{app}\program\installer\icon.ico"; Tasks: startup

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\program\launcher.py"""; WorkingDir: "{app}\program"; Description: "افتح البرنامج دلوقتي"; Flags: postinstall nowait skipifsilent
