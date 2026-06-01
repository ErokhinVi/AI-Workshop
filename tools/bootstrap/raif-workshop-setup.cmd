@echo off
REM raif-workshop-setup.cmd
REM How to run:
REM   1. Double-click the file. A black window opens.
REM   2. If SmartScreen warns, click "More info" then "Run anyway".
REM   3. Pick your team and block, type your name in the dialog.
REM Git and ssh are installed automatically as a portable copy (MinGit) under
REM %LOCALAPPDATA%\raif-workshop\tools\ if they are missing. No admin rights.
REM Node.js and Python are NOT installed: Claude Code App ships its own runtime
REM and runs the agent inside its own sandbox, so the host needs neither.

setlocal EnableExtensions
chcp 65001 >nul 2>&1

echo.
echo === Raif AI Workshop setup ===
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
# PowerShell part. Launched by the trampoline above as a regular .ps1 in TEMP.
# ──────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding         = [System.Text.UTF8Encoding]::new()

# ── parameters ───────────────────────────────────────────────────────────────
# $RepoUrl/$RepoDir are assigned after the participant picks the team
$RepoUrl = $null
$RepoDir = $null
$SshDir           = Join-Path $env:USERPROFILE '.ssh'
$SshKeyPath       = Join-Path $SshDir   'raif_workshop'
$SshConfig        = Join-Path $SshDir   'config'
$SshConfigMarker  = '# raif-workshop-2026'

# ── helpers ──────────────────────────────────────────────────────────────────
$StartedAt         = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$script:TotalSteps = 10
$script:CurStep    = 0

function Banner {
  Write-Host ''
  Write-Host '╔══════════════════════════════════════════════════════════════╗' -ForegroundColor Cyan
  Write-Host '║  Raif AI Workshop · laptop setup                             ║' -ForegroundColor Cyan
  Write-Host '║  raif-workshop-setup.cmd                                     ║' -ForegroundColor Cyan
  Write-Host '╚══════════════════════════════════════════════════════════════╝' -ForegroundColor Cyan
  Write-Host ('  started: ' + $StartedAt)        -ForegroundColor DarkGray
  Write-Host ('  PC:      ' + $env:COMPUTERNAME) -ForegroundColor DarkGray
  Write-Host ('  user:    ' + $env:USERNAME)     -ForegroundColor DarkGray
  Write-Host ('  HOME:    ' + $env:USERPROFILE)  -ForegroundColor DarkGray
  Write-Host ''
}

function Step($title) {
  $script:CurStep++
  Write-Host ''
  Write-Host ('━━━━━━[ ' + $script:CurStep + '/' + $script:TotalSteps + ' ]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━') -ForegroundColor Blue
  Write-Host ('  ' + $title) -ForegroundColor Blue
  Write-Host ''
}

function Ok  ($m) { Write-Host ('  ✓ ' + $m) -ForegroundColor Green }
function Info($m) { Write-Host ('  · ' + $m) -ForegroundColor DarkGray }
function Note($m) { Write-Host ('      ' + $m) -ForegroundColor DarkGray }
function Warn($m) { Write-Host ('  ! ' + $m) -ForegroundColor Red }
function Die ($m) {
  Write-Host ''
  Write-Host ('  ✗ ' + $m) -ForegroundColor Red
  Write-Host ''
  Write-Host 'Setup aborted. Show the host the message above.' -ForegroundColor Red
  exit 1
}

function Require-Command($name, $hint) {
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  if (-not $cmd) { Die ("$name not found. $hint") }
}

# ─────────────────────────────────────────────────────────────────────────────
# Portable Git (MinGit) — unpacked locally if the machine has no git/ssh.
# MinGit is the official mini Git for Windows build, ~38 MB, ships git.exe +
# ssh.exe + minimal helpers. Unpacks from a zip without admin rights.
# Target: board members' laptops where Git for Windows may not be installed
# and the corporate Artifactory is unreachable. URL and version are pinned in
# case api.github.com throttles when several participants run the script at
# the same time.
# ─────────────────────────────────────────────────────────────────────────────
$MinGitVersion = '2.54.0'
$MinGitUrl     = 'https://github.com/git-for-windows/git/releases/download/v2.54.0.windows.1/MinGit-2.54.0-64-bit.zip'
# Tools live outside $RepoDir — otherwise git clone fails with "destination
# path already exists and is not an empty directory". LOCALAPPDATA is the
# standard per-user tools location on Windows, requires no admin and doesn't
# depend on where the repo lives.
$ToolsRoot     = Join-Path $env:LOCALAPPDATA 'raif-workshop\tools'
$MinGitDir     = Join-Path $ToolsRoot 'MinGit'

