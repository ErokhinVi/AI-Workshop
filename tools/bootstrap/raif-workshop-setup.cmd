@echo off
REM raif-workshop-setup.cmd
REM How to run:
REM   1. Double-click the file. A black window will open.
REM   2. If SmartScreen warns, click "More info" then "Run anyway".
REM   3. Pick yourself in the dialog.
REM Requirements: Git for Windows + OpenSSH Client (built into Win10/11).

setlocal EnableExtensions
chcp 65001 >nul 2>&1

echo.
echo === Raif AI-Workshop setup ===
echo.

REM Locate PowerShell
where powershell >nul 2>&1
if errorlevel 1 (
  echo [ERROR] powershell.exe not found on PATH.
  pause
  exit /b 1
)

set "TMPPS=%TEMP%\raif-workshop-setup-%RANDOM%%RANDOM%.ps1"
echo Extracting PowerShell payload to "%TMPPS%"...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$src=[IO.File]::ReadAllText('%~f0',[Text.UTF8Encoding]::new($false)); $m=[char]35+'__PS'+'_BEGIN__'; $i=$src.LastIndexOf($m); if($i -lt 0){ Write-Host 'marker not found'; exit 2 }; [IO.File]::WriteAllText('%TMPPS%', $src.Substring($i+$m.Length), [Text.UTF8Encoding]::new($true))"

if errorlevel 1 (
  echo.
  echo [ERROR] Could not unpack the PowerShell payload. Code: %errorlevel%
  pause
  exit /b 1
)

echo Running setup...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%TMPPS%"
set "RC=%ERRORLEVEL%"
del /q "%TMPPS%" 2>nul

echo.
if not "%RC%"=="0" (
  echo [ERROR] Setup exited with code %RC%. Read the message above.
) else (
  echo [OK] Done.
)
echo.
pause
exit /b %RC%

#__PS_BEGIN__
# ──────────────────────────────────────────────────────────────────────────────
# PowerShell-часть. Запускается trampoline-ом выше как обычный .ps1 в temp.
# ──────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding         = [System.Text.UTF8Encoding]::new()

# ── параметры ────────────────────────────────────────────────────────────────
$RepoUrl          = 'git@github.com:ErokhinVi/AI-Workshop.git'
$RepoDir          = Join-Path $env:USERPROFILE 'AI-Workshop'
$SshDir           = Join-Path $env:USERPROFILE '.ssh'
$SshKeyPath       = Join-Path $SshDir   'raif_workshop'
$SshConfig        = Join-Path $SshDir   'config'
$SshConfigMarker  = '# raif-workshop-2026'

# ── helpers ──────────────────────────────────────────────────────────────────
function Say($m) { Write-Host ''; Write-Host ("→ " + $m) -ForegroundColor Cyan }
function Ok ($m) { Write-Host ("  ✓ " + $m) -ForegroundColor Green }
function Warn($m){ Write-Host ("  ! " + $m) -ForegroundColor Yellow }
function Die ($m){ Write-Host ''; Write-Host ("✗ " + $m) -ForegroundColor Red; exit 1 }

function Require-Command($name, $hint) {
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  if (-not $cmd) { Die ("$name не найден. $hint") }
}

function Write-FileNoBom($path, $text) {
  $enc = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($path, $text, $enc)
}

function Lock-FileToCurrentUser($path) {
  # убираем наследование, оставляем доступ только текущему пользователю
  & icacls $path /inheritance:r           | Out-Null
  & icacls $path /grant:r "$($env:USERNAME):F" | Out-Null
  & icacls $path /remove "BUILTIN\Users"      2>&1 | Out-Null
  & icacls $path /remove "NT AUTHORITY\Authenticated Users" 2>&1 | Out-Null
}

function Test-PathInsideSync($p) {
  # true если путь оказался внутри облачной синхронизации.
  # OneDrive/iCloud/Dropbox/Google Drive держат файлы открытыми и ломают
  # virtiofs-mount у Claude в Cowork (sandbox не может unlink .git/*.lock).
  $candidates = @()
  foreach ($n in @('OneDrive','OneDriveCommercial','OneDriveConsumer')) {
    $v = [Environment]::GetEnvironmentVariable($n)
    if ($v) { $candidates += $v }
  }
  $home = $env:USERPROFILE
  foreach ($d in @('OneDrive','OneDrive - Raiffeisenbank','iCloudDrive','Dropbox','Google Drive','GoogleDrive','Yandex.Disk','Яндекс.Диск')) {
    $candidates += (Join-Path $home $d)
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
  # Сносим всё, что осталось от прерванных git-операций. Запускаем ИЗ Windows,
  # поэтому unlink проходит даже на тех файлах, которые залипают для sandbox.
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
      try { Remove-Item -LiteralPath $f -Force -ErrorAction Stop; Ok ("Снёс стейл-лок: " + $l) }
      catch { Warn ("Не смог снести " + $l + ' (' + $_.Exception.Message + ')') }
    }
  }
  # locks глубже — ref-локи под refs/heads/
  $refLocks = Get-ChildItem -LiteralPath (Join-Path $gitDir 'refs') -Filter '*.lock' -Recurse -ErrorAction SilentlyContinue
  foreach ($f in $refLocks) {
    try { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop; Ok ("Снёс ref-лок: " + $f.Name) }
    catch { Warn ("Не смог снести " + $f.Name) }
  }
}

