<#
.SYNOPSIS
    Removes Lily's auto-start (both Startup shortcut and Scheduled Task, if present).
#>
$ErrorActionPreference = 'SilentlyContinue'

$startup = [Environment]::GetFolderPath('Startup')
$shortcut = Join-Path $startup 'Lily.lnk'
if (Test-Path $shortcut) {
    Remove-Item $shortcut -Force
    Write-Host "Removed Startup shortcut."
}

$task = Get-ScheduledTask -TaskName 'Lily' -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName 'Lily' -Confirm:$false
    Write-Host "Removed Scheduled Task 'Lily'."
}

Write-Host "Done."
