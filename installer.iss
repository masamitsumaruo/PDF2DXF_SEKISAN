; ============================================================
;  PDF2DXF_SEKISAN インストーラー定義 (Inno Setup 6)
;  ビルド方法:
;    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;  出力:
;    dist\PDF2DXF_SEKISAN_Setup.exe   ← これを配布 / GitHub Releases に添付
;
;  前提: 先に build.bat を実行して dist\PDF2DXF_SEKISAN.exe を作成しておく。
; ============================================================

#define MyAppName "PDF2DXF 積算"
#define MyAppExeName "PDF2DXF_SEKISAN.exe"
#define MyAppVersion "1.0.6"
; ↓ 発行元・URL は必要に応じて編集してください
#define MyAppPublisher "PDF2DXF_SEKISAN"
#define MyAppURL ""

[Setup]
; AppId はアプリを一意に識別する GUID。アップデート時も同じ値を保つこと（変更不可）。
AppId={{8F3A1C2D-5B6E-4A7F-9C0D-1E2F3A4B5C6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\PDF2DXF_SEKISAN
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 出力先 dist\ は .gitignore 済み（成果物は Releases へ）
OutputDir=dist
OutputBaseFilename=PDF2DXF_SEKISAN_Setup
SetupIconFile=files_dxf\favicon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Program Files へ入れるため管理者権限を要求（標準的な挙動）
PrivilegesRequired=admin
; setup.exe 自身にもバージョン情報（発行元・製品名）を埋め込み、プロパティに正規情報を表示
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} セットアップ

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\PDF2DXF_SEKISAN.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
