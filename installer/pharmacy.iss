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
; Windows 10 or newer, 64-bit only: the embedded Python is amd64 (windows-installer.yml).
; x64compatible = x64 Windows, and Windows 11 on Arm through x64 emulation (Inno Setup 6.3+).
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
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

[Code]
{ The program runs as two windowless pythonw.exe processes (launcher + server), which the Restart
  Manager cannot ask to close (trial 2026-10-29, #38). Setup stops them itself: only Python processes
  whose executable is inside this program's own folder, never anything else on the PC. Data is safe:
  every sale/stock write is one SQLite transaction, so an interrupted request leaves nothing half done. }

function ProgramPythonDir(): String;
begin
  Result := Lowercase(AddBackslash(ExpandConstant('{app}')) + 'python\');
end;

{ Counts (DryRun) or ends the program's own Python processes; returns how many were found. }
function StopProgram(DryRun: Boolean): Integer;
var
  Locator, Service, Procs, P: Variant;
  I, Pid, Code: Integer;
  Exe: String;
begin
  Result := 0;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Procs := Service.ExecQuery('SELECT ProcessId, ExecutablePath FROM Win32_Process' +
                               ' WHERE Name=''python.exe'' OR Name=''pythonw.exe''');
    for I := 0 to Procs.Count - 1 do
    begin
      try
        P := Procs.ItemIndex(I);
        Exe := P.ExecutablePath;          { Null (another user's process) raises: skipped }
        if Pos(ProgramPythonDir(), Lowercase(Exe)) = 1 then
        begin
          Result := Result + 1;
          if not DryRun then
          begin
            Pid := P.ProcessId;
            Log('Stopping ' + Exe + ' pid ' + IntToStr(Pid));
            Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /PID ' + IntToStr(Pid), '', SW_HIDE,
                 ewWaitUntilTerminated, Code);
          end;
        end;
      except
      end;
    end;
  except
    Log('Process scan failed: ' + GetExceptionMessage);
  end;
end;

{ Something still answers where the program listens (an old start.bat server on system Python). }
function ServerAnswers(): Boolean;
var
  Http: Variant;
begin
  Result := False;
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.SetTimeouts(1000, 1000, 1000, 1000);
    Http.Open('GET', 'http://127.0.0.1:8000/api/health', False);
    Http.Send('');
    Result := Http.Status = 200;
  except
    Result := False;
  end;
end;

function StopAndWait(): Boolean;
var
  Tries: Integer;
begin
  StopProgram(False);
  Tries := 0;
  while ((StopProgram(True) > 0) or ServerAnswers()) and (Tries < 10) do
  begin
    Sleep(1000);
    Tries := Tries + 1;
  end;
  Result := (StopProgram(True) = 0) and not ServerAnswers();
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if StopProgram(True) > 0 then
  begin
    if not WizardSilent() and (MsgBox('منصة الصيدلية شغالة دلوقتي، والمثبّت هيقفلها عشان يكمّل.' + #13#10 +
        'لو فيه فاتورة مفتوحة على الشاشة، كمّلها الأول. بياناتك مش هتتأثر.' + #13#10#13#10 +
        'اضغط OK للإغلاق والمتابعة.', mbConfirmation, MB_OKCANCEL) <> IDOK) then
    begin
      Result := 'التثبيت اتلغى والبرنامج لسه شغال. شغّل المثبّت تاني لما تكون جاهز.';
      exit;
    end;
  end;
  if not StopAndWait() then
    Result := 'فيه نسخة تانية من البرنامج لسه شغالة على نفس الجهاز (غالبًا start.bat القديم في شباك أسود).' + #13#10 +
              'اقفلها (أو من Task Manager اقفل Python) وبعدين شغّل المثبّت تاني.';
end;

{ Only after the owner confirmed removal: InitializeUninstall runs before that question. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopProgram(False);
    Sleep(1500);
  end;
end;
