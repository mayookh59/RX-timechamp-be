; TrackMe Agent - Inno Setup 6 Installer Script
; Builds the Windows installer for the TrackMe desktop agent.
; Requires Inno Setup 6.x (https://jrsoftware.org/isinfo.php)

#define MyAppName "TrackMe Agent"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "TrackMe Inc"
#define MyAppURL "https://trackme.io"
#define MyAppExeName "TrackMe.Agent.exe"
#define MyServiceName "TrackMeAgent"
#define MyServiceDisplayName "TrackMe Agent Service"
#define MyServiceDescription "TrackMe enterprise employee activity tracking agent"

[Setup]
AppId={{B2F3E8A1-7C4D-4E6F-9A2B-1D3E5F7A9B0C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/support
AppUpdatesURL={#MyAppURL}/updates
DefaultDirName={autopf}\TrackMe
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=
OutputDir=..\output
OutputBaseFilename=TrackMe-Agent-Setup-{#MyAppVersion}
SetupIconFile=..\assets\trackme.ico
UninstallDisplayIcon={app}\TrackMe.Agent.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
UninstallRestartComputer=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Published agent binaries (self-contained, single-file)
Source: "..\src\TrackMe.Agent\bin\Release\net8.0\win-x64\publish\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; Create the ProgramData directory for config, database, and logs
Name: "{commonappdata}\TrackMe"; Permissions: admins-full system-full
Name: "{commonappdata}\TrackMe\logs"; Permissions: admins-full system-full
Name: "{commonappdata}\TrackMe\data"; Permissions: admins-full system-full

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Code]
var
  ConsentPage: TOutputMsgWizardPage;

procedure InitializeWizard();
begin
  { Create a custom consent page shown before installation }
  ConsentPage := CreateOutputMsgPage(
    wpLicense,
    'Employee Monitoring Consent',
    'Please review the following information about this software.'
  );
  ConsentPage.RichEditViewer.Lines.Add(
    'TrackMe Agent monitors desktop activity on this workstation ' +
    'as part of your organization''s productivity tracking program.'
  );
  ConsentPage.RichEditViewer.Lines.Add('');
  ConsentPage.RichEditViewer.Lines.Add('The agent collects the following data:');
  ConsentPage.RichEditViewer.Lines.Add('  - Active and idle time tracking');
  ConsentPage.RichEditViewer.Lines.Add('  - Foreground application usage');
  ConsentPage.RichEditViewer.Lines.Add('  - Browser URL visits');
  ConsentPage.RichEditViewer.Lines.Add('  - Periodic screenshots');
  ConsentPage.RichEditViewer.Lines.Add('');
  ConsentPage.RichEditViewer.Lines.Add(
    'All data is encrypted in transit and at rest. ' +
    'By proceeding with the installation, you acknowledge that this ' +
    'software will be installed and active on this machine.'
  );
  ConsentPage.RichEditViewer.Lines.Add('');
  ConsentPage.RichEditViewer.Lines.Add(
    'Contact your IT administrator for questions or to opt out.'
  );
end;

procedure CreateDefaultConfig();
var
  ConfigDir: String;
  ConfigPath: String;
  ConfigJson: TStringList;
begin
  ConfigDir := ExpandConstant('{commonappdata}\TrackMe');
  ConfigPath := ConfigDir + '\config.json';

  { Only create if config does not already exist (preserve existing) }
  if not FileExists(ConfigPath) then
  begin
    ConfigJson := TStringList.Create;
    try
      ConfigJson.Add('{');
      ConfigJson.Add('  "agent_id": "",');
      ConfigJson.Add('  "api_base_url": "https://trackme.company.com/api/v1",');
      ConfigJson.Add('  "api_key": "",');
      ConfigJson.Add('  "idle_threshold_seconds": 60,');
      ConfigJson.Add('  "screenshot_interval_minutes": 5,');
      ConfigJson.Add('  "screenshot_quality": 60,');
      ConfigJson.Add('  "sync_interval_seconds": 60,');
      ConfigJson.Add('  "capture_all_monitors": false,');
      ConfigJson.Add('  "enabled_features": {');
      ConfigJson.Add('    "activity_tracking": true,');
      ConfigJson.Add('    "app_monitoring": true,');
      ConfigJson.Add('    "url_tracking": true,');
      ConfigJson.Add('    "screenshots": true');
      ConfigJson.Add('  },');
      ConfigJson.Add('  "log_level": "Information"');
      ConfigJson.Add('}');
      ConfigJson.SaveToFile(ConfigPath);
      Log('Created default config at: ' + ConfigPath);
    finally
      ConfigJson.Free;
    end;
  end
  else
    Log('Config already exists at: ' + ConfigPath + ' - skipping creation');
end;

function InstallService(): Boolean;
var
  ResultCode: Integer;
  ExePath: String;
begin
  ExePath := ExpandConstant('{app}\{#MyAppExeName}');
  Result := True;

  { Create the Windows service }
  if not Exec(
    ExpandConstant('{sys}\sc.exe'),
    'create ' + '{#MyServiceName}' +
    ' binPath= "' + ExePath + '"' +
    ' start= delayed-auto' +
    ' DisplayName= "{#MyServiceDisplayName}"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  ) then
  begin
    Log('Failed to create service. Exit code: ' + IntToStr(ResultCode));
    Result := False;
    Exit;
  end;

  { Set service description }
  Exec(
    ExpandConstant('{sys}\sc.exe'),
    'description {#MyServiceName} "{#MyServiceDescription}"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );

  { Configure service recovery: restart on first and second failure }
  Exec(
    ExpandConstant('{sys}\sc.exe'),
    'failure {#MyServiceName} reset= 86400 actions= restart/60000/restart/120000//',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );

  { Start the service }
  Exec(
    ExpandConstant('{sys}\sc.exe'),
    'start {#MyServiceName}',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );
end;

procedure StopAndRemoveService();
var
  ResultCode: Integer;
begin
  { Stop the service (ignore errors if not running) }
  Exec(
    ExpandConstant('{sys}\sc.exe'),
    'stop {#MyServiceName}',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );

  { Wait briefly for service to stop }
  Sleep(2000);

  { Delete the service registration }
  Exec(
    ExpandConstant('{sys}\sc.exe'),
    'delete {#MyServiceName}',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    CreateDefaultConfig();
    InstallService();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    StopAndRemoveService();
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{commonappdata}\TrackMe');
    if DirExists(DataDir) then
    begin
      if MsgBox(
        'Do you want to remove all TrackMe data (configuration, logs, and local database)?',
        mbConfirmation,
        MB_YESNO
      ) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
        Log('Removed data directory: ' + DataDir);
      end;
    end;
  end;
end;
