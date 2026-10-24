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

# Only the dedicated temporary role may call SSM; never use the source IAM user directly.
$expectedRole = 'arn:aws:iam::393229433969:role/edge-portfolio-admin-local'
$configuredRole = & aws configure get role_arn --profile $Profile
if ($configuredRole -ne $expectedRole) {
    throw 'Use the edge-portfolio-admin profile configured for the dedicated administrator role.'
}

$probe = [System.Net.Sockets.TcpClient]::new()
try {
    $connected = $probe.ConnectAsync('127.0.0.1', 18080)
    try { $null = $connected.Wait(500) } catch {}
    if ($probe.Connected) { throw 'Local port 18080 is already in use. Close the previous tunnel before reconnecting.' }
} finally { $probe.Dispose() }

$identity = & aws sts get-caller-identity --profile $Profile --region $region --output json
if ($LASTEXITCODE -ne 0) { throw 'Unable to assume the administrator role. Check the source profile authentication.' }
if (($identity | ConvertFrom-Json).Arn -notmatch '^arn:aws:sts::393229433969:assumed-role/edge-portfolio-admin-local/[^/]+$') {
    throw 'The resolved AWS identity is not the dedicated administrator role. SSM access stopped.'
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
