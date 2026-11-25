# Secure Exam System

Secure Exam System is a network-restricted Django examination platform designed for classroom use on a teacher-controlled Windows Mobile Hotspot. It combines role-based administration, QR-assisted exam admission, a server-enforced 15-minute MCQ window, autosave, automatic grading, and defense-in-depth network controls.

The project is suitable for a university demonstration and security-focused portfolio. A production deployment still requires institution-approved infrastructure, device testing, certificate management, monitoring, and backups.

## Networking security objective

The system accepts classroom exam traffic through one explicitly selected network boundary. In the Windows hotspot deployment:

- Caddy terminates HTTPS only on the selected hotspot IPv4 address at TCP 8443.
- Waitress listens only on `127.0.0.1:8001`.
- Windows Firewall permits inbound HTTPS only for the selected local address, hotspot interface, port, and remote subnet.
- Caddy overwrites client forwarding headers.
- Waitress trusts one loopback proxy hop and clears untrusted proxy headers.
- Django independently enforces the configured hotspot and per-exam authorized networks.

These controls identify an approved network path. They do not prove a student's physical location.

## Key features

- Admin, Teacher, and Student roles with username/password authentication
- Teacher-controlled student account creation and roster import
- Draft, publish, and immutable-history workflows for MCQ quizzes
- Transactional quiz deletion with ownership checks, confirmation, open-session protection, and a minimal audit record
- Explicit quiz enrollment and one attempt per student and quiz
- QR token and manual-code exam joining
- Fixed 900-second exam window enforced by the server
- Stable question order, autosave, optimistic answer versions, and idempotency receipts
- Automatic grading on submission or expiry
- Background expiry worker and lazy request-time reconciliation
- Audited result access and append-only score corrections
- Network profiles, trusted-proxy validation, and fail-closed hotspot admission
- Encrypted backup and restore helper
- Automated functional, security, proxy-chain, timer, and concurrency tests

## Technology stack

| Layer | Technology |
|---|---|
| Application | Python 3.12+, Django 5.2 |
| Production WSGI | Waitress 3 |
| HTTPS reverse proxy | Caddy 2 |
| Database | SQLite for local development, MySQL or PostgreSQL supported |
| Authentication | Django sessions, Argon2 password hashing |
| Front end | Django templates, local CSS, vanilla JavaScript |
| QR generation | `qrcode` |
| Windows networking | Mobile Hotspot, PowerShell diagnostics, Windows Defender Firewall |

Dependencies are pinned in [`requirements.txt`](requirements.txt).

## Network architecture

```text
Student browser
    |
    | HTTPS over teacher-controlled hotspot
    v
Windows Mobile Hotspot 192.168.137.0/24
    |
    | scoped inbound firewall rule
    v
Caddy 192.168.137.1:8443
    |
    | overwritten X-Forwarded-For and X-Forwarded-Proto
    v
Waitress 127.0.0.1:8001
    |
    v
Django network and exam authorization
    |
    v
Local SQLite or private MySQL/PostgreSQL database
```

The launcher discovers the active Wi-Fi Direct adapter instead of assuming that the hotspot always uses the example address above.

## User workflows

### Administrator

1. Bootstrap the initial administrator.
2. Manage teacher and student accounts.
3. Create approved network profiles.
4. Review audit events and authorized result views.

### Teacher

1. Create students for assigned classes or import an authorized roster.
2. Build an MCQ quiz and publish it when ready.
3. Open an exam session using an approved network profile.
4. Display the QR code or share the manual join code.
5. Monitor attempts, close the session if required, export results, and release review after closure.

### Student

1. Connect to the teacher laptop hotspot.
2. Scan the exam QR code or enter its manual code.
3. Sign in with the assigned username and password.
4. Start or resume the authorized attempt.
5. Answer questions while selections autosave.
6. Submit before the shared deadline or allow automatic expiry and grading.

Leaving the approved network blocks exam operations while the deadline continues. Reconnecting resumes the same attempt and deadline.

## Requirements

- Python 3.12 or newer
- Windows 10 or Windows 11 for the Mobile Hotspot launcher
- Caddy 2 for local HTTPS
- A browser on the teacher laptop and student devices
- Optional MySQL or PostgreSQL server for non-SQLite deployments
- Administrator access only when a scoped Windows Firewall rule must be created

The Caddy executable and all private certificate material are intentionally excluded from source control. The portable, non-secret `Caddyfile.hotspot` is included so a fresh clone retains the reviewed proxy and header policy.

## Installation

```powershell
git clone https://github.com/Ali4156/Secure-Exam-System.git
cd Secure-Exam-System
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Generate a new local `.env` and application keys with:

```powershell
.\.venv\Scripts\python.exe configure.py
```

Review `.env` before applying migrations. Never commit that file.

Alternatively, copy `.env.example` to `.env`, generate unique values for `DJANGO_SECRET_KEY` and `EXAM_ENCRYPTION_KEY`, and fill in the deployment-specific placeholders. `configure.py` preserves an existing `.env` and does not fill blank keys in a copied template.

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
```

