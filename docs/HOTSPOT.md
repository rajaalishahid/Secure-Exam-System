# Laptop hotspot only — Windows setup

## What this mode does
Your laptop can receive internet from any Wi-Fi/Ethernet network. Windows Mobile hotspot creates a separate Wi-Fi network for students. Only students connected to **the laptop hotspot** should reach the exam service. The setup identifies the Wi-Fi Direct adapter rather than using the main Wi-Fi adapter or assuming a fixed address.

The HTTPS listener binds to the detected hotspot IPv4 address on TCP 8443. Waitress is loopback-only on port 8001. The generated Caddy config and Django both check the hotspot subnet; Django also restricts which exam network profiles can be selected. The launcher diagnoses relevant firewall rules and listeners before startup. If the scoped HTTPS allow rule is missing, it previews one exact rule and requires typed approval. It does not remove existing rules, add broad blocks to other adapters, or change weak-host interface settings. Review existing broad firewall rules and resolve conflicts separately. IPv6 and HTTP redirect listeners are not opened by the generated Caddy configuration. No access logging of bearer URLs is enabled.

These are defense-in-depth network controls, not proof of a student's physical location. An IP/CIDR match only identifies the source address presented to the application. The intended claim—that mobile-data, home-Wi-Fi and upstream-Wi-Fi clients cannot reach the exam service—must be verified on the actual laptop and network before a classroom demo.

## First use
1. On Windows, open **Settings → Network & Internet → Mobile hotspot**. Select your upstream internet connection, choose **Share over Wi-Fi**, set a hotspot name/password and turn it on. Keep it on throughout the exam. Disable automatic hotspot power-off if Windows offers it.
2. Extract the project into a writable local folder and install Python 3.12+. Download the Windows build of **Caddy** from https://caddyserver.com/download and put `caddy.exe` beside `Start-Hotspot.cmd`. Caddy provides the HTTPS reverse proxy and local certificate authority.
3. Run `Start-Hotspot.cmd`. It creates the Python environment if missing, installs requirements, configures keys if missing, detects active Wi-Fi Direct adapter candidates, asks you to select one if ambiguous, migrates the database, creates the `Teacher laptop hotspot` profile and starts the proxy/application/expiry worker. It never selects the upstream adapter automatically. The diagnostics are read-only by default. If the scoped firewall rule is missing, the launcher stops; rerun `Start-Hotspot.cmd` from an elevated terminal (right-click → **Run as administrator**). It previews its exact interface/address/subnet/port scope and asks you to type `APPLY FIREWALL`. Declining stops live startup. Existing rules are not deleted or replaced.
4. On first use, open a second terminal in the project folder and run `.venv\Scripts\python.exe manage.py seed_demo`. It prompts for each missing demo account's password without echoing it and does not print passwords or overwrite existing ones. For a clean database without any administrator, `bootstrap_admin yourname verified@email.example` instead creates a private activation link for that initial admin; set a password at the link. Existing accounts and keys are preserved.
5. Open the HTTPS URL printed by the launcher on the teacher laptop. It is typically similar to `https://192.168.137.1:8443`, but **use the detected address**, not this example.
6. Caddy uses a private local CA. Trust its **public root certificate only** on authorized student devices before login. Under the default Windows Caddy data location it is normally `%APPDATA%\Caddy\pki\authorities\local\root.crt` for the account running Caddy; check that account's Caddy data directory if absent. Never share `root.key` or any private key. Install the public certificate through the device's normal certificate trust settings; on iPhone, manually installed root certificates also require full trust in Certificate Trust Settings. Do not bypass TLS warnings. For institution-managed devices ask IT to deploy the CA.
7. Students connect to the laptop's hotspot using its name/password. Teacher selects the automatic hotspot profile, starts the exam and displays the QR. Students scan it with the phone camera: **QR → login → exam**, with no dashboard step. Logged-in students go directly to the exam. JavaScript submits a CSRF-protected POST to begin/resume; GET requests themselves do not create attempts. If JavaScript is off, a Continue button is shown.

## Each later exam
Turn on laptop hotspot, run `Start-Hotspot.cmd` (elevated only if the scoped firewall rule needs to be created), open the printed HTTPS address, sign in and start the exam. Keep the launcher open. Ctrl+C stops its processes; hotspot disconnection/address change also stops the processes. Do not simultaneously run Start-Local.cmd or another exposed server. Do not change the hotspot subnet during an open exam. After switching hotspot address/subnet, close old sessions and restart before opening a new one.

## Mandatory two-device check
- Student connected to **laptop hotspot**: HTTPS page loads and enrolled login can take the exam.
- Student connected only to the laptop's **upstream Wi-Fi**: exam URL must be unreachable/denied.
- Student using mobile data or another Wi-Fi: must be unreachable/denied.
- Switching away during the exam: saves denied, deadline continues; reconnect resumes the same attempt.
- Forward the QR to an unenrolled account: exam admission must fail.

The Django test suite covers the application CIDR and trusted-proxy checks, but this session did not run the elevated launcher or verify Windows adapter detection/firewall behavior, Caddy listener binding, or TLS trust on actual phones. Test these exact cases on the target laptop before the demonstration. If the hotspot adapter is not identified, the launcher stops instead of exposing the upstream interface. Devices outside the classroom but still connected to the hotspot remain inside the allowed network; this is network isolation, not physical-location verification. Do not add port forwarding, tunnels, routing relays or broad proxy rules.

## Phase B network-lockdown verification

Before using real exams, complete each procedure in [NETWORK_ACCEPTANCE_TEST.md](NETWORK_ACCEPTANCE_TEST.md). The launcher distinguishes a detected configuration and a running local socket from phone verification. Prefer a dedicated classroom network/VLAN with router-enforced isolation when available; the host CIDR allowlist is not a substitute for that boundary.

## Undo firewall rules
After stopping all exam processes, an administrator may remove only the launcher-created HTTPS rule by its exact name:

```powershell
.\Test-HotspotNetwork.ps1 -InterfaceIndex <index> -Address <hotspot-ip> -PrefixLength <prefix> -Rollback
```

This removes only `Secure Exam Hotspot HTTPS 8443`; unrelated firewall rules and exam data are untouched. For local-only development, restore development `.env` values from README and clear EXAM_HOTSPOT_CIDR/EXAM_HOTSPOT_IP; keep existing secret keys. Production instructions remain in DEPLOYMENT.md. Do not turn off the hotspot gate while exposing a public listener.

Configuration references: https://caddyserver.com/docs/caddyfile/directives/bind and https://caddyserver.com/docs/caddyfile/directives/tls .
