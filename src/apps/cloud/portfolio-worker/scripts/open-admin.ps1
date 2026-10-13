param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$Profile
)
$ErrorActionPreference = 'Stop'
$region = 'ap-northeast-2'
$url = 'http://localhost:18080/portfolio/admin.html'
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { throw 'AWS CLI v2 is required.' }
if (-not (Get-Command session-manager-plugin -ErrorAction SilentlyContinue)) { throw 'AWS Session Manager plugin is required.' }

# Never fall back to a default/master access key. Require an SSO-backed profile chain.
$source = $Profile
$visited = @{}
while ($true) {
    if ($visited.ContainsKey($source)) { throw 'AWS profile source cycle detected.' }
    $visited[$source] = $true
    foreach ($credentialSetting in @('aws_access_key_id', 'aws_secret_access_key', 'aws_session_token', 'credential_process', 'credential_source', 'web_identity_token_file')) {
        $credentialValue = & aws configure get $credentialSetting --profile $source
        if ($credentialValue) { throw 'Use an SSO-backed restricted admin profile without static credentials or alternate credential providers.' }
    }
    $sso = & aws configure get sso_session --profile $source
    $legacySso = & aws configure get sso_start_url --profile $source
    $nextSource = & aws configure get source_profile --profile $source
    $role = & aws configure get role_arn --profile $source
    if ($sso -or $legacySso) {
        if ($nextSource -or $role) { throw 'Use an unambiguous SSO profile; do not mix SSO metadata with AssumeRole settings.' }
        break
    }
    if ($nextSource -and -not $role) { throw 'A non-SSO source_profile requires role_arn; credential fallback is not allowed.' }
    $source = $nextSource
    if (-not $source) { throw 'Use an SSO-backed restricted admin profile. Static/default access keys are not used.' }
}

$probe = [System.Net.Sockets.TcpClient]::new()
try {
    $connected = $probe.ConnectAsync('127.0.0.1', 18080)
    try { $null = $connected.Wait(500) } catch {}
    if ($probe.Connected) { throw 'Local port 18080 is already in use. Close the previous tunnel before reconnecting.' }
} finally { $probe.Dispose() }

$savedErrorPreference = $ErrorActionPreference
try {
    # Windows PowerShell treats native stderr as an error; an expired SSO token is recoverable.
    $ErrorActionPreference = 'Continue'
    $identity = & aws sts get-caller-identity --profile $Profile --region $region --output json 2>$null
    $identityExit = $LASTEXITCODE
} finally { $ErrorActionPreference = $savedErrorPreference }
if ($identityExit -ne 0) {
    & aws sso login --profile $source
    if ($LASTEXITCODE -ne 0) { throw 'AWS SSO login failed.' }
    $identity = & aws sts get-caller-identity --profile $Profile --region $region --output json
    if ($LASTEXITCODE -ne 0) { throw 'AWS identity verification failed.' }
}
if (($identity | ConvertFrom-Json).Arn -notmatch '^arn:aws:sts::393229433969:assumed-role/') {
    throw 'Use an assumed role in the EDGE account, not an IAM user or root credential.'
}
$raw = & aws ssm get-parameter --name /edge/portfolio/admin-connection --profile $Profile --region $region --output json
if ($LASTEXITCODE -ne 0) { throw 'The profile needs the portfolio administrator tunnel policy.' }
$config = ($raw | ConvertFrom-Json).Parameter.Value | ConvertFrom-Json
if ($config.document -ne 'edge-portfolio-admin-tunnel' -or $config.instance_id -notmatch '^i-[a-f0-9]+$') { throw 'Unexpected administrator connection configuration.' }

# The foreground session owns the tunnel lifetime; the helper only opens the browser.
$browser = Start-Job -ArgumentList $url -ScriptBlock {
    param($url)
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri 'http://localhost:18080/health' -TimeoutSec 2
            if ($health.service -eq 'orca-admin-ssm') { Start-Process $url; return }
        } catch {}
        Start-Sleep -Seconds 1
    }
}
Write-Host 'Connecting through SSM. The browser opens when ready. Press Ctrl+C to disconnect.'
try {
    & aws ssm start-session --target $config.instance_id --document-name edge-portfolio-admin-tunnel --profile $Profile --region $region
    if ($LASTEXITCODE -ne 0) { throw 'SSM tunnel failed. Check the restricted role permissions and instance connection.' }
} finally {
    Stop-Job $browser
    Remove-Job $browser
}
