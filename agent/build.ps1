#Requires -Version 5.1
<#
.SYNOPSIS
    Build and publish the TrackMe Agent for Windows x64.

.DESCRIPTION
    Compiles the TrackMe Agent as a self-contained, single-file executable
    targeting win-x64. Optionally runs Inno Setup to produce the installer.

.PARAMETER Configuration
    Build configuration (Debug or Release). Default: Release.

.PARAMETER CreateInstaller
    If specified, runs Inno Setup after publishing to create the installer MSI.

.PARAMETER InnoSetupPath
    Path to the Inno Setup compiler (iscc.exe). Default: standard install location.

.PARAMETER Clean
    If specified, cleans the build output before publishing.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -CreateInstaller
    .\build.ps1 -Configuration Debug -Clean
#>

[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",

    [switch]$CreateInstaller,

    [string]$InnoSetupPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",

    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Join-Path $ScriptDir "src\TrackMe.Agent"
$ProjectFile = Join-Path $ProjectDir "TrackMe.Agent.csproj"
$PublishDir = Join-Path $ScriptDir "publish"
$InstallerScript = Join-Path $ScriptDir "installer\trackme.iss"
$OutputDir = Join-Path $ScriptDir "output"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
    Write-Host ""
}

function Assert-DotnetSdk {
    try {
        $version = & dotnet --version 2>&1
        Write-Host "Using .NET SDK: $version"
    }
    catch {
        Write-Error ".NET SDK is not installed or not on PATH. Install from https://dotnet.microsoft.com/download"
        exit 1
    }
}

# Verify prerequisites
Write-Step "Checking prerequisites"
Assert-DotnetSdk

# Clean if requested
if ($Clean) {
    Write-Step "Cleaning previous build output"

    if (Test-Path $PublishDir) {
        Remove-Item -Recurse -Force $PublishDir
        Write-Host "Removed: $PublishDir"
    }

    & dotnet clean $ProjectFile --configuration $Configuration --verbosity minimal
    if ($LASTEXITCODE -ne 0) {
        Write-Error "dotnet clean failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
}

# Restore packages
Write-Step "Restoring NuGet packages"
& dotnet restore $ProjectFile
if ($LASTEXITCODE -ne 0) {
    Write-Error "dotnet restore failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

# Publish
Write-Step "Publishing TrackMe Agent ($Configuration, win-x64, self-contained)"
& dotnet publish $ProjectFile `
    --configuration $Configuration `
    --runtime win-x64 `
    --self-contained true `
    -p:PublishSingleFile=true `
    -p:EnableCompressionInSingleFile=true `
    -p:IncludeNativeLibrariesForSelfExtract=true `
    --output $PublishDir

if ($LASTEXITCODE -ne 0) {
    Write-Error "dotnet publish failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

$exePath = Join-Path $PublishDir "TrackMe.Agent.exe"
if (Test-Path $exePath) {
    $fileInfo = Get-Item $exePath
    $sizeMb = [math]::Round($fileInfo.Length / 1MB, 2)
    Write-Host "Published executable: $exePath ($sizeMb MB)" -ForegroundColor Green
}
else {
    Write-Error "Published executable not found at $exePath"
    exit 1
}

# Optionally create installer
if ($CreateInstaller) {
    Write-Step "Building installer with Inno Setup"

    if (-not (Test-Path $InnoSetupPath)) {
        Write-Error "Inno Setup compiler not found at: $InnoSetupPath"
        Write-Error "Install Inno Setup 6 from https://jrsoftware.org/isdl.php or pass -InnoSetupPath"
        exit 1
    }

    if (-not (Test-Path $InstallerScript)) {
        Write-Error "Inno Setup script not found at: $InstallerScript"
        exit 1
    }

    # Ensure output directory exists
    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir | Out-Null
    }

    & $InnoSetupPath $InstallerScript
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Inno Setup compilation failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }

    $installerFiles = Get-ChildItem -Path $OutputDir -Filter "*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($installerFiles) {
        $installerSizeMb = [math]::Round($installerFiles.Length / 1MB, 2)
        Write-Host "Installer created: $($installerFiles.FullName) ($installerSizeMb MB)" -ForegroundColor Green
    }
}

Write-Step "Build complete"
Write-Host "Publish directory: $PublishDir"
if ($CreateInstaller) {
    Write-Host "Installer output:  $OutputDir"
}
