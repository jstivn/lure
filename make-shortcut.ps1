# Creates a "Lure" shortcut on the Desktop that starts the app with no console window and the Lure icon.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $root "meta-lead-gen\venv\Scripts\pythonw.exe"
$icon = Join-Path $root "control-hub\static\img\lure.ico"
$link = Join-Path ([Environment]::GetFolderPath("Desktop")) "Lure.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($link)
$lnk.TargetPath = $pythonw
$lnk.Arguments = "control-hub\app.py"
$lnk.WorkingDirectory = $root
$lnk.IconLocation = "$icon,0"
$lnk.Description = "Lure"
$lnk.Save()
