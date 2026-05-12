# harden.ps1 — пост-клон настройки для воркшопа.
# Вызывается из raif-workshop-setup.cmd одной строкой:
#     & "$RepoDir\tools\bootstrap\harden.ps1" -RepoDir $RepoDir
#
# Задачи:
#   1) Запретить ситуацию, когда папка проекта внутри облачной синхронизации.
#   2) Закрепить git-config для дружелюбия к Linux-sandbox-у Claude.
#   3) Снести стейл .git/*.lock от прошлых упавших операций.
#   4) Добавить Defender-исключение на папку проекта (best-effort).
#   5) Положить на рабочий стол ярлык на tools/unstick-locks.cmd.

param(
  [Parameter(Mandatory=$true)][string]$RepoDir
)

function Say($m) { Write-Host ''; Write-Host ("-> " + $m) -ForegroundColor Cyan }
function Ok ($m) { Write-Host ("  + " + $m) -ForegroundColor Green }
function Warn($m){ Write-Host ("  ! " + $m) -ForegroundColor Yellow }

function Test-PathInsideSync($p) {
  $candidates = @()
  foreach ($n in @('OneDrive','OneDriveCommercial','OneDriveConsumer')) {
    $v = [Environment]::GetEnvironmentVariable($n)
    if ($v) { $candidates += $v }
  }
  $userHome = $env:USERPROFILE
  foreach ($d in @('OneDrive','OneDrive - Raiffeisenbank','iCloudDrive','Dropbox','Google Drive','GoogleDrive','Yandex.Disk')) {
    $candidates += (Join-Path $userHome $d)
  }
  $pAbs = [IO.Path]::GetFullPath($p).TrimEnd('\').ToLowerInvariant()
  foreach ($c in $candidates) {
    if (-not $c) { continue }
    $cAbs = [IO.Path]::GetFullPath($c).TrimEnd('\').ToLowerInvariant()
    if ($pAbs -eq $cAbs -or $pAbs.StartsWith($cAbs + '\')) { return $true }
  }
  return $false
}

function Remove-StaleGitLocks($repoDir) {
  $gitDir = Join-Path $repoDir '.git'
  if (-not (Test-Path $gitDir)) { return }
  $locks = @(
    'HEAD.lock','index.lock','packed-refs.lock','config.lock','REBASE_HEAD.lock',
    'MERGE_HEAD.lock','FETCH_HEAD.lock','ORIG_HEAD.lock','objects\maintenance.lock',
    'shallow.lock','gc.pid.lock'
  )
  foreach ($l in $locks) {
    $f = Join-Path $gitDir $l
    if (Test-Path $f) {
      try { Remove-Item -LiteralPath $f -Force -ErrorAction Stop; Ok ("snyat lock: " + $l) }
      catch { Warn ("ne snyat " + $l) }
    }
  }
  $refLocks = Get-ChildItem -LiteralPath (Join-Path $gitDir 'refs') -Filter '*.lock' -Recurse -ErrorAction SilentlyContinue
  foreach ($f in $refLocks) {
    try { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop; Ok ("snyat ref-lock: " + $f.Name) }
    catch { Warn ("ne snyat ref-lock " + $f.Name) }
  }
}

function Harden-GitConfig($repoDir) {
  $kv = @{
    'core.autocrlf'      = 'false'
    'core.eol'           = 'lf'
    'core.fileMode'      = 'false'
    'core.fsmonitor'     = 'false'
    'core.untrackedCache'= 'false'
    'core.longPaths'     = 'true'
    'gc.auto'            = '0'
    'maintenance.auto'   = 'false'
    'feature.manyFiles'  = 'false'
    'pull.rebase'        = 'true'
    'push.autoSetupRemote' = 'true'
  }
  foreach ($k in $kv.Keys) {
    & git -C $repoDir config $k $kv[$k] | Out-Null
  }
  & git -C $repoDir maintenance unregister 2>$null | Out-Null
  Ok 'git config: autocrlf=false, fsmonitor=off, gc.auto=0, maintenance=off'
}

function Add-DefenderExclusion($path) {
  try {
    Add-MpPreference -ExclusionPath $path -ErrorAction Stop
    Ok ("Defender bolshe ne skaniruet " + $path)
  } catch {
    Warn 'Ne smog dobavit Defender-isklyuchenie (net admin-prav). Ne kriticno.'
  }
}

function Install-UnstickShortcut($repoDir) {
  $unstickCmd = Join-Path $repoDir 'tools\unstick-locks.cmd'
  if (-not (Test-Path $unstickCmd)) { Warn 'tools/unstick-locks.cmd otsutstvuet'; return }
  $desktop = [Environment]::GetFolderPath('Desktop')
  $linkPath = Join-Path $desktop 'Raif-Workshop - pochinit git.lnk'
  try {
    $sh = New-Object -ComObject WScript.Shell
    $sc = $sh.CreateShortcut($linkPath)
    $sc.TargetPath = $unstickCmd
    $sc.WorkingDirectory = $repoDir
    $sc.IconLocation = 'shell32.dll,238'
    $sc.Description = 'Snosit .git/*.lock - esli Claude govorit ne mogu sohranit'
    $sc.Save()
    Ok ("Shortcut: " + $linkPath)
  } catch {
    Warn 'Ne smog sozdat yarlyk na rabochem stole - ne kriticno.'
  }
}

# === main ===
Say 'Proveryayu chto papka proekta NE v oblachnoy sinhronizacii'
if (Test-PathInsideSync $RepoDir) {
  Write-Host ''
  Write-Host ('! Papka ' + $RepoDir + ' okazalas vnutri OneDrive/iCloud/Dropbox.') -ForegroundColor Yellow
  Write-Host '  Eto budet lomat Claude v Cowork mode (sandbox ne smozhet udalit .git/*.lock).' -ForegroundColor Yellow
  Write-Host '  Posovetuy vedushemu pomoch perenesti papku v ne-syncable mesto.' -ForegroundColor Yellow
} else {
  Ok 'Ne v oblake - horosho'
}

Say 'Zakreplyayu git config dlya druzhelubia k sandbox-u Claude'
Harden-GitConfig -repoDir $RepoDir

Say 'Chischu stale-loki v .git/'
Remove-StaleGitLocks -repoDir $RepoDir

Say 'Proshu Defender ne skanirovat papku proekta'
Add-DefenderExclusion -path $RepoDir

Say 'Kladu yarlyk avariyki na rabochiy stol'
Install-UnstickShortcut -repoDir $RepoDir

Write-Host ''
Write-Host '+ harden.ps1 zakonchen' -ForegroundColor Green
