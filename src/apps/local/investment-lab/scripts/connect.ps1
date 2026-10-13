$ErrorActionPreference = 'Stop'
# Only the local port is configurable in this existing fixed-destination document.
# The research agent never receives this profile or a shell tool.
aws ssm start-session --profile edge-research-ro --region ap-northeast-2 `
    --target i-0ba627536f36993d5 `
    --document-name EdgeResearchPostgresReadOnlyTunnel `
    --parameters localPortNumber=15435
if ($LASTEXITCODE -ne 0) { throw 'Research SSM tunnel failed' }