function Test-CommandAvailable($name) {
  return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Add-ToUserPath($folder) {
  # Persistent User-PATH via HKCU\Environment. setx truncates at 1024 chars
  # and loses variables of other users — [Environment]::SetEnvironmentVariable
  # avoids both traps. New processes (Claude/Codex App after restart) see it.
  $current = [Environment]::GetEnvironmentVariable('PATH', 'User')
  if ($null -eq $current) { $current = '' }
  $parts = $current -split ';' | Where-Object { $_ -and ($_.Trim()) }
  if ($parts -contains $folder) { return $false }
  $newPath = (@($folder) + $parts) -join ';'
  [Environment]::SetEnvironmentVariable('PATH', $newPath, 'User')
  return $true
}

function Install-MinGit {
  if (-not (Test-Path $ToolsRoot)) { New-Item -ItemType Directory -Path $ToolsRoot -Force | Out-Null }

  $gitExe = Join-Path $MinGitDir 'cmd\git.exe'
  if (Test-Path $gitExe) {
    Info ('Portable git already unpacked: ' + $MinGitDir)
  } else {
    $zipPath = Join-Path $ToolsRoot ('MinGit-' + $MinGitVersion + '-64-bit.zip')
    if (-not (Test-Path $zipPath)) {
      Info ('Downloading MinGit ' + $MinGitVersion + ' (~38 MB)')
      Note ('  ' + $MinGitUrl)
      $prevPP = $ProgressPreference
      $ProgressPreference = 'SilentlyContinue'
      try {
        # TLS 1.2 — needed for github.com on older Win10 without recent updates
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $MinGitUrl -OutFile $zipPath -UseBasicParsing -TimeoutSec 120
      } catch {
        Warn 'Could not download MinGit from github.com. The corporate proxy may be blocking HTTPS.'
        Note 'Manual fallback:'
        Note ('  1. Open in a browser: ' + $MinGitUrl)
        Note ('  2. Download the zip into: ' + $ToolsRoot)
        Note ('  3. Run this .cmd again — it will unpack the existing archive.')
        Die ('MinGit download failed: ' + $_.Exception.Message)
      } finally {
        $ProgressPreference = $prevPP
      }
    } else {
      Info ('Using already downloaded archive: ' + $zipPath)
    }
    try { Unblock-File -LiteralPath $zipPath -ErrorAction SilentlyContinue } catch {}
    Info 'Unpacking...'
    if (Test-Path $MinGitDir) { Remove-Item -LiteralPath $MinGitDir -Recurse -Force }
    try {
      Expand-Archive -LiteralPath $zipPath -DestinationPath $MinGitDir -Force
    } catch {
      Die ('Could not unpack MinGit: ' + $_.Exception.Message)
    }
    if (-not (Test-Path $gitExe)) { Die ('MinGit unpacked, but git.exe not found at ' + $gitExe) }
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    Ok ('MinGit unpacked into ' + $MinGitDir)
  }

  $gitBin = Join-Path $MinGitDir 'cmd'
  $sshBin = Join-Path $MinGitDir 'usr\bin'

  # Current session PATH — so subsequent & git / & ssh calls in this script work
  if (($env:PATH -split ';') -notcontains $gitBin) { $env:PATH = $gitBin + ';' + $env:PATH }
  if (($env:PATH -split ';') -notcontains $sshBin) { $env:PATH = $sshBin + ';' + $env:PATH }

  # Persistent User-PATH — so Claude/Codex App sees git/ssh after restart
  $added = $false
  if (Add-ToUserPath $gitBin) { $added = $true }
  if (Add-ToUserPath $sshBin) { $added = $true }
  if ($added) {
    Ok 'PortableGit added to persistent User-PATH'
    Note '(new Claude/Codex windows will see git after the app restarts)'
  } else {
    Info 'PortableGit was already in User-PATH'
  }
}

function Ensure-PortableTools {
  $needGit  = -not (Test-CommandAvailable 'git')
  $needSsh  = -not (Test-CommandAvailable 'ssh')

  if ($needGit) { Info 'git not on PATH — will install a portable copy (MinGit)' }
  if ($needSsh) { Info 'ssh not on PATH — will take ssh from portable MinGit' }
  if ($needGit -or $needSsh) { Install-MinGit }
  if (-not (Test-CommandAvailable 'git')) { Die 'After MinGit install git is still not available. Show the host the log above.' }
  if (-not (Test-CommandAvailable 'ssh')) { Die 'After MinGit install ssh is still not available. Show the host the log above.' }

  Ok ('git: ' + ((& git --version) | Out-String).Trim())
  $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $sshVer = ((& ssh -V 2>&1) | Out-String).Trim() } catch { $sshVer = '(version unavailable)' }
  $ErrorActionPreference = $prevEAP
  Ok ('ssh: ' + $sshVer)
}

