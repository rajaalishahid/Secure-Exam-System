param(
    [Parameter(Mandatory=$true)][int]$InterfaceIndex,
    [Parameter(Mandatory=$true)][string]$Address,
    [Parameter(Mandatory=$true)][ValidateRange(1,32)][int]$PrefixLength,
    [switch]$ApplyFirewall,
    [switch]$Rollback
)
$ErrorActionPreference = 'Continue'
$ruleName = 'Secure Exam Hotspot HTTPS 8443'
$ruleGroup = 'Secure Exam Hotspot'

trap {
    Write-Host ("FINAL RESULT: FAIL - unexpected diagnostic error: {0}" -f $_.Exception.Message)
    exit 10
}

. (Join-Path $PSScriptRoot 'HotspotFirewall.ps1')

function Test-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$adapter = Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object ifIndex -eq $InterfaceIndex
$assignedAddress = Get-NetIPAddress -InterfaceIndex $InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object IPAddress -eq $Address | Select-Object -First 1
$targetReady = $adapter -and $adapter.Status -eq 'Up' -and $adapter.InterfaceDescription -like '*Wi-Fi Direct*' -and $assignedAddress -and $Address -notlike '169.254.*' -and $Address -notlike '127.*' -and $PrefixLength -ge 16 -and $PrefixLength -le 30
$cidr = ConvertTo-NormalizedFirewallAddress "$Address/$PrefixLength"
if ($cidr -like 'invalid:*') { throw 'Unable to calculate selected hotspot subnet.' }
$unsafeListenerFound = $false
$broadPythonAllowFound = $false
$firewallQueryVerified = $false
$listenerQueryVerified = $false
$firewallProfilesVerified = $false
$firewallProfilesEnabled = $false
$matchingFirewallRules = @()
$knownRuleNames = @($ruleName,'Secure Exam hotspot HTTPS')
$existingSecureExamRuleFound = $false
$duplicateMatchingRulesFound = $false
$caddyCommand = Get-Command caddy.exe -ErrorAction SilentlyContinue
$caddyPath = if ($caddyCommand) { $caddyCommand.Path } elseif (Test-Path (Join-Path $PSScriptRoot 'caddy.exe')) { Join-Path $PSScriptRoot 'caddy.exe' } else { '' }
Write-Host 'Secure Exam hotspot network diagnostics'
Write-Host ("Selected interface: {0} (ifIndex {1}); status: {2}" -f $adapter.Name,$InterfaceIndex,$adapter.Status)
Write-Host "Selected address/subnet: $Address/$PrefixLength; $cidr"
if ($targetReady) { Write-Host 'Selected hotspot target: VERIFIED current Wi-Fi Direct IPv4 assignment.' }
else { Write-Host 'Selected hotspot target: NOT VERIFIED (must be an active Wi-Fi Direct adapter with its assigned private IPv4 address and valid prefix).' }
Write-Host 'Configured HTTPS port: TCP 8443'
Write-Host 'Django/Waitress target: 127.0.0.1:8001; database: local Django configuration'
Write-Host '[1/4] Hotspot adapter and address inspection complete.'