function Harden-GitConfig($repoDir) {
  # Делаем git максимально дружелюбным к Linux-sandbox-у Claude.
  # Все настройки ставим repo-local, чтобы не ломать другие проекты участника.
  $kv = @{
    'core.autocrlf'      = 'false'   # никаких CRLF-штормов на первый коммит
    'core.eol'           = 'lf'
    'core.fileMode'      = 'false'   # 0755 vs 0644 между Win и Linux не считаем
    'core.fsmonitor'     = 'false'   # fsmonitor-daemon держит handle на .git
    'core.untrackedCache'= 'false'
    'core.longPaths'     = 'true'
    'gc.auto'            = '0'       # никаких неожиданных gc посреди работы
    'maintenance.auto'   = 'false'
    'feature.manyFiles'  = 'false'
    'pull.rebase'        = 'true'    # default-ный pull = rebase, как в правилах
    'push.autoSetupRemote' = 'true'
  }
  foreach ($k in $kv.Keys) {
    & git -C $repoDir config $k $kv[$k] | Out-Null
  }
  # Отрегистрировать git maintenance scheduler для этого репо
  & git -C $repoDir maintenance unregister 2>$null | Out-Null
  Ok 'git config: autocrlf=false, eol=lf, fsmonitor=off, gc.auto=0, maintenance off'
}

function Add-DefenderExclusion($path) {
  # Defender жрёт .git/objects при каждом git-write — это вторая частая причина
  # залипания .lock. Если PowerShell запущен не под админом, Add-MpPreference
  # упадёт — пробуем тихо, на провал не ругаемся.
  try {
    Add-MpPreference -ExclusionPath $path -ErrorAction Stop
    Ok ("Defender больше не сканирует " + $path)
  } catch {
    Warn 'Не смог добавить Defender-исключение (нет админ-прав). Если git в sandbox начнёт залипать — запусти cmd "От имени администратора" и проведи setup ещё раз.'
  }
}

function Install-UnstickShortcut($repoDir) {
  # Кладём на рабочий стол shortcut на tools/unstick-locks.cmd —
  # участник сможет двойным кликом починить себя, если что-то залипнет.
  $unstickCmd = Join-Path $repoDir 'tools\unstick-locks.cmd'
  if (-not (Test-Path $unstickCmd)) { Warn 'tools/unstick-locks.cmd отсутствует — shortcut не делаю'; return }
  $desktop = [Environment]::GetFolderPath('Desktop')
  $linkPath = Join-Path $desktop 'Раиф-Воркшоп — починить git.lnk'
  try {
    $sh = New-Object -ComObject WScript.Shell
    $sc = $sh.CreateShortcut($linkPath)
    $sc.TargetPath = $unstickCmd
    $sc.WorkingDirectory = $repoDir
    $sc.IconLocation = 'shell32.dll,238'   # «инструменты»
    $sc.Description = 'Сносит .git/*.lock — если Claude говорит «не могу сохранить»'
    $sc.Save()
    Ok ("Shortcut на аварийку: " + $linkPath)
  } catch {
    Warn 'Не смог создать ярлык аварийки на рабочем столе — не критично.'
  }
}

# ── 0. sanity ────────────────────────────────────────────────────────────────
Require-Command git 'Установи Git for Windows: https://git-scm.com/download/win'
Require-Command ssh 'OpenSSH Client отсутствует. Settings → Apps → Optional Features → Add → OpenSSH Client.'

