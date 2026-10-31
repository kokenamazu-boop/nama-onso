# Build Nama Onso as a one-folder Windows app (PyInstaller).
# Usage: powershell -ExecutionPolicy Bypass -File tools\build.ps1
# Output: _releases\nama-onso\NamaOnso\NamaOnso.exe

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$src = $repo
$dist = Join-Path $repo '_releases\nama-onso'
$work = Join-Path $repo 'build\nama-onso'
$app = Join-Path $dist 'NamaOnso'

Write-Host "Python:" (python -c "import sys; print(sys.version.split()[0])")
python -m PyInstaller --version | Out-Host

if (Test-Path -LiteralPath $app) { Remove-Item -LiteralPath $app -Recurse -Force }
New-Item -ItemType Directory -Path $dist -Force | Out-Null
New-Item -ItemType Directory -Path $work -Force | Out-Null

$buildArgs = @(
    '--noconfirm', '--clean', '--windowed', '--onedir',
    '--name', 'NamaOnso',
    '--icon', (Join-Path $src 'assets\NamaOnso.ico'),
    '--add-data', ((Join-Path $src 'assets') + ';assets'),
    '--distpath', $dist,
    '--workpath', (Join-Path $work 'work'),
    '--specpath', $work,
    '--paths', $src,
    '--collect-all', 'tkinterdnd2',
    (Join-Path $src 'gui.py')
)
python -m PyInstaller @buildArgs
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

$models = Join-Path $repo 'models'
if (Test-Path -LiteralPath $models) {
    Copy-Item -LiteralPath $models -Destination (Join-Path $app 'models') -Recurse
    Write-Host 'Copied FCPE models next to the exe.'
} else {
    Write-Host 'No models folder: the app will fall back to YIN pitch estimation.'
}

$exe = Join-Path $app 'NamaOnso.exe'
$readme = Join-Path $src 'DISTRIBUTION.md'
if (Test-Path -LiteralPath $readme) {
    Copy-Item -LiteralPath $readme -Destination (Join-Path $app 'README.md') -Force
    Copy-Item -LiteralPath $readme -Destination (Join-Path $dist 'README.md') -Force
    Write-Host 'Copied DISTRIBUTION.md as README.md.'
}

Write-Host "Built:" $exe
Get-Item -LiteralPath $exe | Select-Object Name, Length | Format-List | Out-Host

# Headless smoke test: the check writes %TEMP%\nama_jinriki_prep_check.txt.
$check = Join-Path $env:TEMP 'nama_jinriki_prep_check.txt'
if (Test-Path -LiteralPath $check) { Remove-Item -LiteralPath $check -Force }
$process = Start-Process -FilePath $exe -ArgumentList '--check' -PassThru -Wait
Write-Host "check exit:" $process.ExitCode
if (Test-Path -LiteralPath $check) { Get-Content -LiteralPath $check | Out-Host }
if ($process.ExitCode -ne 0) { throw 'The packaged app failed its --check run.' }
Write-Host 'OK: NamaOnso'
