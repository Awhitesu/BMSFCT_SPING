Write-Host "--- Git Status ---"
& "C:\Program Files\Git\cmd\git.exe" status

Write-Host "--- Git Add ---"
& "C:\Program Files\Git\cmd\git.exe" add ui/tabs/overview_tab.py codebook.md doc/flowcharts/battery_current_monitor.html

Write-Host "--- Git Commit ---"
& "C:\Program Files\Git\cmd\git.exe" commit -m "feat: 电池模拟器电流超过10ma通道实时监控与循环显示"

Write-Host "--- Git Push ---"
& "C:\Program Files\Git\cmd\git.exe" push
