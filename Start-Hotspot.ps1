param([int]$HotspotInterfaceIndex = 0)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$caddy = Get-Command caddy.exe -ErrorAction SilentlyContinue
if (-not $caddy -and (Test-Path '.\caddy.exe')) { $caddy = Get-Item '.\caddy.exe' }
if (-not $caddy) { throw 'Caddy is missing. Place caddy.exe beside this project or add it to PATH.' }
$caddyPath = if ($caddy.Path) { $caddy.Path } else { $caddy.FullName }
. (Join-Path $PSScriptRoot 'HotspotFirewall.ps1')

$candidates = @()
foreach ($adapter in Get-NetAdapter -IncludeHidden | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -like '*Wi-Fi Direct*' }) {
    foreach ($addr in Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' }) {
        $candidates += [PSCustomObject]@{Name=$adapter.Name;Index=$adapter.ifIndex;IP=$addr.IPAddress;Prefix=$addr.PrefixLength;Description=$adapter.InterfaceDescription}
    }
}
if ($HotspotInterfaceIndex -gt 0) { $candidates = @($candidates | Where-Object Index -eq $HotspotInterfaceIndex) }
if ($candidates.Count -eq 0) { throw 'No usable active Wi-Fi Direct adapter was found. Enable Windows Mobile Hotspot and retry. Upstream Wi-Fi is never selected.' }
if ($candidates.Count -gt 1) {
    Write-Host 'Select the adapter created by Windows Mobile Hotspot:'
    for ($i=0; $i -lt $candidates.Count; $i++) { Write-Host ("[{0}] {1}  {2}/{3}  (ifIndex {4})" -f ($i+1),$candidates[$i].Name,$candidates[$i].IP,$candidates[$i].Prefix,$candidates[$i].Index) }
    $choice = 0
    if (-not [int]::TryParse((Read-Host 'Adapter number'),[ref]$choice) -or $choice -lt 1 -or $choice -gt $candidates.Count) { throw 'No valid adapter selected.' }
    $hotspot = $candidates[$choice-1]
} else { $hotspot = $candidates[0] }
$cidr = ConvertTo-NormalizedFirewallAddress "$($hotspot.IP)/$($hotspot.Prefix)"
if ($cidr -like 'invalid:*') { throw 'Unable to calculate selected hotspot subnet.' }
Write-Host "Selected interface: $($hotspot.Name) (ifIndex $($hotspot.Index))"
Write-Host "Subnet: $cidr    HTTPS: https://$($hotspot.IP):8443"
Write-Host 'Status: CONFIGURED candidate; phone access is NOT VERIFIED.'

& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Test-HotspotNetwork.ps1') -InterfaceIndex $hotspot.Index -Address $hotspot.IP -PrefixLength $hotspot.Prefix
if ($LASTEXITCODE -ne 0) { throw 'Network diagnostics failed.' }
$activeInboundRules = @(Get-SecureExamFirewallRuleSnapshot | Where-Object Direction -eq 'Inbound')
$matchingRules = @()
foreach ($candidateRule in $activeInboundRules) {
    $candidatePort = $candidateRule.PortFilter
    if ($candidatePort.LocalPort -contains '8443') {
        $match = Test-SecureExamHotspotHttpsRule -Rule $candidateRule -Address $hotspot.IP -Network $cidr -InterfaceAlias $hotspot.Name -CaddyPath $caddyPath
        if ($match.Matches) { $matchingRules += [PSCustomObject]@{Rule=$candidateRule;Match=$match} }
    }
}
if ($matchingRules.Count -gt 1) { throw 'Firewall status: NOT VERIFIED. Multiple enabled rules match this exact HTTPS scope; inspect duplicates manually.' }
if ($matchingRules.Count -eq 0) {
    $knownNames = @('Secure Exam Hotspot HTTPS 8443','Secure Exam hotspot HTTPS')
    $namedRules = @($activeInboundRules | Where-Object { $_.DisplayName -in $knownNames })
    if ($namedRules.Count -gt 0) { throw 'Firewall status: NOT VERIFIED. A known Secure Exam HTTPS rule exists but does not exactly match the selected scope; no duplicate was created.' }
    Write-Host 'Firewall status: NOT VERIFIED (scoped allow rule is missing).'
    Write-Host 'The launcher will add only this rule after explicit typed approval. No existing rules will be changed.'
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Test-HotspotNetwork.ps1') -InterfaceIndex $hotspot.Index -Address $hotspot.IP -PrefixLength $hotspot.Prefix -ApplyFirewall
    if ($LASTEXITCODE -ne 0) { throw 'Scoped firewall rule was not applied. Live startup stopped.' }
} else {
    Write-Host ("Firewall status: VERIFIED active rule '{0}' (program={1}, profile={2}); phone reachability still requires testing." -f $matchingRules[0].Rule.DisplayName,$matchingRules[0].Match.Program,$matchingRules[0].Match.Profile)
}

