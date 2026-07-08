$t = Get-ScheduledTask -TaskName 'Stocke-MorningServerWatch' -ErrorAction SilentlyContinue
if (-not $t) {
    Write-Host 'NOT_REGISTERED'
    exit 1
}
$info = Get-ScheduledTaskInfo -TaskName 'Stocke-MorningServerWatch'
Write-Host "State=$($t.State)"
Write-Host "NextRun=$($info.NextRunTime)"
Write-Host "LastRun=$($info.LastRunTime)"
Write-Host "LastResult=$($info.LastTaskResult)"
foreach ($tr in $t.Triggers) {
    Write-Host "Trigger Start=$($tr.StartBoundary) Days=$($tr.DaysOfWeek) Enabled=$($tr.Enabled)"
}
