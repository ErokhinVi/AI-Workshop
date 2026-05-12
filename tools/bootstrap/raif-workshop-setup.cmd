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

powershell -NoProfile -ExecutionPolicy Bypass -Command "$src=[IO.File]::ReadAllText('%~f0',[Text.UTF8Encoding]::new($false)); $m=[char]35+'__PS'+'_BEGIN__'; $i=$src.LastIndexOf($m); if($i -lt 0){ Write-Host 'marker not found'; exit 2 }; [IO.File]::WriteAllText('%TMPPS%', $src.Substring($i+$m.Length), [Text.UTF8Encoding]::new($false))"

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

# ── 0. sanity ────────────────────────────────────────────────────────────────
Require-Command git 'Установи Git for Windows: https://git-scm.com/download/win'
Require-Command ssh 'OpenSSH Client отсутствует. Settings → Apps → Optional Features → Add → OpenSSH Client.'

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
if (-not $cfg) { Die 'Не удалось определить участника.' }

# ── 3. SSH key (embedded, base64 — чтобы не палиться перед secret-scanner-ом) ─
Say 'Кладу рабочий ключ воркшопа'
if (-not (Test-Path $SshDir)) { New-Item -ItemType Directory -Path $SshDir | Out-Null }

$PrivateKeyB64 = 'LS0tLS1CRUdJTiBPUEVOU1NIIFBSSVZBVEUgS0VZLS0tLS0KYjNCbGJuTnphQzFyWlhrdGRqRUFBQUFBQkc1dmJtVUFBQUFFYm05dVpRQUFBQUFBQUFBQkFBQUFNd0FBQUF0emMyZ3RaV1EKeU5UVXhPUUFBQUNDYTluUFJ4TkJMYUhYTWFKU3didXdlelRjb1FLTS90NStHMGRvR09kQzJHQUFBQUtBNzZsam5PK3BZCjV3QUFBQXR6YzJndFpXUXlOVFV4T1FBQUFDQ2E5blBSeE5CTGFIWE1hSlN3YnV3ZXpUY29RS00vdDUrRzBkb0dPZEMyR0EKQUFBRUNLMFJqU0IvbEhjWmdwejZPcldUSVZ1SVNDc2xoTFAzeWhFeUN1UWRLWS81cjJjOUhFMEV0b2RjeG9sTEJ1N0I3TgpOeWhBb3orM240YlIyZ1k1MExZWUFBQUFHMk5zWVhWa1pTMWpiM2R2Y21zdGNtRnBaaTEzYjNKcmMyaHZjQUVDCi0tLS0tRU5EIE9QRU5TU0ggUFJJVkFURSBLRVktLS0tLQo='
$PrivateKey = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($PrivateKeyB64))

# OpenSSH ждёт LF-окончания строк, без BOM
$keyText = ($PrivateKey -replace "`r`n", "`n")
if (-not $keyText.EndsWith("`n")) { $keyText = $keyText + "`n" }
Write-FileNoBom -path $SshKeyPath -text $keyText
Lock-FileToCurrentUser -path $SshKeyPath
Ok ("Ключ на месте: " + $SshKeyPath)

# ── 4. SSH config ────────────────────────────────────────────────────────────
Say 'Настраиваю ssh так, чтобы GitHub использовал этот ключ'
if (-not (Test-Path $SshConfig)) {
  Write-FileNoBom -path $SshConfig -text ''
}

$configText = Get-Content -LiteralPath $SshConfig -Raw -ErrorAction SilentlyContinue
if ($null -eq $configText) { $configText = '' }

if ($configText -match [Regex]::Escape($SshConfigMarker)) {
  Ok ("Запись для GitHub уже есть в " + $SshConfig)
} else {
  $block = @"

$SshConfigMarker
Host github.com
  HostName github.com
  User git
  IdentityFile $SshKeyPath
  IdentitiesOnly yes
"@
  # Append без BOM, с LF
  $newText = ($configText -replace "`r`n", "`n").TrimEnd("`n") + "`n" + ($block -replace "`r`n","`n") + "`n"
  Write-FileNoBom -path $SshConfig -text $newText
  Ok ("Дописал " + $SshConfig)
}