if (-not (Test-Path '.venv\Scripts\python.exe')) { & py -3 -m venv .venv; if ($LASTEXITCODE) { throw 'Python setup failed.' } }
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
function Run-Python { & $python @args; if ($LASTEXITCODE -ne 0) { throw 'Python command failed. Read the error above.' } }
Run-Python @('-m','pip','install','-r','requirements.txt')
Run-Python @('configure.py')
Run-Python @('configure_hotspot.py',$hotspot.IP,[string]$hotspot.Prefix)
Run-Python @('manage.py','migrate')
Run-Python @('configure_hotspot.py',$hotspot.IP,[string]$hotspot.Prefix,'--profile-only')
Run-Python @('manage.py','collectstatic','--noinput')
Run-Python @('manage.py','check','--deploy')
& $caddyPath validate --config Caddyfile.hotspot --adapter caddyfile
if ($LASTEXITCODE -ne 0) { throw 'Caddy configuration failed validation.' }

$server=$null;$worker=$null;$proxy=$null
try {
    $server=Start-Process $python -ArgumentList 'serve_production.py' -WorkingDirectory $PSScriptRoot -PassThru -NoNewWindow
    $worker=Start-Process $python -ArgumentList 'manage.py finalize_exams' -WorkingDirectory $PSScriptRoot -PassThru -NoNewWindow
    $proxy=Start-Process $caddyPath -ArgumentList 'run --config Caddyfile.hotspot --adapter caddyfile' -WorkingDirectory $PSScriptRoot -PassThru -NoNewWindow
    $listener=$null
    for ($attempt=0; $attempt -lt 20 -and -not $listener; $attempt++) {
        $server.Refresh();$worker.Refresh();$proxy.Refresh()
        if ($server.HasExited -or $worker.HasExited -or $proxy.HasExited) { throw 'One or more application processes exited during startup. Status is NOT VERIFIED.' }
        $listener = Get-NetTCPConnection -State Listen -LocalPort 8443 -ErrorAction SilentlyContinue | Where-Object LocalAddress -eq $hotspot.IP
        if (-not $listener) { Start-Sleep -Seconds 1 }
    }
    if ($listener) { Write-Host "Status: RUNNING; HTTPS socket bound to $($hotspot.IP):8443. TLS/browser trust is NOT VERIFIED from a student phone." }
    else { Write-Host 'Status: NOT VERIFIED; expected hotspot HTTPS listener was not found.' }
    if ($listener) {
        try {
            $localResponse=Invoke-WebRequest -Uri "https://$($hotspot.IP):8443/" -UseBasicParsing -TimeoutSec 8
            Write-Host ("Status: VERIFIED LOCALLY (HTTPS returned HTTP {0}; certificate accepted by Windows trust store)." -f [int]$localResponse.StatusCode)
        } catch {
            $httpResponse=$_.Exception.Response
            if ($httpResponse) {
                Write-Host ("Status: VERIFIED LOCALLY (TLS certificate validated; HTTPS returned HTTP {0})." -f [int]$httpResponse.StatusCode)
            } else {
                Write-Host 'Status: NOT VERIFIED LOCALLY (HTTPS connection or certificate validation failed).'
            }
        }
    }
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Test-HotspotNetwork.ps1') -InterfaceIndex $hotspot.Index -Address $hotspot.IP -PrefixLength $hotspot.Prefix
    if ($LASTEXITCODE -ne 0) { Write-Host 'Post-start diagnostic status: NOT VERIFIED.' }
    Write-Host "Teacher URL: https://$($hotspot.IP):8443"
    Write-Host 'Open the teacher dashboard after trusting the Caddy public root certificate. Never bypass browser warnings.'
    Write-Host 'Keep this window open. Stop with Ctrl+C. Phone verification remains NOT VERIFIED until TEST A-I are performed.'
    while (-not $proxy.HasExited -and -not $server.HasExited -and -not $worker.HasExited) {
        Start-Sleep -Seconds 2
        $active=Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object { $_.ifIndex -eq $hotspot.Index }
        $same=Get-NetIPAddress -InterfaceIndex $hotspot.Index -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -eq $hotspot.IP }
        if (-not $active -or $active.Status -ne 'Up' -or -not $same) { throw 'Hotspot disconnected or address changed. Restart after restoring the hotspot.' }
        $proxy.Refresh();$server.Refresh();$worker.Refresh()
    }
} finally {
    foreach ($child in @($proxy,$server,$worker)) { if ($child -and -not $child.HasExited) { Stop-Process -Id $child.Id -ErrorAction SilentlyContinue } }
}