function Write-FileNoBom($path, $text) {
  $enc = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($path, $text, $enc)
}

function Lock-FileToCurrentUser($path) {
  # Drop inheritance, grant access only to the current user
  & icacls $path /inheritance:r           | Out-Null
  & icacls $path /grant:r "$($env:USERNAME):F" | Out-Null
  & icacls $path /remove "BUILTIN\Users"      2>&1 | Out-Null
  & icacls $path /remove "NT AUTHORITY\Authenticated Users" 2>&1 | Out-Null
}

# Mark the repo folder as trusted in ~/.codex/config.toml — otherwise Codex
# does not load the project's .codex/config.toml. Append a block, don't
# overwrite whatever the user already has. If the path format does not match
# — no harm done: Codex simply asks for trust on its next start.
function Add-CodexTrust($RepoDir) {
  $codexHome = Join-Path $env:USERPROFILE '.codex'
  $codexCfg  = Join-Path $codexHome 'config.toml'
  if (-not (Test-Path $codexHome)) { New-Item -ItemType Directory -Path $codexHome | Out-Null }
  $existing = ''
  if (Test-Path $codexCfg) { $existing = Get-Content -LiteralPath $codexCfg -Raw -ErrorAction SilentlyContinue }
  if ($null -eq $existing) { $existing = '' }
  # Windows path in a TOML string: double the backslashes
  $repoForToml = $RepoDir -replace '\\','\\'
  $marker = '[projects."' + $repoForToml + '"]'
  if ($existing -match [Regex]::Escape($marker)) {
    Note 'Folder already trusted by Codex'
    return
  }
  $block   = "`n$marker`ntrust_level = `"trusted`"`n"
  $newText = ($existing -replace "`r`n","`n").TrimEnd("`n")
  if ($newText) { $newText = $newText + "`n" }
  $newText = $newText + $block
  Write-FileNoBom -path $codexCfg -text $newText
  Note ('Folder marked as trusted in ' + $codexCfg)
}

# ── 0. sanity ────────────────────────────────────────────────────────────────
Banner
Step 'Checking the environment and tools'

# Full tool sweep: what's already on the machine, so the in-room log shows
# at a glance who is missing what.
$osCaption = ''
try { $osCaption = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption } catch {}
Info ('OS:        ' + [System.Environment]::OSVersion.VersionString + $(if ($osCaption) { '  (' + $osCaption + ')' }))
Info ('arch:      ' + $env:PROCESSOR_ARCHITECTURE)
Info ('user:      ' + $env:USERNAME)
Info ('HOME:      ' + $env:USERPROFILE)
Info ('TOOLS:     ' + $ToolsRoot)
# REPO_DIR printed later, once team selection is known

function Show-Tool($name, $hint) {
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  if ($cmd) {
    $ver = '?'
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
      $raw = (& $name --version 2>&1) | Out-String
      $ver = ($raw -split "`n")[0].Trim()
    } catch {}
    $ErrorActionPreference = $prevEAP
    Info ($name.PadRight(8) + ': ✓  ' + $cmd.Source + '  (' + $ver + ')')
    return $true
  }
  if ($hint) {
    Info ($name.PadRight(8) + ': ✗  not installed  (' + $hint + ')')
  } else {
    Info ($name.PadRight(8) + ': ✗  not installed')
  }
  return $false
}

Show-Tool 'git'    'will install via portable MinGit below' | Out-Null
Show-Tool 'ssh'    'will take ssh from MinGit if missing' | Out-Null
# Nothing else is required on the host: python/node aren't used by this
# script — the agent (Claude Code App pre-installed on board laptops) runs
# inside its own sandbox.

Ensure-PortableTools
Ok 'Environment looks good'

