# Verification report

## Django suite

Executed on 2026-11-25 with the existing SQLite database retained and Django's isolated test database:

```powershell
$env:DJANGO_DEBUG='1'
$env:EXAM_HOTSPOT_CIDR='127.0.0.0/8'
$env:EXAM_TRUSTED_PROXY_CIDRS='192.0.2.1/32'
$env:TRUST_PROXY_HTTPS='0'
.\.venv\Scripts\python.exe manage.py test --verbosity 1
```

Result: **133 tests ran: 126 passed and 7 skipped.** Django reported no system-check issues. The environment values are temporary test-process settings: they allow test-client loopback requests and do not change the production `.env` or weaken production network validation.

The seven skipped tests exercise SQL row-lock and concurrent-operation behavior that SQLite cannot provide; run them against PostgreSQL or MySQL. They include concurrent starts, answer-version writes, submission, activation-token redemption, and quiz-deletion races. The suite includes existing student dashboard, role, account, QR, network, timer, answer, result and audit behavior, secure complete quiz deletion, approved-network access, missing global hotspot config, forged headers, IPv6, wrong network profile, network switching, denied save/submit off-network, preserved acknowledged answers, reconnection with the same attempt/deadline, configured HTTPS QR origin, and loopback service/database configuration.

Additional executed checks:

| Check | Result |
|---|---|
| `manage.py check` | Passed; no issues reported. |
| `manage.py migrate --check --noinput` | Passed; no pending migration changes reported. |
| PowerShell parser for `Start-Hotspot.ps1`, `Test-HotspotNetwork.ps1` and `HotspotFirewall.ps1` | Passed. |
| `caddy.exe validate --config Caddyfile.hotspot --adapter caddyfile` | Passed. The configuration was formatted; Caddy reported only that the explicit `X-Forwarded-For` overwrite duplicates its default behavior. The explicit overwrite remains part of the reviewed anti-forgery policy. |
| Hotspot adapter check | `Local Area Connection* 10` was Up at `192.168.137.1/24`; selected as the Wi-Fi Direct hotspot. |
| Firewall rule scope | ActiveStore rule `Secure Exam hotspot HTTPS` verified as inbound/allow/enabled TCP 8443, local `192.168.137.1`, remote `192.168.137.0/255.255.255.0` (normalized to `/24`), interface `Local Area Connection* 10`, profile/program `Any`. |
| Broad Python firewall rules | TCP and UDP Public-profile Python rules were disabled. No rule changes were made by this work. |
| Startup listeners | Startup reached Caddy at `192.168.137.1:8443` and Waitress at `127.0.0.1:8001`. No wildcard/IPv6 application or database listeners were reported. The processes were stopped after verification. |
| Local HTTPS | Normal Windows certificate validation succeeded; `https://192.168.137.1:8443/login/` returned HTTP 200 with the CSRF login form. This verifies the laptop only, not a student phone. |
| Remaining firewall rule | The legacy TCP 8000 rule still has `LocalAddress=Any` and `InterfaceAlias=Any`; no TCP 8000 listener was found. It was not modified. |

The startup generated the Caddy config for the selected hotspot address, validated it, and successfully launched both services. The launcher remained active after checking its local HTTPS response and listener scopes. Phone TLS trust, student hotspot access, off-network denial, alternate-route reachability and physical-device tests remain **NOT VERIFIED**.

The seven skipped SQL race tests are listed by name in the test output when run verbosely. Mocked Django requests verify application policy only; they cannot prove physical presence or network isolation. Complete [NETWORK_ACCEPTANCE_TEST.md](NETWORK_ACCEPTANCE_TEST.md) on the classroom laptop and student devices before a live exam.
