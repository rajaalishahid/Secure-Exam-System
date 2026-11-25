function ConvertTo-FirewallIPv4 {
    param([string]$Value)

    $ip = $null
    if ($Value -match '^(0|[1-9][0-9]{0,2})(\.(0|[1-9][0-9]{0,2})){3}$' -and
        [Net.IPAddress]::TryParse($Value, [ref]$ip)) {
        return $ip
    }
}

function ConvertTo-NormalizedFirewallAddress {
    param([Parameter(Mandatory=$true)][AllowEmptyString()][string]$Value)

    $value = $Value.Trim()
    $parts = $value -split '[/\-]'
    $ip = ConvertTo-FirewallIPv4 $parts[0]
    if (-not $ip -or $parts.Count -gt 2) { return "invalid:$value" }
    if ($parts.Count -eq 1) { return $ip.ToString() }
    if ($value.Contains('-')) {
        if ($parts[1] -eq $ip.ToString()) { return $ip.ToString() }
        return "invalid:$value"
    }

    if ($parts[1] -match '^([0-9]|[12][0-9]|3[0-2])$') {
        $prefix = [int]$parts[1]
        $maskBits = ('1' * $prefix) + ('0' * (32 - $prefix))
    } else {
        $mask = ConvertTo-FirewallIPv4 $parts[1]
        if (-not $mask) { return "invalid:$value" }
        $maskBits = ($mask.GetAddressBytes() | ForEach-Object {
            [Convert]::ToString($_, 2).PadLeft(8, '0')
        }) -join ''
        if ($maskBits -notmatch '^1*0*$') { return "invalid:$value" }
        $prefix = $maskBits.TrimEnd('0').Length
    }
    if ($prefix -eq 32) { return $ip.ToString() }

    $bytes = $ip.GetAddressBytes()
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        $bytes[$i] = $bytes[$i] -band [Convert]::ToByte($maskBits.Substring($i * 8, 8), 2)
    }
    return "$($bytes -join '.')/$prefix"
}

function ConvertFrom-FirewallCsv {
    param(
        $Value,
        [string]$EmptyValue = 'Any'
    )

    $values = @(
        foreach ($item in @($Value)) {
            foreach ($part in ([string]$item -split ',')) {
                $normalized = $part.Trim()
                if ($normalized) {
                    if ($normalized -eq '*') { 'Any' } else { $normalized }
                }
            }
        }
    )
    if ($values.Count -eq 0) { return @($EmptyValue) }
    return $values
}

function ConvertFrom-FirewallProfiles {
    param([int]$Profiles)

    if ($Profiles -eq 0x7fffffff) { return 'Any' }
    $names = @()
    if ($Profiles -band 1) { $names += 'Domain' }
    if ($Profiles -band 2) { $names += 'Private' }
    if ($Profiles -band 4) { $names += 'Public' }
    if ($names.Count -eq 0) { return 'NotConfigured' }
    return ($names -join ', ')
}

function Get-SecureExamFirewallRuleSnapshot {
    <# Read the effective firewall policy once through the Windows Firewall API.
       This avoids thousands of per-rule CIM association queries. #>
    try {
        $policy = New-Object -ComObject HNetCfg.FwPolicy2 -ErrorAction Stop
        $rules = @($policy.Rules)
    } catch {
        throw "Unable to read the effective Windows Firewall policy: $($_.Exception.Message)"
    }

    foreach ($rule in $rules) {
        try {
            $protocol = switch ([int]$rule.Protocol) {
                6 { 'TCP' }
                17 { 'UDP' }
                256 { 'Any' }
                default { [string]$rule.Protocol }
            }
            $interfaces = @(
                $rule.Interfaces |
                    ForEach-Object { ([string]$_).Trim() } |
                    Where-Object { $_ }
            )
            if ($interfaces.Count -eq 0) { $interfaces = @('Any') }
            $program = [string]$rule.ApplicationName
            if (-not $program) { $program = 'Any' }
            [PSCustomObject]@{
                Name = [string]$rule.Name
                DisplayName = [string]$rule.Name
                Enabled = [string][bool]$rule.Enabled
                Direction = if ([int]$rule.Direction -eq 1) { 'Inbound' } else { 'Outbound' }
                Action = if ([int]$rule.Action -eq 1) { 'Allow' } else { 'Block' }
                Profile = ConvertFrom-FirewallProfiles ([int]$rule.Profiles)
                PortFilter = [PSCustomObject]@{
                    Protocol = $protocol
                    LocalPort = @(ConvertFrom-FirewallCsv $rule.LocalPorts)
                    RemotePort = @(ConvertFrom-FirewallCsv $rule.RemotePorts)
                }
                AddressFilter = [PSCustomObject]@{
                    LocalAddress = @(ConvertFrom-FirewallCsv $rule.LocalAddresses)
                    RemoteAddress = @(ConvertFrom-FirewallCsv $rule.RemoteAddresses)
                }
                InterfaceFilter = [PSCustomObject]@{ InterfaceAlias = $interfaces }
                ApplicationFilter = [PSCustomObject]@{ Program = $program }
            }
        } catch {
            throw "Unable to inspect firewall rule '$([string]$rule.Name)': $($_.Exception.Message)"
        }
    }
}

