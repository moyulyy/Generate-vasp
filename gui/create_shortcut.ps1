# 在桌面创建带图标、无控制台窗口的快捷方式；更换 Python 环境后重新运行即可
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = Join-Path (Split-Path (Get-Command python).Source) "pythonw.exe" }
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "VASP 作业文件生成器.lnk"))
$lnk.TargetPath = $pyw
$lnk.Arguments = "`"$here\incar_gui.py`""
$lnk.WorkingDirectory = $here
$lnk.IconLocation = "$here\assets\app.ico"
$lnk.Save()
Write-Host "已创建桌面快捷方式：$($lnk.FullName)"
