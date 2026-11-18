@echo off
rem Flash the ColorOS 17 port to a Xiaomi Mi 9 (cepheus) from the bootloader's fastboot mode.
rem Writes boot, dtbo, vbmeta, system and vendor (and cust if super_cust.img is present),
rem and ERASES userdata and metadata.  flash_all.bat --keep-data re-flashes without erasing.
setlocal
cd /d "%~dp0"
where fastboot >nul 2>nul || (echo fastboot not found - install Android platform-tools & exit /b 1)
for %%f in (boot.img dtbo.img vbmeta.img super_system.img super_vendor.img) do (
  if not exist %%f (echo missing %%f & exit /b 1)
)
fastboot getvar product 2>&1 | findstr /r /c:"^product: *cepheus" >nul || (echo This device is not a cepheus. Aborting. & exit /b 1)
fastboot getvar unlocked 2>&1 | findstr /r /c:"^unlocked: *no" >nul && (echo The bootloader is locked. Unlock it first. & exit /b 1)
set KEEP_DATA=0
if /i "%~1"=="--keep-data" set KEEP_DATA=1
echo.
if "%KEEP_DATA%"=="1" (
  echo Re-flashing the port WITHOUT erasing userdata - use only over an earlier build of this port.
) else (
  echo This will install an EXPERIMENTAL, never device-tested ColorOS 17 port and ERASE ALL USER DATA
  echo ^(internal storage included^). Read README.txt first.
)
echo.
set /p answer=Type YES to continue: 
if not "%answer%"=="YES" (echo aborted & exit /b 1)
fastboot flash boot boot.img || exit /b 1
fastboot flash dtbo dtbo.img || exit /b 1
fastboot flash vbmeta vbmeta.img || exit /b 1
fastboot flash system super_system.img || exit /b 1
fastboot flash vendor super_vendor.img || exit /b 1
if exist super_cust.img (fastboot flash cust super_cust.img || exit /b 1)
if "%KEEP_DATA%"=="0" (
  fastboot erase metadata || echo warning: could not erase metadata, continuing
  fastboot erase frp || echo warning: could not erase frp, continuing
  fastboot erase userdata || exit /b 1
)
fastboot reboot
echo Done. The first boot formats userdata and can take 5-10 minutes. See README.txt if it does not boot.
