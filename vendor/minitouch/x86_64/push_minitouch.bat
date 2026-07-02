@echo off
REM push minitouch (x86_64) เข้า LDPlayer instance สำหรับ smoke test ด้วยมือ
REM (ตอนรันบอทจริง MinitouchClient จัดการ push/forward/run ให้เองตาม spec)
set "ADB=E:\LDPlayer\LDPlayer14\adb.exe"
set "SER=127.0.0.1:5555"
set "BIN=%~dp0minitouch"

"%ADB%" -s %SER% push "%BIN%" /data/local/tmp/minitouch
"%ADB%" -s %SER% shell chmod 755 /data/local/tmp/minitouch
"%ADB%" -s %SER% forward tcp:1111 localabstract:minitouch
echo.
echo pushed + forwarded (tcp:1111 -^> localabstract:minitouch)
echo run daemon (ค้าง terminal ไว้):
echo    "%ADB%" -s %SER% shell /data/local/tmp/minitouch
echo แล้วเชื่อม TCP 127.0.0.1:1111 อ่าน banner + ส่ง command