function Test-SecureExamHotspotHttpsRule {
    param(
        [Parameter(Mandatory=$true)]$Rule,
        [Parameter(Mandatory=$true)][string]$Address,
        [Parameter(Mandatory=$true)][string]$Network,
        [Parameter(Mandatory=$true)][string]$InterfaceAlias,
        [string]$CaddyPath = ''
    )
    $reasons = [System.Collections.Generic.List[string]]::new()
    if ($Rule.Enabled -ne 'True') { $reasons.Add('disabled') }
    if ($Rule.Direction -ne 'Inbound') { $reasons.Add('direction is not inbound') }
    if ($Rule.Action -ne 'Allow') { $reasons.Add('action is not allow') }

    $port = if ($Rule.PSObject.Properties['PortFilter']) { $Rule.PortFilter } else { $Rule | Get-NetFirewallPortFilter -ErrorAction Stop }
    if ($port.Protocol -notin @('TCP','6')) { $reasons.Add('protocol is not TCP') }
    $localPorts = @($port.LocalPort | ForEach-Object { [string]$_ })
    if ($localPorts.Count -ne 1 -or $localPorts[0] -ne '8443') { $reasons.Add('local port is not exactly 8443') }
    $remotePorts = @($port.RemotePort | ForEach-Object { [string]$_ })
    if ($remotePorts.Count -ne 1 -or $remotePorts[0] -ne 'Any') { $reasons.Add('remote port filter is not Any') }

    $addresses = if ($Rule.PSObject.Properties['AddressFilter']) { $Rule.AddressFilter } else { $Rule | Get-NetFirewallAddressFilter -ErrorAction Stop }
    $interfaces = if ($Rule.PSObject.Properties['InterfaceFilter']) { $Rule.InterfaceFilter } else { $Rule | Get-NetFirewallInterfaceFilter -ErrorAction Stop }
    $actualLocal = @($addresses.LocalAddress | ForEach-Object { ConvertTo-NormalizedFirewallAddress ([string]$_) } | Sort-Object)
    $expectedLocal = @(ConvertTo-NormalizedFirewallAddress $Address)
    if ($actualLocal.Count -ne 1 -or $actualLocal[0] -cne $expectedLocal[0]) { $reasons.Add('local address does not exactly match selected hotspot IP') }
    $actualRemote = @($addresses.RemoteAddress | ForEach-Object { ConvertTo-NormalizedFirewallAddress ([string]$_) } | Sort-Object)
    $expectedRemote = @(ConvertTo-NormalizedFirewallAddress $Network)
    if ($actualRemote.Count -ne 1 -or $actualRemote[0] -cne $expectedRemote[0]) { $reasons.Add('remote subnet does not exactly match selected hotspot subnet') }
    $actualInterfaces = @($interfaces.InterfaceAlias | ForEach-Object { ([string]$_).Trim().ToLowerInvariant() } | Sort-Object)
    $expectedInterface = @($InterfaceAlias.Trim().ToLowerInvariant())
    if ($actualInterfaces.Count -ne 1 -or $actualInterfaces[0] -cne $expectedInterface[0]) { $reasons.Add('interface does not exactly match selected hotspot adapter') }

    $profiles = @($Rule.Profile | ForEach-Object { [string]$_ })
    if ($profiles.Count -eq 0 -or $profiles -contains 'NotConfigured') { $reasons.Add('firewall profile is not configured') }
    $application = if ($Rule.PSObject.Properties['ApplicationFilter']) { $Rule.ApplicationFilter } else { $Rule | Get-NetFirewallApplicationFilter -ErrorAction Stop }
    $program = [string]$application.Program
    if ($program -ne 'Any') {
        $matchesProgram = $false
        if ($program -and $CaddyPath) {
            try {
                $matchesProgram = [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($program)) -eq [IO.Path]::GetFullPath($CaddyPath)
            } catch [ArgumentException] {}
        }
        if (-not $matchesProgram) { $reasons.Add('program is neither Any nor the selected Caddy executable') }
    }

    [PSCustomObject]@{Matches=($reasons.Count -eq 0);Reasons=$reasons.ToArray();Program=$program;Profile=($profiles -join ',')}
}
