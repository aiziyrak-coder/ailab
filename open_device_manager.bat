@echo off
chcp 65001 > nul
echo ============================================================
echo   BioBlue Euromex - Drayver Tuzatish Ko'rsatmasi
echo ============================================================
echo.
echo Bu skript Device Manager ni ochib, kamerani belgilaydi.
echo.

:: devmgmt.msc ochish
start devmgmt.msc

echo Device Manager ochildi.
echo.
echo ===== QUYIDAGI QADAMLARNI BAJARING =====
echo.
echo 1. Device Manager da "Universal Serial Bus devices" bo'limini
echo    oching (yoki "Other devices" yoki "Imaging devices")
echo.
echo 2. "USB2.0 Camera" qurilmasini IKKI MARTA bosing
echo.
echo 3. "Driver" tabiga o'ting
echo.
echo 4. "Update Driver" tugmasini bosing
echo.
echo 5. "Browse my computer for drivers" ni tanlang
echo.
echo 6. "Let me pick from a list of available drivers" ni tanlang
echo.
echo 7. Ro'yxatdan "USB Video Device" ni tanlang
echo    (yoki "Camera" kategoriyasini tanlang)
echo.
echo 8. "Next" tugmasini bosing va tasdiqlang
echo.
echo 9. Mikroskopni USB dan chiqarib qayta ulang
echo.
echo 10. Brauzerda http://localhost:5000 ni yangilang
echo.
echo ============================================
echo Bu oyna ochiq tursun - bajarilgach Enter bosing
echo ============================================
pause
echo.
echo Tekshirilmoqda...
powershell -Command "
$dev = Get-CimInstance Win32_PnPEntity | Where-Object { $_.DeviceID -match 'VID_0547' }
if ($dev) {
    Write-Host \"Qurilma: $($dev.Name) - $($dev.PNPClass) - $($dev.Description)\"
    if ($dev.PNPClass -eq 'Camera' -or $dev.Description -match 'USB Video') {
        Write-Host '[MUVAFFAQIYAT] Endi kamera dasturda ko rina di!'
    } else {
        Write-Host 'Hali ham WinUSB. Yuqoridagi qadamlarni takrorlang.'
    }
}
"
pause
