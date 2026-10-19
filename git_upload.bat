@echo off
echo --- Git Status ---
"C:\Program Files\Git\cmd\git.exe" status

echo --- Git Add ---
"C:\Program Files\Git\cmd\git.exe" add .

echo --- Git Commit ---
"C:\Program Files\Git\cmd\git.exe" commit -m "feat: battery simulator current monitor"

echo --- Git Push ---
"C:\Program Files\Git\cmd\git.exe" push