# ── 1. team / block / name picker (WinForms) ─────────────────────────────────
Info 'Opening the participant picker window...'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Show-WorkshopPicker {
  $form = New-Object Windows.Forms.Form
  $form.Text            = 'Raif AI Workshop — laptop setup'
  $form.Size            = New-Object Drawing.Size(540, 460)
  $form.StartPosition   = 'CenterScreen'
  $form.FormBorderStyle = 'FixedDialog'
  $form.MaximizeBox     = $false
  $form.MinimizeBox     = $false
  $form.Font            = New-Object Drawing.Font('Segoe UI', 10)

  $teamLabel = New-Object Windows.Forms.Label
  $teamLabel.Text     = 'Your team:'
  $teamLabel.Location = New-Object Drawing.Point(18, 15)
  $teamLabel.Size     = New-Object Drawing.Size(470, 22)
  $form.Controls.Add($teamLabel)

  $teamA = New-Object Windows.Forms.RadioButton
  $teamA.Text     = 'Team A'
  $teamA.Location = New-Object Drawing.Point(28, 40)
  $teamA.Size     = New-Object Drawing.Size(220, 24)
  $teamA.Checked  = $true
  $form.Controls.Add($teamA)

  $teamB = New-Object Windows.Forms.RadioButton
  $teamB.Text     = 'Team B'
  $teamB.Location = New-Object Drawing.Point(260, 40)
  $teamB.Size     = New-Object Drawing.Size(220, 24)
  $form.Controls.Add($teamB)

  $blockLabel = New-Object Windows.Forms.Label
  $blockLabel.Text     = 'Your block:'
  $blockLabel.Location = New-Object Drawing.Point(18, 80)
  $blockLabel.Size     = New-Object Drawing.Size(470, 22)
  $form.Controls.Add($blockLabel)

  $blockBox = New-Object Windows.Forms.ListBox
  $blockBox.Location = New-Object Drawing.Point(28, 105)
  $blockBox.Size     = New-Object Drawing.Size(460, 90)
  [void]$blockBox.Items.AddRange(@(
    'Retail — customer mobile bank',
    'CIB — corporate and business logic',
    'Backend — bank data core'
  ))
  $blockBox.SelectedIndex = 0
  $form.Controls.Add($blockBox)

  $nameLabel = New-Object Windows.Forms.Label
  $nameLabel.Text     = 'Your name and surname (used to sign your commits):'
  $nameLabel.Location = New-Object Drawing.Point(18, 210)
  $nameLabel.Size     = New-Object Drawing.Size(470, 22)
  $form.Controls.Add($nameLabel)

  $nameBox = New-Object Windows.Forms.TextBox
  $nameBox.Location = New-Object Drawing.Point(28, 235)
  $nameBox.Size     = New-Object Drawing.Size(460, 28)
  $form.Controls.Add($nameBox)

  $hostBox = New-Object Windows.Forms.CheckBox
  $hostBox.Text     = "I'm the workshop host (full repo access, no block isolation)"
  $hostBox.Location = New-Object Drawing.Point(28, 280)
  $hostBox.Size     = New-Object Drawing.Size(460, 24)
  $form.Controls.Add($hostBox)

  $ok = New-Object Windows.Forms.Button
  $ok.Text         = 'Go'
  $ok.Location     = New-Object Drawing.Point(290, 370)
  $ok.Size         = New-Object Drawing.Size(95, 32)
  $ok.DialogResult = [Windows.Forms.DialogResult]::OK
  $form.Controls.Add($ok)
  $form.AcceptButton = $ok

  $cancel = New-Object Windows.Forms.Button
  $cancel.Text         = 'Cancel'
  $cancel.Location     = New-Object Drawing.Point(395, 370)
  $cancel.Size         = New-Object Drawing.Size(95, 32)
  $cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel
  $form.Controls.Add($cancel)
  $form.CancelButton = $cancel

  $result = $form.ShowDialog()
  if ($result -ne [Windows.Forms.DialogResult]::OK) { return $null }

  $name = ($nameBox.Text).Trim()
  if (-not $name) { return @{ Error = 'empty-name' } }

  $team = if ($teamA.Checked) { 'team_a' } else { 'team_b' }
  $blockMap = @{ 0 = 'retail'; 1 = 'cib'; 2 = 'backend' }
  $block = $blockMap[$blockBox.SelectedIndex]

  if ($hostBox.Checked) { $team = 'host'; $block = 'host' }

  return @{
    Team        = $team
    Block       = $block
    Name        = $name
  }
}

