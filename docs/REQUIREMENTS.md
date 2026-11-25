# Requirements mapping and authorization

The original blueprint remains the reference for networking, exam timing, roles, permissions, grading and audit. The revised university network-security requirements supersede its OTP/TOTP/MFA setup: active authentication is Django username/password only. The `AccountMFA`/`RecoveryCode` models and historical migrations are retained, but do not gate login or protected routes.

## Requirement mapping

| Area | Current implementation | Limits / verification |
| --- | --- | --- |
| Roles and account management | Admin, teacher and student roles; admin manages teacher accounts, classes, network profiles and audit; teacher manages students in owned classes | Existing real users are preserved; demo accounts are created only by explicitly invoking `seed_demo` |
| Authentication | Django `authenticate`/`login`/`logout`, Django password hashing, database sessions, CSRF, shared DB-backed login throttling, role dashboards, server-side security-version/password fingerprint and 8-hour absolute session limit | No public student sign-up, email verification, OTP, TOTP or MFA |
| Legacy account transition | Valid-password `pending_mfa` accounts are promoted to active on successful login; suspension revokes active sessions | Historical MFA data is not deleted or consulted for authentication |
| Student provisioning | Teacher enters full name, student ID and an assigned class; student ID becomes username; active student is enrolled immediately; generated temporary password appears once in a no-store response | Password must be privately delivered by the teacher; no invitation/email flow is used for new students |
| Classes and quizzes | Teacher class ownership constrains class, quiz, roster and result actions; publication snapshots enrollment; published quiz/questions are immutable | Adding a student after publication does not silently enroll them in an existing published quiz |
| Exam entry and timing | Student dashboard lists open enrolled sessions on the authorized network; instructions/join actions recheck login, student role, enrollment, open session and network; QR/manual join remains a pending session hint; CSRF-protected POST starts/resumes a single attempt; server enforces exactly 900 seconds | Scanning a QR does not itself authenticate or start an attempt |
| Autosave and grading | Versioned autosave, idempotency receipts, server deadline checks, automatic grading, one attempt per student/quiz and result review controls | SQL row-lock/concurrency tests require MySQL or PostgreSQL; SQLite skips those tests |
| Audit | Request actor/role/source metadata; append-only database records and audited denials; teacher result export is scoped | DB owners can bypass application controls; runtime credentials should not have migration privileges |
| Network boundary | Exam network profiles are CIDR validated and snapshotted per session; trusted proxy parsing ignores client headers from untrusted peers; global hotspot CIDR gate; Windows launcher configures a Wi-Fi Direct-bound Caddy listener and scoped firewall rules | CIDR matching is not physical-presence proof. Actual Windows firewall, routed paths, IPv6 and phone checks remain required; see `HOTSPOT.md` |
| Initial synthetic demo | Explicit, idempotent `manage.py seed_demo` creates one demo admin/teacher/student, one class and a draft quiz; missing passwords are collected via hidden prompt or private environment values | Never overwrites existing passwords; demo quiz needs teacher review/publication |

## Route permissions

| Route/action | Required actor | Additional authorization |
| --- | --- | --- |
| POST `/login/`, POST `/logout/` | Public / authenticated | Password, account state, throttling and CSRF |
| `/accounts/activate/` | Legacy pending account token holder | Single-use digest, purpose, expiry, password policy; no MFA enrollment |
| `/accounts/student-create/` | Teacher | `CreateStudentForm` limits class choices to teacher-owned classes; duplicate student IDs rejected |
| `/accounts/teacher-create/`, `/accounts/manage/` | Admin | Account actions audited; suspension invalidates existing sessions |
| `/classes/assign/` | Admin | Only active teacher accounts may be assigned |
| `/classes/{id}/import/` | Assigned teacher | Bounded CSV, all-or-nothing transaction, student IDs validated |
| Quiz creation/edit/publish and exam session actions | Owning teacher | Teacher/class ownership; publication freezes quiz; fixed 900-second session |
| POST `/exams/scan/` or QR GET | Public/student | Valid pending join token, session open and current network; does not create an attempt |
| `/exams/session/{id}/instructions/` | Enrolled authenticated student | Open session, active network profile and current source network; sets only a pending session hint |
| `/exams/enter/`, `/exams/instructions/` | Enrolled authenticated student | Current session token, network and exam window; start requires POST |
| Attempt read/save/submit/result | Student owner | Active session and trusted network; teachers see only their own class results; admin view audited |
| `/audit/`, network management | Admin | Scoped application UI; audit history is append-only |

The custom Django admin site is not exposed as an alternate application path. Keep application and migration credentials separate. For network guarantees, validate the deployed edge firewall and listener rather than relying on application CIDR checks alone.
