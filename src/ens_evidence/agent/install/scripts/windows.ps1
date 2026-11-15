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

# El msi de osquery puede arrancar osqueryd al instalar, brevemente y sin los flags ni el token de esta
# instalación (en reinstalación, con los flags previos). Se para y se verifica antes de configurar;
# cualquier fallo aborta (sin silenciar errores) y no se ocultan los de la limpieza.
function Disable-Osqueryd {
  $service = Get-Service -Name osqueryd -ErrorAction Stop
  if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Stopped) {
    Stop-Service -Name osqueryd -Force -ErrorAction Stop
    $service.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped, [TimeSpan]::FromSeconds(30))
  }
  $service.Refresh()
  if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Stopped) {
    throw 'osqueryd no se ha parado; no se configura.'
  }
  Set-Service -Name osqueryd -StartupType Disabled -ErrorAction Stop
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
$Residue = $null
$ResidueLeft = $false
$WorkDir = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $WorkDir | Out-Null
try {
  Set-ExclusiveAcl $WorkDir $true
  # El token se lee del origen antes de crear o vaciar el destino: ningún alias del destino puede vaciarlo antes.
  $TokenCopy = Join-Path $WorkDir 'token'
  $secret = ([IO.File]::ReadAllText($TokenFile)).Trim()
  if ($secret.Length -eq 0) { throw '--token-file no contiene ningún token.' }
  [IO.File]::WriteAllText($TokenCopy, $secret, (New-Object Text.ASCIIEncoding))
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

    # Parado y verificado antes de escribir flags y token.
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
    [IO.File]::WriteAllText($EnrollSecret, [IO.File]::ReadAllText($TokenCopy), (New-Object Text.ASCIIEncoding))
    if ((Get-Item -LiteralPath $EnrollSecret).Length -eq 0) { throw 'No se pudo copiar el token.' }

    Set-Service -Name osqueryd -StartupType Automatic
    Start-Service -Name osqueryd
    Write-Output "osquery @@VERSION@@ instalado y arrancado. Retira el token de $TokenFile cuando el equipo aparezca en agent hosts."
  } catch {
    $failure = $_
    try { Disable-Osqueryd } catch { Write-Error "No se pudo parar y deshabilitar osqueryd: $_" }
    throw $failure
  }
} finally {
  # El temporal guarda una copia del token: si no se borra, se dice dónde y la instalación no acaba en éxito.
  try { Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction Stop }
  catch { $Residue = $_ }
  if ($Residue -or (Test-Path -LiteralPath $WorkDir)) {
    [Console]::Error.WriteLine("ATENCIÓN: no se pudo borrar $WorkDir, que contiene una copia del token. Bórralo a mano. $Residue")
    $ResidueLeft = $true
  }
}
if ($ResidueLeft) { exit 1 }
