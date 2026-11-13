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
$SystemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
$AdministratorsSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')

# Reemplaza la DACL entera (sin herencia): solo SYSTEM y Administradores, propietario Administradores.
function Set-ExclusiveAcl([string] $Path, [bool] $IsDirectory) {
  if ($IsDirectory) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
  } else {
    $acl = New-Object Security.AccessControl.FileSecurity
    $inherit = [Security.AccessControl.InheritanceFlags]::None
  }
  $acl.SetAccessRuleProtection($true, $false)
  $acl.SetOwner($AdministratorsSid)
  foreach ($sid in @($SystemSid, $AdministratorsSid)) {
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
      $sid, [Security.AccessControl.FileSystemRights]::FullControl, $inherit,
      [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)))
  }
  Set-Acl -LiteralPath $Path -AclObject $acl
}

# Mismo fichero: ruta resuelta igual o, si ambos existen, enlace duro (fsutil hardlink list, mismo volumen).
# Límite: alias por unión/montaje de directorio que cambie la ruta y no sea enlace duro no se detectan.
function Test-SameFile([string] $A, [string] $B) {
  if ([string]::Equals($A, $B, [StringComparison]::OrdinalIgnoreCase)) { return $true }
  if (-not ((Test-Path -LiteralPath $A) -and (Test-Path -LiteralPath $B))) { return $false }
  if (-not [string]::Equals([IO.Path]::GetPathRoot($A), [IO.Path]::GetPathRoot($B), [StringComparison]::OrdinalIgnoreCase)) { return $false }
  $relative = $B.Substring([IO.Path]::GetPathRoot($B).Length - 1)
  $links = & fsutil.exe hardlink list $A
  if ($LASTEXITCODE -ne 0) { throw "No se pudo comprobar si $A y $B son el mismo fichero." }
  foreach ($link in $links) {
    if ([string]::Equals($link.Trim(), $relative, [StringComparison]::OrdinalIgnoreCase)) { return $true }
  }
  return $false
}

# Servicio parado y deshabilitado (también si no llega a existir).
function Disable-Osqueryd {
  if (Get-Service -Name osqueryd -ErrorAction SilentlyContinue) {
    Stop-Service -Name osqueryd -Force -ErrorAction SilentlyContinue
    Set-Service -Name osqueryd -StartupType Disabled -ErrorAction SilentlyContinue
  }
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
if (Test-SameFile $TokenFile $EnrollSecret) {
  throw "el token debe estar en un fichero distinto de $EnrollSecret."
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$WorkDir = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $WorkDir | Out-Null
try {
  Set-ExclusiveAcl $WorkDir $true
  $PackageFile = Join-Path $WorkDir $FileName
  Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $PackageFile -MaximumRedirection 5
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackageFile).Hash.ToLowerInvariant()
  if ($actual -ne $ExpectedSha256) {
    throw "El sha256 de $FileName no coincide con el esperado; no se instala."
  }

  try {
    $installer = Start-Process -FilePath 'msiexec.exe' -Wait -PassThru -ArgumentList @(
      '/i', ('"' + $PackageFile + '"'), '/qn', '/norestart')
    if ($installer.ExitCode -ne 0) { throw "msiexec ha terminado con el código $($installer.ExitCode)." }

    # El msi arranca el servicio al instalar: parado y deshabilitado hasta tener flags y token.
    Disable-Osqueryd

    $flags = @'
@@FLAGS@@
'@
    [IO.File]::WriteAllText($FlagsFile, $flags + "`r`n", (New-Object Text.UTF8Encoding($false)))

    if (Test-Path -LiteralPath $EnrollSecret) {
      $existing = Get-Item -LiteralPath $EnrollSecret -Force
      if ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "$EnrollSecret es un enlace." }
    }
    New-Item -ItemType File -Path $EnrollSecret -Force | Out-Null
    Set-ExclusiveAcl $EnrollSecret $false
    $secret = ([IO.File]::ReadAllText($TokenFile)).Trim()
    [IO.File]::WriteAllText($EnrollSecret, $secret, (New-Object Text.ASCIIEncoding))
    if ((Get-Item -LiteralPath $EnrollSecret).Length -eq 0) { throw 'No se pudo copiar el token.' }

    Set-Service -Name osqueryd -StartupType Automatic
    Start-Service -Name osqueryd
    Write-Output "osquery @@VERSION@@ instalado y arrancado. Retira el token de $TokenFile cuando el equipo aparezca en agent hosts."
  } catch {
    Disable-Osqueryd
    throw
  }
} finally {
  Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
}