try {
    Write-Host '[2/4] Reading effective Windows Firewall policy (single-pass inspection)...'
    $firewallTimer = [Diagnostics.Stopwatch]::StartNew()
    $rules = @(Get-SecureExamFirewallRuleSnapshot)
    if ($rules.Count -eq 0) { throw 'The effective Windows Firewall policy returned no rules.' }
    $relevant = foreach ($r in $rules) {
        $port = $r.PortFilter
        $appFilter = $r.ApplicationFilter
        $knownPort = @($port.LocalPort | ForEach-Object { [string]$_ } | Where-Object { $_ -in @('80','443','8000','8001','8080','8443','3306','5432') })
        $pythonProgram = $appFilter.Program -match '(?i)\\python(?:w)?\.exe$'
        $namedRule = $r.DisplayName -in $knownRuleNames
        if ($namedRule) { $existingSecureExamRuleFound = $true }
        if ($namedRule -or ($r.Direction -eq 'Inbound' -and ($knownPort.Count -gt 0 -or ($pythonProgram -and $port.LocalPort -contains 'Any')))) {
            $addr = $r.AddressFilter
            $iface = $r.InterfaceFilter
            [PSCustomObject]@{Name=$r.DisplayName;Enabled=$r.Enabled;Direction=$r.Direction;Action=$r.Action;Profile=$r.Profile;Program=$appFilter.Program;Protocol=$port.Protocol;LocalPort=($port.LocalPort -join ',');LocalAddress=($addr.LocalAddress -join ',');RemoteAddress=($addr.RemoteAddress -join ',');InterfaceAlias=($iface.InterfaceAlias -join ',')}
            if ($r.Direction -eq 'Inbound' -and $pythonProgram -and $r.Enabled -eq 'True' -and $r.Action -eq 'Allow' -and ($r.Profile -eq 'Any' -or $r.Profile -match 'Public') -and $port.LocalPort -contains 'Any' -and $addr.LocalAddress -contains 'Any' -and $addr.RemoteAddress -contains 'Any' -and $iface.InterfaceAlias -contains 'Any') { $broadPythonAllowFound = $true }
            if ($adapter -and ($namedRule -or $port.LocalPort -contains '8443')) {
                $match = Test-SecureExamHotspotHttpsRule -Rule $r -Address $Address -Network $cidr -InterfaceAlias $adapter.Name -CaddyPath $caddyPath
                if ($match.Matches) { $matchingFirewallRules += [PSCustomObject]@{Rule=$r;Match=$match} }
                elseif ($namedRule) { Write-Host ("Rule '{0}' rejected: {1}." -f $r.DisplayName,($match.Reasons -join '; ')) }
            }
        }
    }
    $firewallTimer.Stop()
    $firewallQueryVerified = $true
    Write-Host ("Firewall policy inspection complete: {0} rules checked in {1:N1} seconds." -f $rules.Count,$firewallTimer.Elapsed.TotalSeconds)
    if ($relevant) { Write-Host 'Relevant active firewall rules (review broad Any scopes as potential conflicts):'; $relevant | Format-List | Out-Host }
    else { Write-Host 'Relevant active firewall rules: none found (intended rule NOT VERIFIED).' }
    if ($matchingFirewallRules.Count -eq 1) { Write-Host ("HTTPS firewall status: VERIFIED exact scope via '{0}' (program={1}; profile={2})." -f $matchingFirewallRules[0].Rule.DisplayName,$matchingFirewallRules[0].Match.Program,$matchingFirewallRules[0].Match.Profile) }
    elseif ($matchingFirewallRules.Count -gt 1) { Write-Host 'HTTPS firewall status: NOT VERIFIED (multiple exact-scope rules are active).'; $duplicateMatchingRulesFound=$true }
    else { Write-Host 'HTTPS firewall status: NOT VERIFIED (no active rule matches the selected IP, subnet, port and interface).' }
} catch {
    $firewallQueryVerified = $false
    Write-Host ("Relevant firewall rules: NOT VERIFIED ({0})." -f $_.Exception.Message)
}

