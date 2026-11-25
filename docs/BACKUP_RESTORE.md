# Encrypted backup and restore

`backup.py` supports SQLite prototype snapshots, PostgreSQL custom-format dumps, and MySQL dumps. It uses an independently supplied Fernet key; it does not silently generate or store the backup key. PostgreSQL requires pg_dump / pg_restore in PATH; MySQL requires mysqldump / mysql in PATH. MySQL client credentials are written to a temporary option file for the duration of the command. Restrict access to backup files and keys. Prefer an encrypted disk because intermediate dumps temporarily exist inside a private OS temporary directory. The helper reads the dump into memory; suitable for this semester project, not huge databases.

1. Generate a separate key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Store it in a password manager/offline secure vault. Set BACKUP_ENCRYPTION_KEY for the backup process only.
3. Run `python backup.py backup lab-backup.enc`. Back up the matching EXAM_ENCRYPTION_KEY and other deployment configuration separately, never inside the same unprotected archive.
4. For a restore drill, use a separate extracted project directory and a NEW empty database. Stop application and worker. For PostgreSQL, do not run migrations before pg_restore: the archive restores schema, data and triggers in the correct order. For MySQL, create an empty target database first; the helper refuses to restore over tables in it. For SQLite, the helper refuses to overwrite an existing db.sqlite3.
5. Use the same backup key: `python backup.py restore lab-backup.enc --confirm-empty-target`.
6. Restore the matching EXAM_ENCRYPTION_KEY and application configuration. Run `python manage.py check` and `python manage.py finalize_exams --once`. Verify users, attempts, answers, audit linkage and ciphertext decryption. Then run the target database tests and a small synthetic exam.
7. Document the restore timestamp, source backup and operator. Do not grant app accounts permission to disable integrity triggers.

Database snapshots preserve deadlines. Restoring an old live exam does not grant a new deadline; the worker finalizes overdue attempts. External side effects such as already-delivered email cannot be rolled back by restoration.
