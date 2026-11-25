# Secure Exam System network acceptance

This procedure separates configuration, process startup, local verification and phone verification. Do not label the system ready because a configuration file exists. Record each result as **PASS**, **FAIL**, or **NOT VERIFIED**, with date, adapter name, address and tester.

## Before the classroom test

1. Enable Windows Mobile Hotspot and connect one enrolled student phone to it.
2. Start the system using `Start-Hotspot.cmd` (right-click and choose **Run as administrator** only if you need to create the scoped firewall rule). Select the adapter Windows created for Mobile Hotspot if prompted. Never select the upstream Wi-Fi/Ethernet adapter.
3. Review the diagnostic output. It must show the selected Wi-Fi Direct interface, its current subnet and TCP 8443. Any broad or conflicting rule, wildcard listener, IPv6 listener or failed firewall query is **NOT VERIFIED** until resolved.
4. If the scoped rule is absent, the launcher previews the exact single rule and requires typing `APPLY FIREWALL`. It does not delete existing rules or add block rules to other adapters. Roll back only this rule with an elevated PowerShell:

   ```powershell
   .\Test-HotspotNetwork.ps1 -InterfaceIndex <index> -Address <hotspot-ip> -PrefixLength <prefix> -Rollback
   ```

5. The launcher reports a running socket only after checking the selected IPv4 address. This does not prove TLS trust or reachability from a student device. Install Caddy's **public** root certificate through normal device trust settings before testing. Never bypass a certificate warning and never copy `root.key` to a student device.

## Acceptance matrix

| Test | Procedure | Expected result | Result |
|---|---|---|---|
| A | On the teacher laptop, inspect the launcher output and `Get-NetTCPConnection -State Listen -LocalPort 8443`. | A TCP listener exists only on the selected hotspot IPv4 address. Status is **RUNNING**; TLS remains separately checked. | NOT VERIFIED here |
| B | Connect the student phone to the teacher laptop hotspot, open the printed `https://<selected-ip>:8443` URL after trusting the public CA root, scan the QR, log in as an enrolled student and start. | Browser validates the certificate; the enrolled student starts the single attempt. | NOT VERIFIED here |
| C | Switch the same phone to mobile data and reopen the URL. | The classroom server is unreachable over the normal off-network route. | NOT VERIFIED here |
| D | Connect the phone to a different Wi-Fi and reopen the URL. | The classroom server is unreachable over that network. | NOT VERIFIED here |
| E | Send the QR to an off-network device and try opening it. | The QR contains the selected hotspot HTTPS address and gives no normal off-network route to the server. | NOT VERIFIED here |
| F | During an attempt, disconnect the phone from the hotspot and try saving and submitting. | Exam operations are denied; previously acknowledged answers remain stored and the original 900-second deadline continues. | NOT VERIFIED here |
| G | Reconnect before the original deadline and enter the exam again. | The existing attempt and answers resume with remaining time; no second attempt or timer reset is created. | NOT VERIFIED here |
| H | Keep the teacher laptop on upstream Wi-Fi/Ethernet while hotspot is active. From a second client on that upstream network, try the exam address. | Upstream interfaces do not expose the exam service. | NOT VERIFIED here |
| I | Where feasible, inspect IPv6 and alternate ports; repeat with VPN, bridging and routing configurations. | No unauthorized exposure is found. Any untested path remains **NOT VERIFIED**. | NOT VERIFIED here |

Tests B through I require the target laptop, actual phone/second client, and in some cases administrator access and control of upstream network/VPN settings. Django request tests do not substitute for these tests. Record each manual result; a failed or untested test must not be reported as passed.

## Current environment inspection (2026-11-21)

At the latest check, Windows Mobile Hotspot was active on `Local Area Connection* 10` at `192.168.137.1/24`. The active `Secure Exam hotspot HTTPS` rule matched exactly after normalizing the dotted subnet mask to `/24`. Startup reached Caddy on `192.168.137.1:8443` and Waitress on `127.0.0.1:8001`; a local HTTPS request passed normal certificate validation and returned HTTP 301. Those processes were stopped after verification, so the ports are no longer listening. Both broad Public-profile `python.exe` firewall rules were disabled. The old TCP 8000 rule still allows `LocalAddress=Any` and `InterfaceAlias=Any`, but no listener was found on that port; this rule was left unchanged. Other stale rules remain for `192.168.100.19:8443` on Wi-Fi and `172.17.250.216:8080` on Wi-Fi. No smartphone or off-network physical acceptance test was performed.

This project only restricts ordinary network paths. It does not prove physical presence against VPN relays, remote control, a second device connected to the hotspot, bridging or other authorized-device forwarding. Use network infrastructure controls when stronger isolation is required.