try {
    Write-Host '[3/4] Inspecting application and database listeners...'
    $listeners = Get-NetTCPConnection -State Listen -ErrorAction Stop
    $listenerQueryVerified = $true
    $appListeners = @($listeners | Where-Object LocalPort -in @(80,443,8000,8001,8080,8443,3306,5432))
    if ($appListeners) {
        Write-Host 'Application/database-related listeners:'
        foreach ($l in $appListeners) {
            $owner = Get-Process -Id $l.OwningProcess -ErrorAction SilentlyContinue
            $bound = if ($l.LocalAddress -in @('0.0.0.0','::','::0')) { 'wildcard (potential exposure)' } else {
                $boundAddress=Get-NetIPAddress -IPAddress $l.LocalAddress -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($boundAddress) { $boundAdapter=Get-NetAdapter -InterfaceIndex $boundAddress.InterfaceIndex -ErrorAction SilentlyContinue; "interface $($boundAdapter.Name)" } else { 'loopback or address not mapped to a local adapter' }
            }
            Write-Host ("  {0}:{1} PID {2} ({3}); {4}" -f $l.LocalAddress,$l.LocalPort,$l.OwningProcess,$owner.ProcessName,$bound)
        }
    } else { Write-Host 'Application/database-related listeners: none detected.' }
    if ($listeners | Where-Object { $_.LocalPort -eq 8443 -and $_.LocalAddress -eq $Address }) { Write-Host 'Intended listener: RUNNING on selected hotspot IPv4.' }
    else { Write-Host 'Intended listener: NOT VERIFIED (not currently listening on selected hotspot IPv4).' }
    $unexpected = @($listeners | Where-Object {
        $_.LocalPort -in @(80,443,8000,8001,8080,8443,3306,5432) -and
        -not (($_.LocalPort -eq 8443 -and $_.LocalAddress -eq $Address) -or
              ($_.LocalPort -eq 8001 -and $_.LocalAddress -in @('127.0.0.1','::1')))
    })
    if ($unexpected) { $unsafeListenerFound=$true; Write-Host 'Potential relevant listener on another interface/address: FOUND; review the owner and intended role.' }
    else { Write-Host 'Potential relevant listener on another interface/address: not detected.' }
    $wildcard = @($listeners | Where-Object { $_.LocalPort -in @(80,443,8000,8001,8080,8443,3306,5432) -and $_.LocalAddress -in @('0.0.0.0','::','::0') })
    if ($wildcard) { Write-Host 'Wildcard/IPv6 listener on a relevant application/database port: FOUND; investigate before use.' }
    else { Write-Host 'Wildcard/IPv6 listener on relevant application/database ports: not detected.' }
} catch {
    $listenerQueryVerified = $false
    Write-Host ("Listener and IPv6 exposure checks: NOT VERIFIED ({0})." -f $_.Exception.Message)
}

try {
    Write-Host '[4/4] Checking Windows Firewall profiles...'
    $fwProfile = Get-NetFirewallProfile -ErrorAction Stop
    $firewallProfilesVerified = $true
    $firewallProfilesEnabled = -not ($fwProfile | Where-Object { -not $_.Enabled })
    if ($firewallProfilesEnabled) { Write-Host 'Firewall profile status: enabled.' }
    else { Write-Host 'Firewall profile status: NOT VERIFIED (one or more profiles disabled).' }
} catch {
    $firewallProfilesVerified = $false
    Write-Host ("Firewall profile status: NOT VERIFIED ({0})." -f $_.Exception.Message)
}

if ($Rollback) {
    $existing = @(Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Where-Object Group -eq $ruleGroup)
    if (-not $existing) { Write-Host 'Rollback: exact project rule not found; nothing changed.'; Write-Host 'FINAL RESULT: PASS - rollback had nothing to remove.'; exit 0 }
    if ($existing.Count -ne 1) { Write-Error 'Rollback refused: multiple matching project rules exist; inspect manually.'; Write-Host 'FINAL RESULT: FAIL - rollback target was ambiguous.'; exit 2 }
    if (-not (Test-Administrator)) { Write-Error 'Rollback requires an elevated PowerShell.'; Write-Host 'FINAL RESULT: FAIL - administrator rights are required for rollback.'; exit 2 }
    if ((Read-Host "Type REMOVE $ruleName to remove this exact rule") -cne "REMOVE $ruleName") { Write-Host 'Rollback cancelled.'; Write-Host 'FINAL RESULT: FAIL - rollback was not approved.'; exit 1 }
    $existing | Remove-NetFirewallRule
    Write-Host 'Rollback complete: removed only the named Secure Exam rule.'
    Write-Host 'FINAL RESULT: PASS - rollback completed.'
    exit 0
}

