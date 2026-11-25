from django.db import migrations, models


AUTH_TABLE = 'exams_quizdeletionauthorization'


def install(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    vendor = schema_editor.connection.vendor
    if vendor == 'sqlite':
        for name in [
            'exams_scorerevision_delete_deny',
            'question_delete_deny',
            'quiz_update_deny',
            'quiz_delete_deny',
        ]:
            cursor.execute(f'DROP TRIGGER IF EXISTS {name}')
        cursor.execute(f"""CREATE TRIGGER exams_scorerevision_delete_deny
            BEFORE DELETE ON exams_scorerevision
            WHEN NOT EXISTS (
                SELECT 1 FROM {AUTH_TABLE} authorization
                JOIN exams_attempt attempt ON attempt.quiz_id=authorization.quiz_id
                WHERE attempt.id=OLD.attempt_id
            )
            BEGIN SELECT RAISE(ABORT, 'Append-only record'); END""")
        cursor.execute(f"""CREATE TRIGGER question_delete_deny
            BEFORE DELETE ON exams_question
            WHEN (SELECT is_published FROM exams_quiz WHERE id=OLD.quiz_id)=1
             AND NOT EXISTS (SELECT 1 FROM {AUTH_TABLE} WHERE quiz_id=OLD.quiz_id)
            BEGIN SELECT RAISE(ABORT, 'Published questions are immutable'); END""")
        cursor.execute(f"""CREATE TRIGGER quiz_update_deny
            BEFORE UPDATE ON exams_quiz
            WHEN OLD.is_published=1
             AND NOT EXISTS (SELECT 1 FROM {AUTH_TABLE} WHERE quiz_id=OLD.id)
            BEGIN SELECT RAISE(ABORT, 'Published quiz is immutable'); END""")
        cursor.execute(f"""CREATE TRIGGER quiz_delete_deny
            BEFORE DELETE ON exams_quiz
            WHEN OLD.is_published=1
             AND NOT EXISTS (SELECT 1 FROM {AUTH_TABLE} WHERE quiz_id=OLD.id)
            BEGIN SELECT RAISE(ABORT, 'Published quiz is immutable'); END""")
    elif vendor == 'postgresql':
        cursor.execute('DROP TRIGGER IF EXISTS immutable ON exams_scorerevision')
        cursor.execute("""CREATE FUNCTION exam_score_revision_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
              IF TG_OP='DELETE' AND EXISTS (
                SELECT 1 FROM exams_quizdeletionauthorization authorization
                JOIN exams_attempt attempt ON attempt.quiz_id=authorization.quiz_id
                WHERE attempt.id=OLD.attempt_id
              ) THEN RETURN OLD; END IF;
              RAISE EXCEPTION 'Append-only record';
            END $$""")
        cursor.execute("""CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON exams_scorerevision
            FOR EACH ROW EXECUTE FUNCTION exam_score_revision_immutable()""")
        cursor.execute(f"""CREATE OR REPLACE FUNCTION exam_published_question() RETURNS trigger
            LANGUAGE plpgsql AS $$
            DECLARE qid bigint;
            BEGIN
              IF TG_OP='INSERT' THEN qid := NEW.quiz_id; ELSE qid := OLD.quiz_id; END IF;
              IF EXISTS(SELECT 1 FROM exams_quiz WHERE id=qid AND is_published)
                 AND NOT (TG_OP='DELETE' AND EXISTS(
                     SELECT 1 FROM {AUTH_TABLE} WHERE quiz_id=qid
                 )) THEN
                RAISE EXCEPTION 'Published questions are immutable';
              END IF;
              IF TG_OP='UPDATE' AND EXISTS(
                  SELECT 1 FROM exams_quiz WHERE id=NEW.quiz_id AND is_published
              ) THEN RAISE EXCEPTION 'Published questions are immutable'; END IF;
              IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
            END $$""")
        cursor.execute(f"""CREATE OR REPLACE FUNCTION exam_published_quiz() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
              IF OLD.is_published AND NOT EXISTS(
                  SELECT 1 FROM {AUTH_TABLE} WHERE quiz_id=OLD.id
              ) THEN RAISE EXCEPTION 'Published quiz is immutable'; END IF;
              IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
            END $$""")
    elif vendor == 'mysql':
        for name in [
            'exams_scorerevision_delete_deny',
            'question_delete_deny',
            'quiz_update_deny',
            'quiz_delete_deny',
        ]:
            cursor.execute(f'DROP TRIGGER IF EXISTS {name}')
        cursor.execute(f"""CREATE TRIGGER exams_scorerevision_delete_deny
            BEFORE DELETE ON exams_scorerevision FOR EACH ROW
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM {AUTH_TABLE} authorization
                JOIN exams_attempt attempt ON attempt.quiz_id=authorization.quiz_id
                WHERE attempt.id=OLD.attempt_id
              ) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Append-only record'; END IF;
            END""")
        cursor.execute(f"""CREATE TRIGGER question_delete_deny
            BEFORE DELETE ON exams_question FOR EACH ROW
            BEGIN
              IF EXISTS (SELECT 1 FROM exams_quiz WHERE id=OLD.quiz_id AND is_published=1)
                 AND NOT EXISTS (SELECT 1 FROM {AUTH_TABLE} WHERE quiz_id=OLD.quiz_id)
              THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Published questions are immutable'; END IF;
            END""")
        cursor.execute(f"""CREATE TRIGGER quiz_update_deny
            BEFORE UPDATE ON exams_quiz FOR EACH ROW
            BEGIN
              IF OLD.is_published=1
                 AND NOT EXISTS (SELECT 1 FROM {AUTH_TABLE} WHERE quiz_id=OLD.id)
              THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Published quiz is immutable'; END IF;
            END""")
        cursor.execute(f"""CREATE TRIGGER quiz_delete_deny
            BEFORE DELETE ON exams_quiz FOR EACH ROW
            BEGIN
              IF OLD.is_published=1
                 AND NOT EXISTS (SELECT 1 FROM {AUTH_TABLE} WHERE quiz_id=OLD.id)
              THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Published quiz is immutable'; END IF;
            END""")


def uninstall(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    vendor = schema_editor.connection.vendor
    if vendor == 'sqlite':
        for name in [
            'exams_scorerevision_delete_deny',
            'question_delete_deny',
            'quiz_update_deny',
            'quiz_delete_deny',
        ]:
            cursor.execute(f'DROP TRIGGER IF EXISTS {name}')
        cursor.execute("""CREATE TRIGGER exams_scorerevision_delete_deny BEFORE DELETE
            ON exams_scorerevision BEGIN SELECT RAISE(ABORT, 'Append-only record'); END""")
        cursor.execute("""CREATE TRIGGER question_delete_deny BEFORE DELETE ON exams_question
            WHEN (SELECT is_published FROM exams_quiz WHERE id=OLD.quiz_id)=1
            BEGIN SELECT RAISE(ABORT, 'Published questions are immutable'); END""")
        cursor.execute("""CREATE TRIGGER quiz_update_deny BEFORE UPDATE ON exams_quiz
            WHEN OLD.is_published=1 BEGIN SELECT RAISE(ABORT, 'Published quiz is immutable'); END""")
        cursor.execute("""CREATE TRIGGER quiz_delete_deny BEFORE DELETE ON exams_quiz
            WHEN OLD.is_published=1 BEGIN SELECT RAISE(ABORT, 'Published quiz is immutable'); END""")
    elif vendor == 'postgresql':
        cursor.execute('DROP TRIGGER IF EXISTS immutable ON exams_scorerevision')
        cursor.execute('DROP FUNCTION IF EXISTS exam_score_revision_immutable()')
        cursor.execute("""CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON exams_scorerevision
            FOR EACH ROW EXECUTE FUNCTION exam_append_only()""")
        cursor.execute("""CREATE OR REPLACE FUNCTION exam_published_question() RETURNS trigger
            LANGUAGE plpgsql AS $$
            DECLARE qid bigint;
            BEGIN
              IF TG_OP='INSERT' THEN qid := NEW.quiz_id; ELSE qid := OLD.quiz_id; END IF;
              IF EXISTS(SELECT 1 FROM exams_quiz WHERE id=qid AND is_published)
              THEN RAISE EXCEPTION 'Published questions are immutable'; END IF;
              IF TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM exams_quiz WHERE id=NEW.quiz_id AND is_published)
              THEN RAISE EXCEPTION 'Published questions are immutable'; END IF;
              IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
            END $$""")
        cursor.execute("""CREATE OR REPLACE FUNCTION exam_published_quiz() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
              IF OLD.is_published THEN RAISE EXCEPTION 'Published quiz is immutable'; END IF;
              IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
            END $$""")
    elif vendor == 'mysql':
        for name in [
            'exams_scorerevision_delete_deny',
            'question_delete_deny',
            'quiz_update_deny',
            'quiz_delete_deny',
        ]:
            cursor.execute(f'DROP TRIGGER IF EXISTS {name}')
        cursor.execute("""CREATE TRIGGER exams_scorerevision_delete_deny BEFORE DELETE
            ON exams_scorerevision FOR EACH ROW
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Append-only record'""")
        cursor.execute("""CREATE TRIGGER question_delete_deny BEFORE DELETE ON exams_question FOR EACH ROW
            BEGIN IF EXISTS (SELECT 1 FROM exams_quiz WHERE id=OLD.quiz_id AND is_published=1)
            THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Published questions are immutable'; END IF; END""")
        cursor.execute("""CREATE TRIGGER quiz_update_deny BEFORE UPDATE ON exams_quiz FOR EACH ROW
            BEGIN IF OLD.is_published=1 THEN SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT='Published quiz is immutable'; END IF; END""")
        cursor.execute("""CREATE TRIGGER quiz_delete_deny BEFORE DELETE ON exams_quiz FOR EACH ROW
            BEGIN IF OLD.is_published=1 THEN SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT='Published quiz is immutable'; END IF; END""")


class Migration(migrations.Migration):
    dependencies = [('exams', '0005_quiz_replaces')]
    operations = [
        migrations.CreateModel(
            name='QuizDeletionAuthorization',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quiz_id', models.PositiveBigIntegerField(unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.RunPython(install, uninstall),
    ]