# Проверяем, что папка проекта НЕ в облачной синхронизации.
# OneDrive/iCloud/Dropbox/Yandex.Disk держат файлы открытыми и virtiofs-mount
# Claude в Cowork не сможет удалять .git/*.lock. Это первая причина, по которой
# участник может застрять и не суметь сохранить работу.
if (Test-PathInsideSync $RepoDir) {
  Write-Host ''
  Write-Host '✗ Папка для воркшопа оказалась внутри облачной синхронизации:' -ForegroundColor Red
  Write-Host ("   " + $RepoDir) -ForegroundColor Red
  Write-Host ''
  Write-Host 'OneDrive/iCloud/Dropbox держат файлы открытыми и Claude в Cowork mode'
  Write-Host 'не сможет нормально работать с git внутри этой папки.'
  Write-Host ''
  Write-Host 'Что сделать:' -ForegroundColor Yellow
  Write-Host '  1. Закрой это окно.'
  Write-Host '  2. В переменных окружения смени USERPROFILE на не-OneDrive путь,'
  Write-Host '     ИЛИ сделай сейчас разово:'
  Write-Host ''
  Write-Host ('     set "USERPROFILE=C:\Users\' + $env:USERNAME + '"') -ForegroundColor Cyan
  Write-Host '     raif-workshop-setup.cmd'
  Write-Host ''
  Write-Host '  Если в C:\Users\<ты> тоже OneDrive (корпоративная политика) —'
  Write-Host '  позови ведущего: подберём папку вручную (например C:\raif-workshop).'
  Die 'Прерываюсь, чтобы не превратить воркшоп в боль.'
}

# ── 1. меню выбора участника (WinForms) ──────────────────────────────────────
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Show-MemberPicker {
  $form = New-Object Windows.Forms.Form
  $form.Text            = 'Райф AI-воркшоп — настройка ноутбука'
  $form.Size            = New-Object Drawing.Size(520, 400)
  $form.StartPosition   = 'CenterScreen'
  $form.FormBorderStyle = 'FixedDialog'
  $form.MaximizeBox     = $false
  $form.MinimizeBox     = $false
  $form.Font            = New-Object Drawing.Font('Segoe UI', 10)

  $label = New-Object Windows.Forms.Label
  $label.Text     = 'Кто ты? Это нужно чтобы коммиты были подписаны твоим именем.'
  $label.Location = New-Object Drawing.Point(18, 15)
  $label.Size    = New-Object Drawing.Size(470, 35)
  $form.Controls.Add($label)

  $listBox = New-Object Windows.Forms.ListBox
  $listBox.Location = New-Object Drawing.Point(18, 55)
  $listBox.Size     = New-Object Drawing.Size(470, 240)
  $listBox.Font     = New-Object Drawing.Font('Consolas', 10)
  [void]$listBox.Items.AddRange(@(
    '1) Сергей Монин          (CEO Office)',
    '2) Никита Патрахин       (CIB)',
    '3) Иван Курочкин         (Розница)',
    '4) Александр Ложечкин    (IT / Платформа)',
    '5) Герт Хебенштрайт      (Финансы и Опс)',
    '6) Роланд Васс           (Управление рисками)',
    '7) Виталий Ерохин        (ведущий)'
  ))
  $listBox.SelectedIndex = 0
  $form.Controls.Add($listBox)

  $ok = New-Object Windows.Forms.Button
  $ok.Text         = 'Поехали'
  $ok.Location     = New-Object Drawing.Point(280, 310)
  $ok.Size         = New-Object Drawing.Size(95, 32)
  $ok.DialogResult = [Windows.Forms.DialogResult]::OK
  $form.Controls.Add($ok)
  $form.AcceptButton = $ok

  $cancel = New-Object Windows.Forms.Button
  $cancel.Text         = 'Отмена'
  $cancel.Location     = New-Object Drawing.Point(390, 310)
  $cancel.Size         = New-Object Drawing.Size(95, 32)
  $cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel
  $form.Controls.Add($cancel)
  $form.CancelButton = $cancel

  $result = $form.ShowDialog()
  if ($result -ne [Windows.Forms.DialogResult]::OK) { return $null }
  return ($listBox.SelectedIndex + 1)
}

$WhoNum = Show-MemberPicker
if (-not $WhoNum) { Write-Host 'Отменено.'; exit 0 }

# ── 2. mapping ───────────────────────────────────────────────────────────────
$Members = @{
  1 = @{ Name='Sergey Monin';       Email='ceo@raif-workshop.local';     Block='ceo';     Participant='sergey-monin'       }
  2 = @{ Name='Nikita Patrahin';    Email='cib@raif-workshop.local';     Block='cib';     Participant='nikita-patrahin'    }
  3 = @{ Name='Ivan Kurochkin';     Email='retail@raif-workshop.local';  Block='retail';  Participant='ivan-kurochkin'     }
  4 = @{ Name='Aleksandr Lozhechkin'; Email='it@raif-workshop.local';    Block='it';      Participant='aleksandr-lozhechkin' }
  5 = @{ Name='Gert Hebenstreit';   Email='finance@raif-workshop.local'; Block='finance'; Participant='gert-hebenstreit'   }
  6 = @{ Name='Roland Vass';        Email='risk@raif-workshop.local';    Block='risk';    Participant='roland-vass'        }
  7 = @{ Name='Vitaly Erokhin';     Email='vitaly@raif-workshop.local';  Block='host';    Participant='vitaly-erokhin'     }
}
$cfg = $Members[$WhoNum]
if (-not $cfg) { 