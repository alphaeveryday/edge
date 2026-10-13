# Mock AWS and browser helpers. Never contact AWS or open a tunnel.
$ErrorActionPreference = 'Stop'
$global:adminTestCalls = [System.Collections.Generic.List[string]]::new()
$global:adminTestConfiguredRole = 'arn:aws:iam::393229433969:role/edge-portfolio-admin-local'
$global:adminTestIdentity = 'arn:aws:sts::393229433969:assumed-role/edge-portfolio-admin-local/test'
$global:adminTestIdentityFails = $false
function aws {
    $global:adminTestCalls.Add(($args -join ' '))
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'configure') { return $global:adminTestConfiguredRole }
    if ($args[0] -eq 'sts') {
        if ($global:adminTestIdentityFails) { $global:LASTEXITCODE = 255; return }
        return (@{Arn=$global:adminTestIdentity} | ConvertTo-Json)
    }
    if ($args[1] -eq 'get-parameter') {
        return (@{Parameter=@{Value=(@{instance_id='i-1234567890abcdef0';document='edge-portfolio-admin-tunnel'} | ConvertTo-Json -Compress)}} | ConvertTo-Json)
    }
}
function session-manager-plugin {}
function Start-Job { return [pscustomobject]@{ Id = 1 } }
function Stop-Job {}
function Remove-Job {}
$launcher = Join-Path $PSScriptRoot 'open-admin.ps1'
& $launcher -Profile edge-portfolio-admin
if (-not ($global:adminTestCalls | Where-Object { $_ -eq 'ssm start-session --target i-1234567890abcdef0 --document-name edge-portfolio-admin-tunnel --profile edge-portfolio-admin --region ap-northeast-2' })) { throw 'Fixed tunnel/profile was not used.' }
foreach ($role in @('', 'arn:aws:iam::393229433969:role/Administrator')) {
    $global:adminTestCalls.Clear()
    $global:adminTestConfiguredRole = $role
    try { & $launcher -Profile work; throw 'Unsafe profile accepted.' }
    catch { if ($_.Exception.Message -notlike '*configured for the dedicated*') { throw } }
    if ($global:adminTestCalls | Where-Object { $_ -like 'sts *' -or $_ -like 'ssm *' }) { throw 'Unsafe profile contacted AWS.' }
}
$global:adminTestConfiguredRole = 'arn:aws:iam::393229433969:role/edge-portfolio-admin-local'
foreach ($identity in @('arn:aws:iam::393229433969:user/test', 'arn:aws:sts::393229433969:assumed-role/Administrator/test', 'arn:aws:sts::111111111111:assumed-role/edge-portfolio-admin-local/test')) {
    $global:adminTestCalls.Clear()
    $global:adminTestIdentity = $identity
    try { & $launcher -Profile edge-portfolio-admin; throw 'Wrong identity accepted.' }
    catch { if ($_.Exception.Message -notlike '*not the dedicated administrator role*') { throw } }
    if ($global:adminTestCalls | Where-Object { $_ -like 'ssm *' }) { throw 'Wrong identity accessed SSM.' }
}
$global:adminTestCalls.Clear()
$global:adminTestIdentityFails = $true
try { & $launcher -Profile edge-portfolio-admin; throw 'Failed authentication accepted.' }
catch { if ($_.Exception.Message -notlike '*Unable to assume*') { throw } }
if ($global:adminTestCalls | Where-Object { $_ -like 'ssm *' }) { throw 'Failed authentication accessed SSM.' }
Write-Output 'Launcher contracts passed: fixed role and tunnel, no IAM-user/wrong-role/failure fallback.'
