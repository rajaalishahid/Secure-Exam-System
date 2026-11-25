# Threat model and limits

Assets: student authentication, exam paper/answer keys, admission tokens, received answers, deadlines, scores and audit history. Trust boundaries: untrusted browser → HTTPS proxy → Django → local database; administrator provisioning → student activation; deployment operator → firewall/keys/backups.

| Threat | Control | Remaining boundary |
| --- | --- | --- |
| Password theft | Argon2, throttling, session revocation | User sharing credentials or phishing is not eliminated |
| Forwarded classroom QR | Separate authentication, enrollment and network gates | QR alone cannot prove physical presence |
| Invitation interception | Single-use 256-bit activation, expiry, private verified delivery, supervised enrollment | Human identity verification is required |
| ID guessing / cross-class requests | Role and object checks on each handler | Database operators remain privileged |
| Forged IP/SSID / alternate listener | Ignore untrusted forwarded headers, explicit network profile; private listener and firewall deployment | Actual routes/VLANs must be validated outside the application |
| Client clock / late write | Server UTC after lock, deadline boundary, shared window | OS clock and DB/server availability are operational dependencies |
| Out-of-order requests | Expected version, idempotency receipt, serial per-question client save loop | Browser/device end-to-end testing remains required |
| Teacher closure races | Same session→attempt lock order for close/start/save/submit | Target MySQL/PostgreSQL concurrency tests must pass before concurrent class use |
| Early answer leakage | Score-only result; explicit review after closure | Authorized teacher sees their own answer keys |
| Published-paper / audit mutation | SQL triggers; no alternate admin editor; append-only corrections | Privileged DB access must be tightly controlled |
| Worker or browser stops | Persistent deadline; periodic worker; lazy finalization | Monitor/restart the worker; no false save success during outage |
| Backup theft | Independent Fernet backup key; exam ciphertext key separate | Keys and temporary dump files require protected storage |

No facial recognition, microphone capture, screenshot blocking, invasive proctoring, or automatic tab-switch cheating accusations are implemented. Remote control, authorized-device relays, shared accounts, NAT and broadly allowed subnets weaken presence assumptions. Use human invigilation and approved network design. This project controls access and exam state; it cannot guarantee zero cheating or zero vulnerabilities.

Operational limitations: admin account listing currently shows at most 500 accounts; use direct supervised administrative procedures for larger deployments. Backups buffer the archive in memory and are intended for small semester-project databases. Login rate limiting uses shared DB counters, not a distributed edge abuse-defense service. Configure external monitoring for server/worker failure and repeated denials. No actual institutional identity directory or SMS provider is integrated. The original uploaded database/credentials are not reused; existing live datasets require a reviewed migration plan rather than treating this sanitized build as a drop-in update.