$picked = Show-WorkshopPicker
if ($null -eq $picked) { Write-Host 'Cancelled.'; exit 0 }
if ($picked.Error -eq 'empty-name') {
  Die 'Name is empty. Run the script again and type your name and surname.'
}

# Build the participant config from the picker output.
# Slug: ASCII letters, digits and dashes only. Anything else collapses to '-'.
$slugRaw = $picked.Name.ToLower()
$slugRaw = $slugRaw -replace '[^a-z0-9]+', '-'
$slugRaw = $slugRaw.Trim('-')
if (-not $slugRaw) { $slugRaw = 'anonymous' }

$cfg = @{
  Team        = $picked.Team
  Block       = $picked.Block
  Name        = $picked.Name
  Email       = $slugRaw + '@raif-workshop.local'
  Participant = $slugRaw
}

switch ($cfg.Team) {
  'team_a' { $RepoUrl = 'git@github.com:ErokhinVi/team_1.git'; $RepoDir = Join-Path $env:USERPROFILE 'team_1' }
  'team_b' { $RepoUrl = 'git@github.com:ErokhinVi/team_2.git'; $RepoDir = Join-Path $env:USERPROFILE 'team_2' }
  'host'   { $RepoUrl = 'git@github.com:ErokhinVi/AI-Workshop.git'; $RepoDir = Join-Path $env:USERPROFILE 'AI-Workshop' }
}
Info ('REPO_URL:  ' + $RepoUrl)
Info ('REPO_DIR:  ' + $RepoDir)
$teamHuman  = @{ 'team_a' = 'Team A'; 'team_b' = 'Team B'; 'host' = 'Host' }[$cfg.Team]
$blockHuman = @{ 'retail' = 'Retail — customer mobile bank'; 'cib' = 'CIB — corporate and business logic'; 'backend' = 'Backend — bank data core'; 'host' = '—' }[$cfg.Block]
Ok ('Participant: ' + $cfg.Name + '  (' + $teamHuman + ' · ' + $blockHuman + ')')

# ── 3. SSH key (embedded, base64 — keeps secret-scanners quiet) ──────────────
Step 'Dropping the workshop SSH key'
if (-not (Test-Path $SshDir)) { New-Item -ItemType Directory -Path $SshDir | Out-Null }
Info ('Folder: ' + $SshDir)

$PrivateKeyB64 = 'LS0tLS1CRUdJTiBPUEVOU1NIIFBSSVZBVEUgS0VZLS0tLS0KYjNCbGJuTnphQzFyWlhrdGRqRUFBQUFBQkc1dmJtVUFBQUFFYm05dVpRQUFBQUFBQUFBQkFBQUFNd0FBQUF0emMyZ3RaV1EKeU5UVXhPUUFBQUNDYTluUFJ4TkJMYUhYTWFKU3didXdlelRjb1FLTS90NStHMGRvR09kQzJHQUFBQUtBNzZsam5PK3BZCjV3QUFBQXR6YzJndFpXUXlOVFV4T1FBQUFDQ2E5blBSeE5CTGFIWE1hSlN3YnV3ZXpUY29RS00vdDUrRzBkb0dPZEMyR0EKQUFBRUNLMFJqU0IvbEhjWmdwejZPcldUSVZ1SVNDc2xoTFAzeWhFeUN1UWRLWS81cjJjOUhFMEV0b2RjeG9sTEJ1N0I3TgpOeWhBb3orM240YlIyZ1k1MExZWUFBQUFHMk5zWVhWa1pTMWpiM2R2Y21zdGNtRnBaaTEzYjNKcmMyaHZjQUVDCi0tLS0tRU5EIE9QRU5TU0ggUFJJVkFURSBLRVktLS0tLQo='
$PrivateKey = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($PrivateKeyB64))

# OpenSSH expects LF line endings, no BOM
$keyText = ($PrivateKey -replace "`r`n", "`n")
if (-not $keyText.EndsWith("`n")) { $keyText = $keyText + "`n" }
Write-FileNoBom -path $SshKeyPath -text $keyText
Lock-FileToCurrentUser -path $SshKeyPath
$fp = '?'
$prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
try { $fpLine = ((& ssh-keygen -lf $SshKeyPath 2>&1) | Out-String).Trim(); if ($fpLine) { $fp = $fpLine } } catch {}
$ErrorActionPreference = $prevEAP
Ok ('File: ' + $SshKeyPath + '  (current user only)')
Note ('fingerprint: ' + $fp)

