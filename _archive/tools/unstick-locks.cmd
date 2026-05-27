@echo off
REM unstick-locks.cmd — аварийный сброс git-локов в репо воркшопа.
REM
REM Запускается двойным кликом по ярлыку на рабочем столе
REM («Раиф-Воркшоп — починить git») если Claude в Cowork говорит:
REM   - «не могу сохранить работу»
REM   - «Another git process seems to be running»
REM   - «.git/index.lock: Operation not permitted»
REM
REM Что делает:
REM   1. Прибивает зависшие git-процессы и fsmonitor-daemon.
REM   2. Удаляет .git/*.lock + .git/objects/maintenance.lock.
REM   3. Сбрасывает refs/heads/*.lock.
REM   4. Печатает короткий статус.
REM
REM После этого вернись в Claude и попроси «попробуй ещё раз сохранить».

setlocal EnableExtensions
chcp 65001 >nul 2>&1

REM Папка проекта — обычно %USERPROFILE%\AI-Workshop. Если ярлык запущен из
REM другой папки — берём её WorkingDirectory (CMD выставит cwd при запуске
REM через ярлык). Иначе fallback на USERPROFILE\AI-Workshop.
set "REPO=%CD%"
if not exist "%REPO%\.git" set "REPO=%USERPROFILE%\AI-Workshop"
if not exist "%REPO%\.git" (
  echo [ERROR] Не нашёл папку с .git ни в "%CD%" ни в "%USERPROFILE%\AI-Workshop".
  echo Запусти этот скрипт изнутри папки воркшопа.
  pause
  exit /b 1
)

echo.
echo === Раиф-Воркшоп — аварийный сброс git-локов ===
echo Папка: %REPO%
echo.

echo [1/4] Прибиваю висящие git-процессы...
taskkill /IM git.exe /F                  >nul 2>&1
taskkill /IM "git-credential-manager.exe" /F >nul 2>&1
taskkill /IM "fsmonitor--daemon.exe" /F  >nul 2>&1
taskkill /IM "sh.exe" /F                 >nul 2>&1
echo   готово.

echo [2/4] Сношу .git\*.lock...
for %%F in (
  HEAD.lock index.lock packed-refs.lock config.lock REBASE_HEAD.lock
  MERGE_HEAD.lock FETCH_HEAD.lock ORIG_HEAD.lock shallow.lock gc.pid.lock
) do (
  if exist "%REPO%\.git\%%F" (
    del /f /q "%REPO%\.git\%%F" 2>nul
    if exist "%REPO%\.git\%%F" (
      echo   ! Не удалил %%F — держит какой-то процесс. Перезагрузись и запусти меня ещё раз.
    ) else (
      echo   - %%F
    )
  )
)

if exist "%REPO%\.git\objects\maintenance.lock" (
  del /f /q "%REPO%\.git\objects\maintenance.lock" 2>nul
  if not exist "%REPO%\.git\objects\maintenance.lock" echo   - objects\maintenance.lock
)

echo [3/4] Сношу ref-локи под refs\...
for /R "%REPO%\.git\refs" %%F in (*.lock) do (
  del /f /q "%%F" 2>nul
  echo   - %%~nxF
)

echo [4/4] Отключаю git maintenance + fsmonitor для этого репо...
pushd "%REPO%" >nul
git maintenance unregister                  >nul 2>&1
git config core.fsmonitor false             >nul 2>&1
git config maintenance.auto false           >nul 2>&1
git config gc.auto 0                        >nul 2>&1
popd >nul

echo.
echo ✓ Готово. Возвращайся в Claude и попроси «попробуй ещё раз сохранить».
echo.
echo Если ошибка повторяется — позови ведущего и покажи это окно.
echo.
pause
exit /b 0
