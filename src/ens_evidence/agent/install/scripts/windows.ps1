# ens-evidence agent install: osquery @@VERSION@@ (msi). Ejecutar como Administrador.
# Rutas del paquete oficial: tools/deployment/windows_packaging (msi: servicio osqueryd, osquery.flags).
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$TokenFile = @@TOKEN_FILE@@
$FileName = @@FILENAME@@
$Url = @@URL@@
$ExpectedSha256 = @@SHA256@@
$InstallRoot = Join-Path $env:ProgramFiles 'osquery'
$FlagsFile = Join-Path $InstallRoot 'osquery.flags'
$EnrollSecret = Join-Path $InstallRoot 'enroll_secret'
$SystemSid = '*S-1-5-18'
$AdministratorsSid = '*S-1-5-32-544'

function Restrict-ToSystemAndAdministrators([string] $Path, [string] $Inherit = '') {
  & icacls.exe $Path /inheritance:r /grant:r "${SystemSid}:${Inherit}F" "${AdministratorsSid}:${Inherit}F" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "icacls ha fallado sobre $Path." }
}

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'Ejecuta este script como Administrador.'
}

$TokenFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($TokenFile)
$token = Get-Item -LiteralPath $TokenFile -Force
if ($token.PSIsContainer -or ($token.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
  throw '--token-file debe ser un fichero normal, no un enlace ni un directorio.'
}
if ($token.Length -eq 0) { throw '--token-file está vacío.' }

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$WorkDir = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $WorkDir | Out-Null
try {
  Restrict-ToSystemAndAdministrators $WorkDir '(OI)(CI)'
  $PackageFile = Join-Path $WorkDir $FileName
  Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $PackageFile -MaximumRedirection 5
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackageFile).Hash.ToLowerInvariant()
  if ($actual -ne $ExpectedSha256) {
    throw "El sha256 de $FileName no coincide con el esperado; no se instala."
  }

  $installer = Start-Process -FilePath 'msiexec.exe' -Wait -PassThru -ArgumentList @(
    '/i', ('"' + $PackageFile + '"'), '/qn', '/norestart')
  if ($installer.ExitCode -ne 0) { throw "msiexec ha terminado con el código $($installer.ExitCode)." }

  # El msi arranca el servicio al instalar: parado y deshabilitado hasta tener flags y token.
  Stop-Service -Name osqueryd
  Set-Service -Name osqueryd -StartupType Disabled

  $flags = @'
@@FLAGS@@
'@
  [IO.File]::WriteAllText($FlagsFile, $flags + "`r`n", (New-Object Text.UTF8Encoding($false)))

  if (Test-Path -LiteralPath $EnrollSecret) {
    $existing = Get-Item -LiteralPath $EnrollSecret -Force
    if ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "$EnrollSecret es un enlace." }
  }
  New-Item -ItemType File -Path $EnrollSecret -Force | Out-Null
  Restrict-ToSystemAndAdministrators $EnrollSecret
  $secret = ([IO.File]::ReadAllText($TokenFile)).Trim()
  [IO.File]::WriteAllText($EnrollSecret, $secret, (New-Object Text.ASCIIEncoding))
  if ((Get-Item -LiteralPath $EnrollSecret).Length -eq 0) { throw 'No se pudo copiar el token.' }

  Set-Service -Name osqueryd -StartupType Automatic
  Start-Service -Name osqueryd
  Write-Output "osquery @@VERSION@@ instalado y arrancado. Retira el token de $TokenFile cuando el equipo aparezca en agent hosts."
} finally {
  Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
}