if (-not $firewallQueryVerified -or -not $listenerQueryVerified -or -not $firewallProfilesVerified) {
    Write-Host 'Startup blocked: firewall, profile or listener state is NOT VERIFIED. Resolve the diagnostic query failure before continuing.'
    Write-Host 'FINAL RESULT: FAIL - required security state could not be inspected.'
    exit 5
}

if (-not $targetReady) {
    Write-Host 'Startup blocked: the selected hotspot adapter and address are not verified.'
    Write-Host 'FINAL RESULT: FAIL - hotspot target validation failed.'
    exit 4
}

if (-not $firewallProfilesEnabled) {
    Write-Host 'Startup blocked: one or more Windows Firewall profiles are disabled.'
    Write-Host 'FINAL RESULT: FAIL - Windows Firewall profile protection is incomplete.'
    exit 3
}

if ($unsafeListenerFound -or $broadPythonAllowFound -or $duplicateMatchingRulesFound) {
    if ($unsafeListenerFound) { Write-Host 'Startup blocked: close or safely reconfigure the unexpected wildcard/other-interface service listener, then rerun diagnostics.' }
    if ($broadPythonAllowFound) { Write-Host 'Startup blocked: review and manually narrow the existing broad Public-profile Python firewall rule. This diagnostic will not change it.' }
    if ($duplicateMatchingRulesFound) { Write-Host 'Startup blocked: multiple rules match the exact HTTPS scope; resolve the duplicate rules manually.' }
    Write-Host 'FINAL RESULT: FAIL - unsafe or ambiguous network exposure was detected.'
    exit 3
}

if ($ApplyFirewall -and $matchingFirewallRules.Count -eq 1) {
    Write-Host 'The selected HTTPS firewall scope is already verified. No firewall rule was created.'
    Write-Host 'FINAL RESULT: PASS - hotspot network security checks passed.'
    exit 0
}

if (-not $ApplyFirewall -and $matchingFirewallRules.Count -ne 1) {
    Write-Host 'Startup blocked: exactly one active HTTPS firewall rule must match the selected hotspot scope.'
    Write-Host 'FINAL RESULT: FAIL - scoped HTTPS firewall rule is not verified.'
    exit 3
}

if ($ApplyFirewall) {
    if (-not (Test-Administrator)) { Write-Error 'Firewall application requires an elevated PowerShell.'; Write-Host 'FINAL RESULT: FAIL - administrator rights are required for firewall changes.'; exit 2 }
    $existing = @(Get-NetFirewallRule -DisplayName $knownRuleNames -ErrorAction SilentlyContinue)
    if ($existing -or $existingSecureExamRuleFound) { Write-Error 'A Secure Exam HTTPS rule already exists but did not match this scope. No duplicate rule was created; inspect it manually.'; Write-Host 'FINAL RESULT: FAIL - an existing named rule requires manual review.'; exit 2 }
    Write-Host "Preview: add inbound TCP 8443 allow; local $Address; remote $cidr; interface $($adapter.Name); group '$ruleGroup'."
    if ((Read-Host 'Type APPLY FIREWALL to create this one rule') -cne 'APPLY FIREWALL') { Write-Host 'No firewall changes made.'; Write-Host 'FINAL RESULT: FAIL - firewall creation was not approved.'; exit 1 }
    try {
        New-NetFirewallRule -DisplayName $ruleName -Group $ruleGroup -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 -LocalAddress $Address -RemoteAddress $cidr -InterfaceAlias $adapter.Name -Profile Any -ErrorAction Stop | Out-Null
    } catch { Write-Error "Could not create the one scoped firewall rule: $($_.Exception.Message)"; Write-Host 'FINAL RESULT: FAIL - scoped firewall rule creation failed.'; exit 2 }
    Write-Host 'Created only the scoped rule. Verify it in Windows Defender Firewall; rollback with -Rollback.'
    Write-Host 'FINAL RESULT: PASS - approved scoped rule was created.'
    exit 0
}

Write-Host 'FINAL RESULT: PASS - hotspot network security checks passed.'
exit 0
