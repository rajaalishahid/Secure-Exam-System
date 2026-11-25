# Initial accounts and password-only sign-in

The application uses Django username/password authentication. OTP, TOTP and MFA are not part of the active sign-in or account setup flow. Historical MFA tables and migration files remain in place so existing data is not deleted.

## Synthetic demo accounts

To explicitly create the initial synthetic administrator, teacher, student, class and draft quiz, run:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo
```

When passwords are not supplied through the private process environment variables (`SECURE_EXAM_DEMO_ADMIN_PASSWORD`, `SECURE_EXAM_DEMO_TEACHER_PASSWORD`, `SECURE_EXAM_DEMO_STUDENT_PASSWORD`), the command requests each required password using hidden terminal input. Do not put credentials in source control, shared command history, application logs or chat. The command is idempotent: existing accounts keep their passwords, and the command does not create additional demo accounts on application startup.

The command creates only missing accounts and leaves real accounts untouched. It also ensures the synthetic student belongs to the synthetic teacher's class and creates a draft quiz if needed. The quiz must still be reviewed and published before an exam.

## First administrator only

For a clean database where no administrator exists, an authorized local operator may run:

```powershell
.\.venv\Scripts\python.exe manage.py bootstrap_admin <username> <verified-email>
```

This command creates a pending initial administrator with an unusable password and prints one private, 30-minute activation link. Open the link, choose a password and sign in normally. Activation is retained for this legacy/bootstrap purpose only; it does not require authenticator setup. Set `BASE_URL` to the trusted reachable application origin before issuing the link. Outside debug mode, that origin must use HTTPS and cannot be loopback.

If an administrator already exists, manage accounts and password resets through the application with an authorized admin account. The bootstrap command refuses to create another administrator.

## Teacher-created students

Teachers use **Add Student** and can select only their assigned class or classes. A sole assigned class is preselected. Student ID is used as the username when unique; duplicate IDs are rejected. The form creates an active account with a securely generated temporary password and displays that password once in a no-store response for private handoff. No email, invitation, email verification, authenticator setup or student self-registration is involved. Students can change their password after signing in.

Do not delete the database or historical migrations as part of account setup. For hotspot-specific steps and the outstanding physical-network verification, see `HOTSPOT.md`.