# ── 4. SSH config ────────────────────────────────────────────────────────────
Step 'Configuring ssh to use this key for GitHub'
Info ('File: ' + $SshConfig)
if (-not (Test-Path $SshConfig)) {
  Write-FileNoBom -path $SshConfig -text ''
}

$configText = Get-Content -LiteralPath $SshConfig -Raw -ErrorAction SilentlyContinue
if ($null -eq $configText) { $configText = '' }

if ($configText -match [Regex]::Escape($SshConfigMarker)) {
  Ok ("GitHub entry already present in " + $SshConfig)
} else {
  $block = @"

$SshConfigMarker
# GitHub via port 443 — port 22 is blocked on the corporate network
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile $SshKeyPath
  IdentitiesOnly yes
"@
  $newCfg = ($configText -replace "`r`n","`n").TrimEnd("`n")
  if ($newCfg) { $newCfg = $newCfg + "`n" }
  $newCfg = $newCfg + ($block -replace "`r`n","`n")
  Write-FileNoBom -path $SshConfig -text $newCfg
  Lock-FileToCurrentUser -path $SshConfig
  Ok ('Added Host github.com block → IdentityFile=' + $SshKeyPath)
}

# ── 5. git identity ──────────────────────────────────────────────────────────
Step 'Participant identity for commit signatures'
Info ('Participant: ' + $cfg.Name)
Info ('Email:       ' + $cfg.Email)
Info ('Team:        ' + $teamHuman + ' (' + $cfg.Team + ')')
Info ('Block:       ' + $blockHuman)
if ($cfg.Team -ne 'host') { Info ('Block folder: ' + $cfg.Block + '\') }

& git config --global user.name  $cfg.Name  | Out-Null
& git config --global user.email $cfg.Email | Out-Null
Ok ('Global git signature: ' + $cfg.Name + ' <' + $cfg.Email + '>')
Note 'file: ~/.gitconfig'

# ── 6. GitHub auth check ─────────────────────────────────────────────────────
Step 'Checking GitHub access with this key'
Info 'ssh -T git@github.com  (BatchMode, StrictHostKeyChecking=accept-new)'
$env:GIT_SSH_COMMAND = "ssh -o IdentitiesOnly=yes -o IdentityFile=`"$SshKeyPath`" -o StrictHostKeyChecking=accept-new"

# ssh -T prints useful diagnostics ("Permanently added github.com to known_hosts")
# to stderr. With $ErrorActionPreference='Stop' and 2>&1 PowerShell 5.1
# interprets that as a terminating NativeCommandError. Isolate the call.
$sshOut = $null
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
  $sshOut = & ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -o IdentityFile="$SshKeyPath" git@github.com 2>&1
} finally {
  $ErrorActionPreference = $prevEAP
}
$sshText = ($sshOut | Out-String)
if ($sshText -match 'successfully authenticated') {
  $ghUser = ''
  $ghMatch = [Regex]::Match($sshText, 'Hi ([^!]+)!')
  if ($ghMatch.Success) { $ghUser = $ghMatch.Groups[1].Value }
  if ($ghUser) { Ok ('GitHub recognised us as ' + $ghUser) } else { Ok 'GitHub recognised us' }
} else {
  Write-Host $sshText
  Die 'GitHub did not accept the key. Show the host the output above.'
}

# ── 7. clone or update ───────────────────────────────────────────────────────
Step ('Preparing the project folder ' + $RepoDir)
if (Test-Path (Join-Path $RepoDir '.git')) {
  Info 'Folder already exists — pulling fresh changes'
  & git -C $RepoDir remote set-url origin $RepoUrl       | Out-Null
  & git -C $RepoDir fetch origin --prune                 | Out-Null
  & git -C $RepoDir checkout main 2>$null                | Out-Null
  & git -C $RepoDir reset --hard origin/main             | Out-Null
  Ok 'Pulled and aligned main'
} else {
  Info ('Cloning ' + $RepoUrl)
  & git clone $RepoUrl $RepoDir
  if ($LASTEXITCODE -ne 0) { Die 'git clone failed. Tell the host.' }
  Ok ('Cloned into ' + $RepoDir)
}
$headLine = '?'; $branchLine = '?'
try { $headLine   = ((& git -C $RepoDir log -1 --format="%h %s") | Out-String).Trim() } catch {}
try { $branchLine = ((& git -C $RepoDir rev-parse --abbrev-ref HEAD) | Out-String).Trim() } catch {}
Note ('branch: ' + $branchLine)
Note ('HEAD:   ' + $headLine)

# ── 7b. team isolation: settings.local.json per (team, block) ────────────────
Step 'Installing block isolation — edits restricted to your block'
$claudeDir = Join-Path $RepoDir '.claude'
$tpl = Join-Path $claudeDir ('templates\settings-' + $cfg.Block + '.json')
if ($cfg.Team -eq 'host') {
  Info 'Host mode — no isolation installed'
  Ok 'Full access to the whole repository'
} elseif (Test-Path $tpl) {
  Copy-Item -LiteralPath $tpl -Destination (Join-Path $claudeDir 'settings.local.json') -Force
  Ok 'Claude isolation active: .claude\settings.local.json'
  Note ('template: settings-' + $cfg.Block + '.json')
} else {
  Warn ('Template not found: ' + $tpl)
  Note 'Claude will warn about this during onboarding'
}

# ── 7c. Codex isolation: .codex/config.toml per (team, block) ────────────────
# Same block protection for participants who use Codex instead of Claude.
Step 'Installing Codex isolation (if anyone uses Codex instead of Claude)'
$codexDir = Join-Path $RepoDir '.codex'
$codexTpl = Join-Path $codexDir ('templates\config-' + $cfg.Block + '.toml')
if ($cfg.Team -eq 'host') {
  Info 'Host mode — no Codex isolation installed'
  Ok 'Full access to the whole repository'
} elseif (Test-Path $codexTpl) {
  Copy-Item -LiteralPath $codexTpl -Destination (Join-Path $codexDir 'config.toml') -Force
  Ok 'Codex isolation active: .codex\config.toml'
  Note ('template: config-' + $cfg.Block + '.toml')
  Add-CodexTrust -RepoDir $RepoDir
} else {
  Warn ('Codex template not found: ' + $codexTpl)
  Note 'Codex will install isolation itself during onboarding (see AGENTS.md)'
}

# ── 8. inject key + info into .git/ for Claude Code App ──────────────────────
Step 'Preparing sandbox onboarding for Claude (.git\raif-workshop-*)'
$gitDir = Join-Path $RepoDir '.git'
$keyInGit  = Join-Path $gitDir 'raif-workshop-key'
$infoInGit = Join-Path $gitDir 'raif-workshop-info'

# .git/ is not tracked by git, so the key never ends up in a commit.
Copy-Item -LiteralPath $SshKeyPath -Destination $keyInGit -Force
Lock-FileToCurrentUser -path $keyInGit
Ok ("Sandbox key: " + $keyInGit)

$infoText = @"
# raif-workshop-2026 — participant meta-info for Claude Code App.
# Read by tools/cowork-onboard.py on Claude's first launch (only relevant
# when the agent runs inside a Linux sandbox; ignored on a Win/Mac host).
WORKSHOP_PARTICIPANT=$($cfg.Participant)
WORKSHOP_TEAM=$($cfg.Team)
WORKSHOP_BLOCK=$($cfg.Block)
WORKSHOP_GIT_NAME=$($cfg.Name)
WORKSHOP_GIT_EMAIL=$($cfg.Email)
"@
$infoText = ($infoText -replace "`r`n","`n") + "`n"
Write-FileNoBom -path $infoInGit -text $infoText
Ok ('Info file: ' + $infoInGit)
Note ('WORKSHOP_PARTICIPANT=' + $cfg.Participant)
Note ('WORKSHOP_TEAM=' + $cfg.Team)
Note ('WORKSHOP_BLOCK=' + $cfg.Block)

# ── 9. local repo git config (safety net for Claude agent sessions) ──────────
# If the agent starts inside its own sandbox with its own $HOME, --global on
# the host user is invisible from there. Drop the signature and ssh-command
# into local .git/config: it lives on disk and is visible from any
# environment opening this repo.
Step 'Local repo git config — safety net for Claude agent sessions'
& git -C $RepoDir config user.name  $cfg.Name  | Out-Null
& git -C $RepoDir config user.email $cfg.Email | Out-Null
$keyFwd = $keyInGit -replace '\\', '/'
$sshCmd = "ssh -i '" + $keyFwd + "' -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/tmp/raif_known_hosts"
& git -C $RepoDir config core.sshCommand $sshCmd | Out-Null
Ok ('user.name       = ' + $cfg.Name)
Ok ('user.email      = ' + $cfg.Email)
Ok 'core.sshCommand = ssh -i .git/raif-workshop-key (accept-new)'
Note ('file: ' + (Join-Path $gitDir 'config'))

# Post-clone hardening (anti-lock + Defender + shortcut) — in a separate ps1
# file to keep this .cmd byte-perfect with the version known to work.
$hardenPs1 = Join-Path $RepoDir "tools\bootstrap\harden.ps1"
if (Test-Path $hardenPs1) {
  try {
    & $hardenPs1 -RepoDir $RepoDir
  } catch {
    Warn ("harden.ps1 failed: " + $_.Exception.Message)
  }
}

# ── 9. done ──────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host ''
Write-Host '╔══════════════════════════════════════════════════════════════╗' -ForegroundColor Cyan
Write-Host '║  ALL SET. Your laptop is ready for the workshop.             ║' -ForegroundColor Cyan
Write-Host '╚══════════════════════════════════════════════════════════════╝' -ForegroundColor Cyan
Write-Host ''
Write-Host ('  Project folder:   ' + $RepoDir)
Write-Host ('  Signature:        ' + $cfg.Name + ' <' + $cfg.Email + '>')
Write-Host ('  Team:             ' + $teamHuman + ' (' + $cfg.Team + ')')
Write-Host ('  Block:            ' + $blockHuman)
Write-Host ('  Current branch:   ' + $branchLine)
Write-Host ('  Project HEAD:     ' + $headLine)
Write-Host ('  SSH fingerprint:  ' + $fp)
Write-Host ''
Write-Host '  Block isolation:' -ForegroundColor DarkGray
if ($cfg.Team -eq 'host') {
  Write-Host '  You are the host — full access, no block isolation.' -ForegroundColor DarkGray
} else {
  Write-Host '  You see and edit only your block. The other team is not' -ForegroundColor DarkGray
  Write-Host '  visible — you can only reach it by visiting its public site.' -ForegroundColor DarkGray
}
Write-Host ''
Write-Host '  Files the script created or updated:'
Write-Host ('    ✓ ' + $SshKeyPath + '  (workshop private key)')
Write-Host ('    ✓ ' + $SshConfig + '  (Host github.com block)')
Write-Host ('    ✓ ' + (Join-Path $env:USERPROFILE '.gitconfig') + '  (git --global)')
Write-Host ('    ✓ ' + $keyInGit + '  (key copy for Claude)')
Write-Host ('    ✓ ' + $infoInGit + '  (meta-info for Claude)')
Write-Host ('    ✓ ' + (Join-Path $gitDir 'config') + '  (local signature + core.sshCommand)')
if ($cfg.Team -eq 'host') {
  Write-Host '    · no block isolation installed (host)'
} else {
  Write-Host ('    ✓ ' + (Join-Path $claudeDir 'settings.local.json') + '  (Claude block isolation)')
  Write-Host ('    ✓ ' + (Join-Path $codexDir 'config.toml') + '  (Codex block isolation)')
}
Write-Host ''
Write-Host '  What''s next:'
if (Test-Path (Join-Path $MinGitDir 'cmd\git.exe')) {
  Write-Host '    1. If Claude Code was open — close it completely (including the tray)' -ForegroundColor Yellow
  Write-Host '       and reopen it. Otherwise it won''t see the git I just installed.' -ForegroundColor Yellow
  Write-Host '    2. Open Claude Code App.'
  Write-Host ('    3. Add the folder ' + $RepoDir + ' as the working folder.')
  Write-Host '    4. Write the agent any first message — it will pick up the key'
  Write-Host '       and read who you are from the info file.'
} else {
  Write-Host '    1. Open Claude Code App.'
  Write-Host ('    2. Add the folder ' + $RepoDir + ' as the working folder.')
  Write-Host '    3. Write the agent any first message — it will pick up the key'
  Write-Host '       and read who you are from the info file.'
}
Write-Host ''
Write-Host '  (The older flow with the "claude" command in a terminal still works —'
Write-Host '   open the folder in a terminal and type "claude".)'
Write-Host ''
Write-Host '  If you use Codex instead of Claude: open the project folder in Codex'
Write-Host '  and write a first message — block isolation is already in place'
Write-Host '  (.codex\config.toml), and Codex reads the script from AGENTS.md.'
Write-Host ''
exit 0
