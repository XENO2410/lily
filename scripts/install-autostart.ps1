<#
.SYNOPSIS
    Registers Lily to launch when you log in.

.PARAMETER Method
    'Startup' (default) drops a shortcut into the user's Startup folder.
    'Task' registers a Scheduled Task triggered at logon.

.EXAMPLE
    .\install-autostart.ps1                   # user Startup folder (simplest)
    .\install-autostart.ps1 -Method Task      # Task Scheduler

.NOTES
    Uses `lilyw.exe` (pythonw-backed) so no console window pops up at login.
#>
param(
    [ValidateSet('Startup', 'Task')]
    [string]$Method = 'Startup'
)

$ErrorActionPreference = 'Stop'

$lilyExe = (Get-Command lilyw -ErrorAction SilentlyContinue).Source
if (-not $lilyExe) {
    Write-Error "lilyw.exe not found on PATH. Install Lily first: pipx install --editable <repo>"
    exit 1
}

if ($Method -eq 'Startup') {
    $startup = [Environment]::GetFolderPath('Startup')
    $shortcut = Join-Path $startup 'Lily.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($shortcut)
    $lnk.TargetPath = $lilyExe
    $lnk.Arguments = 'start'
    $lnk.WorkingDirectory = Split-Path -Parent $lilyExe
    $lnk.IconLocation = $lilyExe
    $lnk.Description = 'Lily — local-first Windows assistant'
    $lnk.Save()
    Write-Host "Installed Startup shortcut: $shortcut"
    Write-Host "Lily will launch next time you log in. Delete the shortcut to disable."
    exit 0
}

# Method = Task
$taskName = 'Lily'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action = New-ScheduledTaskAction -Execute $lilyExe -Argument 'start'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -Hidden
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings | Out-Null
Write-Host "Registered Scheduled Task '$taskName'. Remove with:"
Write-Host "  Unregister-ScheduledTask -TaskName Lily -Confirm:`$false"
