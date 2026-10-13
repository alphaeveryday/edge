# Local contract test: mock AWS and the browser helper, never contact AWS or open a tunnel.
$ErrorActionPreference = 'Stop'
$global:adminTestCalls = [System.Collections.Generic.List[string]]::new()
$global:adminTestSso = $false
$global:adminTestExpired = $false
$global:adminTestMixed = $false
$global:adminTestChain = $false
$global:adminTestAmbiguous = $false
$global:adminTestWebIdentity = $false
$global:adminTestMissingRole = $false
function aws {
    $global:adminTestCalls.Add(($args -join ' '))
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'configure') {
        $profileName = $args[4]
        if ($global:adminTestWebIdentity -and $args[2] -eq 'web_identity_token_file') { return 'test-token-file' }
        if ($global:adminTestChain -and $profileName -eq 'test-role') {
            if ($args[2] -eq 'source_profile') { return 'test-sso' }
            if ($args[2] -eq 'role_arn' -and -not $global:adminTestMissingRole) { return 'arn:aws:iam::393229433969:role/limited-admin' }
            if ($global:adminTestAmbiguous -and $args[2] -eq 'sso_session') { return 'stale-sso-marker' }
            return
        }
        if ($global:adminTestMixed -and $args[2] -eq 'aws_access_key_id') { return 'fake-test-key' }
        if ($global:adminTestSso -and $args[2] -eq 'sso_session') { return 'test-sso' }
        return
    }
    if ($args[0] -eq 'sts') {
        if ($global:adminTestExpired) {
            $global:adminTestExpired = $false
            $global:LASTEXITCODE = 255
            Write-Error 'Test SSO token expired'
            return
        }
        return '{"Arn":"arn:aws:sts::393229433969:assumed-role/limited-admin/test"}'
    }
    if ($args[1] -eq 'get-parameter') {
        return '{"Parameter":{"Value":"{\"instance_id\":\"i-1234567890abcdef0\",\"document\":\"edge-portfolio-admin-tunnel\"}"}}'
    }
}
function session-manager-plugin {}
function Start-Job { return [pscustomobject]@{ Id = 1 } }
function Stop-Job {}
function Remove-Job {}
$launcher = Join-Path $PSScriptRoot 'open-admin.ps1'
try {
    & $launcher -Profile test-static
    throw 'Static credentials were accepted.'
} catch {
    if ($_.Exception.Message -notlike '*SSO-backed restricted*') { throw }
}
if ($global:adminTestCalls | Where-Object { $_ -like 'sts *' -or $_ -like 'ssm *' }) { throw 'Static profile contacted AWS.' }
$global:adminTestCalls.Clear()
$global:adminTestSso = $true
& $launcher -Profile test-sso
if (-not ($global:adminTestCalls | Where-Object { $_ -eq 'ssm start-session --target i-1234567890abcdef0 --document-name edge-portfolio-admin-tunnel --profile test-sso --region ap-northeast-2' })) {
    throw 'Launcher did not use the fixed administrator session document.'
}
Write-Output 'Launcher contract passed: no static-key fallback; explicit SSO profile and fixed SSM document.'
$global:adminTestCalls.Clear()
$global:adminTestExpired = $true
& $launcher -Profile test-sso
if (-not ($global:adminTestCalls | Where-Object { $_ -eq 'sso login --profile test-sso' })) {
    throw 'Expired SSO session did not start an interactive SSO login.'
}
Write-Output 'Expired SSO credentials trigger login before opening the tunnel.'

foreach ($chain in @($false, $true)) {
    $global:adminTestCalls.Clear()
    $global:adminTestMixed = $true
    $global:adminTestChain = $chain
    $testProfile = if ($chain) { 'test-role' } else { 'test-sso' }
    try {
        & $launcher -Profile $testProfile
        throw 'Mixed SSO and static credentials were accepted.'
    } catch {
        if ($_.Exception.Message -notlike '*without static credentials*') { throw }
        if ($_.Exception.Message -like '*fake-test-key*') { throw 'Credential value leaked.' }
    }
    if ($global:adminTestCalls | Where-Object { $_ -like 'sts *' -or $_ -like 'ssm *' }) { throw 'Mixed credentials contacted AWS.' }
}
$global:adminTestMixed = $false
$global:adminTestCalls.Clear()
& $launcher -Profile test-role
if (-not ($global:adminTestCalls | Where-Object { $_ -like 'ssm start-session *--profile test-role *' })) { throw 'SSO-backed role chain failed.' }
Write-Output 'Mixed static/SSO profiles are rejected throughout the role chain; clean SSO chains work.'

$global:adminTestCalls.Clear()
$global:adminTestAmbiguous = $true
try {
    & $launcher -Profile test-role
    throw 'Ambiguous SSO/AssumeRole profile was accepted.'
} catch {
    if ($_.Exception.Message -notlike '*unambiguous SSO profile*') { throw }
}
if ($global:adminTestCalls | Where-Object { $_ -like 'sts *' -or $_ -like 'ssm *' }) { throw 'Ambiguous profile contacted AWS.' }
$global:adminTestCalls.Clear()
$global:adminTestWebIdentity = $true
try {
    & $launcher -Profile test-sso
    throw 'Web identity combined with SSO was accepted.'
} catch {
    if ($_.Exception.Message -notlike '*alternate credential providers*') { throw }
}
if ($global:adminTestCalls | Where-Object { $_ -like 'sts *' -or $_ -like 'ssm *' }) { throw 'Web identity contacted AWS.' }
Write-Output 'Conflicting SSO/AssumeRole and web identity configurations fail before AWS access.'

$global:adminTestCalls.Clear()
$global:adminTestAmbiguous = $false
$global:adminTestWebIdentity = $false
$global:adminTestMissingRole = $true
try {
    & $launcher -Profile test-role
    throw 'source_profile without role_arn was accepted.'
} catch {
    if ($_.Exception.Message -notlike '*requires role_arn*') { throw }
}
if ($global:adminTestCalls | Where-Object { $_ -like 'sts *' -or $_ -like 'ssm *' }) { throw 'Invalid role chain contacted AWS.' }
Write-Output 'source_profile without role_arn cannot fall back to non-SSO credentials.'