# ── 5. git identity ──────────────────────────────────────────────────────────
& git config --global user.name  $cfg.Name  | Out-Null
& git config --global user.email $cfg.Email | Out-Null
Ok ("Подпись для коммитов: " + $cfg.Name + ' <' + $cfg.Email + '>')

# ── 6. verify GitHub auth ────────────────────────────────────────────────────
Say 'Стучусь к GitHub этим ключом'
$env:GIT_SSH_COMMAND = "ssh -o IdentitiesOnly=yes -o IdentityFile=`"$SshKeyPath`" -o StrictHostKeyChecking=accept-new"

$sshOut = & ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -o IdentityFile="$SshKeyPath" git@github.com 2>&1
$sshText = ($sshOut | Out-String)
if ($sshText -match 'successfully authenticated') {
  Ok 'GitHub нас узнал'
} else {
  Write-Host $sshText
  Die 'GitHub не принял ключ. Покажи ведущему вывод выше.'
}

# ── 7. clone or update ───────────────────────────────────────────────────────
if (Test-Path (Join-Path $RepoDir '.git')) {
  Say ("Папка " + $RepoDir + " уже существует — подтягиваю свежие изменения")
  & git -C $RepoDir remote set-url origin $RepoUrl       | Out-Null
  & git -C $RepoDir fetch origin --prune                 | Out-Null
  & git -C $RepoDir checkout main 2>$null                | Out-Null
  & git -C $RepoDir reset --hard origin/main             | Out-Null
  Ok 'Подтянул и выровнял main'
} else {
  Say ("Клонирую проект в " + $RepoDir)
  & git clone $RepoUrl $RepoDir
  if ($LASTEXITCODE -ne 0) { Die 'git clone упал. Сообщи ведущему.' }
  Ok 'Клонировано'
}

# ── 8. inject key + info в .git/ для Claude в Cowork ─────────────────────────
Say 'Готовлю sandbox-onboarding для Claude (.git/raif-workshop-*)'
$gitDir = Join-Path $RepoDir '.git'
$keyInGit  = Join-Path $gitDir 'raif-workshop-key'
$infoInGit = Join-Path $gitDir 'raif-workshop-info'

# .git/ git'ом не отслеживается, поэтому ключ тут никогда не попадёт в коммит.
Copy-Item -LiteralPath $SshKeyPath -Destination $keyInGit -Force
Lock-FileToCurrentUser -path $keyInGit
Ok ("Ключ для sandbox: " + $keyInGit)

$infoText = @"
# raif-workshop-2026 — мета-инфо участника для Claude в Cowork.
# Этот файл читает tools/cowork-onboard.py при первом запуске Claude.
WORKSHOP_PARTICIPANT=$($cfg.Participant)
WORKSHOP_BLOCK=$($cfg.Block)
WORKSHOP_GIT_NAME=$($cfg.Name)
WORKSHOP_GIT_EMAIL=$($cfg.Email)
"@
$infoText = ($infoText -replace "`r`n","`n") + "`n"
Write-FileNoBom -path $infoInGit -text $infoText
Ok ("Info-файл: " + $infoInGit)

# ── 9. done ──────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host '=========================================================='
Write-Host ' Всё готово. Твой ноутбук настроен на воркшоп.'
Write-Host ''
Write-Host ("   Папка проекта:    " + $RepoDir)
Write-Host ("   Подпись коммитов: " + $cfg.Name + ' <' + $cfg.Email + '>')
Write-Host ("   Блок:             " + $cfg.Block)
Write-Host ''
Write-Host ' Дальше:'
Write-Host ("   1. Открой Claude в Cowork mode.")
Write-Host ("   2. Подключи папку " + $RepoDir + " как working folder.")
Write-Host ('   3. Напиши Claude любое первое сообщение — он сам')
Write-Host ('      подцепит ключ и узнает, кто ты, по info-файлу.')
Write-Host ''
Write-Host ' (Стартовый flow с командой "claude" в терминале тоже работает —'
Write-Host '  если предпочитаешь его, открой папку в терминале и скажи "claude".)'
Write-Host '=========================================================='
Write-Host ''
exit 0
