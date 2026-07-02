# minitouch (x86_64) — สำหรับ LDPlayer14 (Android x86_64)

ที่มา: npm `@devicefarmer/minitouch-prebuilt` v1.3.0 (DeviceFarmer, official)
ไฟล์: ELF 64-bit x86-64, interpreter /system/bin/linker64

- `minitouch`       = PIE build → ใช้ตัวนี้ (Android 7+ / LDPlayer)
- `minitouch-nopie` = สำหรับ Android เก่ามาก (<5.0) ที่ไม่รองรับ PIE — ปกติไม่ต้องใช้

## setup (โดย MinitouchClient ตาม spec §2/§3)
1. adb push minitouch /data/local/tmp/minitouch
2. adb shell chmod 755 /data/local/tmp/minitouch
3. adb shell /data/local/tmp/minitouch        # daemon ค้างไว้, สร้าง abstract socket "minitouch"
4. adb forward tcp:1111 localabstract:minitouch
5. เชื่อม TCP 127.0.0.1:1111 → อ่าน banner → ส่ง command

## banner (อ่านตอนต่อ socket)
```
v <version>
^ <max_contacts> <max_x> <max_y> <max_pressure>   # เครื่องนี้ max_pressure = 2 !!
$ <pid>
```

## protocol (1 tap ที่ (x,y))
```
d 0 <x> <y> <pressure>\n     # pressure ต้อง <= max_pressure (=2) อย่าใส่ 100
c\n                          # commit
u 0\n                        # up
c\n                          # commit
```
- coord = pixel 1280x720 ตรง ๆ (device max = 1279x719)
- หน่วงเวลาระหว่าง tap: จัดการฝั่ง Python scheduler (perf_counter) ไม่ใช้ `w <ms>` ของ minitouch เพื่อคุม timing เอง
