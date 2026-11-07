# Runs a command and keeps Windows from idle-sleeping while it runs, so a sweep that takes hours is
# not cut off. It asks for the system to stay awake for as long as this script runs and gives
# that up when the command ends. It changes no setting. A closed lid or a manual sleep still sleeps.
#
#   research\run.ps1 .venv\Scripts\cthulhu.exe sweep --config research\sweep-a.yaml
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Command,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)

Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]1

[void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
try {
    & $Command @Rest
    exit $LASTEXITCODE
}
finally {
    [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS)
}