## Windows setup

1. Open **Settings > Network & Internet > Mobile hotspot**.
2. Select the intended upstream connection and share it over Wi-Fi.
3. Configure a strong hotspot password.
4. Turn on the hotspot and keep automatic power-off disabled during an exam.
5. Download Caddy from its official website and place `caddy.exe` beside `Start-Hotspot.cmd`, or add it to `PATH`.
6. Keep Windows Defender Firewall enabled.

See [`docs/HOTSPOT.md`](docs/HOTSPOT.md) for the complete operational procedure.

## Hotspot deployment

Start the system with:

```powershell
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File ".\Start-Hotspot.ps1" -HotspotInterfaceIndex 17
```

Replace `17` with the current Wi-Fi Direct interface index, or run `Start-Hotspot.cmd` and select the adapter interactively when more than one candidate exists.

The launcher:

1. Verifies the selected hotspot adapter and address.
2. Performs read-only firewall and listener diagnostics.
3. Requires exactly one matching HTTPS allow rule.
4. Creates a missing rule only after an elevated operator types the explicit approval phrase.
5. Applies migrations and collects static assets.
6. Starts Waitress, the expiry worker, and Caddy.
7. Verifies the HTTPS listener and monitors the hotspot address.

The diagnostic never disables Windows Firewall and never silently edits an existing rule.

## HTTPS setup

The hotspot configuration uses Caddy's internal certificate authority. Install and trust only its public root certificate on authorized devices. Never distribute the CA private key and never bypass browser certificate warnings.

The exact root-certificate location depends on the Windows account and Caddy data directory. Confirm it from the Caddy runtime environment. Private CA state, certificates, and keys are excluded by `.gitignore`.

For an institution deployment, use an IT-managed certificate and reviewed reverse-proxy configuration. More guidance is available in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Database configuration

SQLite is the default local database. Its file is excluded from Git.

For MySQL, configure these values in `.env`:

```dotenv
DB_ENGINE=mysql
DB_NAME=secure_exam
DB_USER=exam_app
DB_PASSWORD=
DB_HOST=127.0.0.1
DB_PORT=3306
```

For PostgreSQL, use `DB_ENGINE=postgresql` and the corresponding private credentials. Use separate migration, runtime, and test accounts where practical. Never run automated tests against production data.

## Demo accounts

Create synthetic demonstration data explicitly:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo
```

The command prompts privately for passwords for missing synthetic accounts. It does not print or overwrite existing passwords.

To create only the first administrator:

```powershell
.\.venv\Scripts\python.exe manage.py bootstrap_admin admin admin@example.edu
```

Use a controlled email address and deliver the one-time setup link privately.

## Testing

Run system and migration checks:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

When the local `.env` contains a real hotspot policy, use isolated test-process overrides so Django's test client can represent loopback requests:

```powershell
$env:DJANGO_DEBUG = '1'
$env:EXAM_HOTSPOT_CIDR = '127.0.0.0/8'
$env:EXAM_TRUSTED_PROXY_CIDRS = '192.0.2.1/32'
$env:TRUST_PROXY_HTTPS = '0'
.\.venv\Scripts\python.exe manage.py test --verbosity 1
```

Latest verified SQLite result:

- 133 tests executed
- 126 passed
- 7 skipped because SQLite cannot provide the row-lock semantics required by those concurrency tests
- Django system checks reported no issues

MySQL and PostgreSQL concurrency tests have not been executed for this release. See [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md) for the recorded environment and remaining acceptance checks.

## Security limitations

- CIDR membership proves a network source address, not physical classroom presence.
- A compromised teacher laptop can undermine the local trust boundary.
- Caddy's private CA requires controlled certificate distribution and device trust management.
- QR invitation URLs are bearer credentials until they expire or are rotated.
- SQLite does not validate production row-lock behavior.
- Windows Firewall and hotspot isolation must be verified on the deployment laptop.
- Mobile data, alternate Wi-Fi, VPN, IPv6, forwarding, and client-isolation behavior require physical-device testing.
- Email delivery, backup restoration, certificate renewal, monitoring, and incident procedures require operational acceptance testing.

Review [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md), [`docs/BACKUP_RESTORE.md`](docs/BACKUP_RESTORE.md), and [`docs/NETWORK_ACCEPTANCE_TEST.md`](docs/NETWORK_ACCEPTANCE_TEST.md) before a live examination.

## Screenshots

No screenshots are committed because the repository currently has no verified images captured from the latest build with synthetic data. Add screenshots only after confirming that they show the current interface and contain no real student names, identifiers, credentials, QR tokens, activation links, or network secrets.

## Repository hygiene

The repository intentionally excludes environment files, databases, virtual environments, generated static assets, local exports, logs, Caddy binaries, certificate material, and private CA state. Use `.env.example` as the safe configuration template.
