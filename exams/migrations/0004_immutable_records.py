from django.db import migrations

def install(apps,schema_editor):
    c=schema_editor.connection.cursor()
    if schema_editor.connection.vendor=='sqlite':
        for table in ['audit_auditevent','exams_scorerevision']:
            for op in ['UPDATE','DELETE']:
                c.execute(f"CREATE TRIGGER {table}_{op.lower()}_deny BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT, 'Append-only record'); END")
        for op in ['INSERT','UPDATE','DELETE']:
            ref='NEW' if op=='INSERT' else 'OLD'
            c.execute(f"CREATE TRIGGER question_{op.lower()}_deny BEFORE {op} ON exams_question WHEN (SELECT is_published FROM exams_quiz WHERE id={ref}.quiz_id)=1 BEGIN SELECT RAISE(ABORT, 'Published questions are immutable'); END")
        c.execute("CREATE TRIGGER question_move_deny BEFORE UPDATE ON exams_question WHEN (SELECT is_published FROM exams_quiz WHERE id=NEW.quiz_id)=1 BEGIN SELECT RAISE(ABORT, 'Published questions are immutable'); END")
        c.execute("CREATE TRIGGER quiz_update_deny BEFORE UPDATE ON exams_quiz WHEN OLD.is_published=1 BEGIN SELECT RAISE(ABORT, 'Published quiz is immutable'); END")
        c.execute("CREATE TRIGGER quiz_delete_deny BEFORE DELETE ON exams_quiz WHEN OLD.is_published=1 BEGIN SELECT RAISE(ABORT, 'Published quiz is immutable'); END")
    elif schema_editor.connection.vendor=='postgresql':
        c.execute("""CREATE FUNCTION exam_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Append-only record'; END $$""")
        for table in ['audit_auditevent','exams_scorerevision']:
            c.execute(f'CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION exam_append_only()')
        c.execute("""CREATE FUNCTION exam_published_question() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE qid bigint;
        BEGIN
          IF TG_OP='INSERT' THEN qid=NEW.quiz_id; ELSE qid=OLD.quiz_id; END IF;
          IF EXISTS(SELECT 1 FROM exams_quiz WHERE id=qid AND is_published) THEN RAISE EXCEPTION 'Published questions are immutable'; END IF;
          IF TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM exams_quiz WHERE id=NEW.quiz_id AND is_published) THEN RAISE EXCEPTION 'Published questions are immutable'; END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END $$""")
        c.execute('CREATE TRIGGER immutable BEFORE INSERT OR UPDATE OR DELETE ON exams_question FOR EACH ROW EXECUTE FUNCTION exam_published_question()')
        c.execute("""CREATE FUNCTION exam_published_quiz() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.is_published THEN RAISE EXCEPTION 'Published quiz is immutable'; END IF; IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF; END $$""")
        c.execute('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON exams_quiz FOR EACH ROW EXECUTE FUNCTION exam_published_quiz()')
    elif schema_editor.connection.vendor=='mysql':
        for table in ['audit_auditevent','exams_scorerevision']:
            for operation in ['UPDATE','DELETE']:
                trigger=f'{table}_{operation.lower()}_deny'
                c.execute(f"""CREATE TRIGGER {trigger} BEFORE {operation} ON {table} FOR EACH ROW
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Append-only record'""")
        c.execute("""CREATE TRIGGER question_insert_deny BEFORE INSERT ON exams_question FOR EACH ROW
        BEGIN
          IF EXISTS (SELECT 1 FROM exams_quiz WHERE id=NEW.quiz_id AND is_published=1) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Published questions are immutable';
          END IF;
        END""")
        c.execute("""CREATE TRIGGER question_update_deny BEFORE UPDATE ON exams_question FOR EACH ROW
        BEGIN
          IF EXISTS (SELECT 1 FROM exams_quiz WHERE id=OLD.quiz_id AND is_published=1)
             OR EXISTS (SELECT 1 FROM exams_quiz WHERE id=NEW.quiz_id AND is_published=1) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Published questions are immutable';
          END IF;
        END""")
        c.execute("""CREATE TRIGGER question_delete_deny BEFORE DELETE ON exams_question FOR EACH ROW
        BEGIN
          IF EXISTS (SELECT 1 FROM exams_quiz WHERE id=OLD.quiz_id AND is_published=1) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Published questions are immutable';
          END IF;
        END""")
        c.execute("""CREATE TRIGGER quiz_update_deny BEFORE UPDATE ON exams_quiz FOR EACH ROW
        BEGIN
          IF OLD.is_published=1 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Published quiz is immutable';
          END IF;
        END""")
        c.execute("""CREATE TRIGGER quiz_delete_deny BEFORE DELETE ON exams_quiz FOR EACH ROW
        BEGIN
          IF OLD.is_published=1 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Published quiz is immutable';
          END IF;
        END""")

def uninstall(apps,schema_editor):
    c=schema_editor.connection.cursor()
    if schema_editor.connection.vendor=='sqlite':
        for name in ['question_move_deny','audit_auditevent_update_deny','audit_auditevent_delete_deny','exams_scorerevision_update_deny','exams_scorerevision_delete_deny','question_insert_deny','question_update_deny','question_delete_deny','quiz_update_deny','quiz_delete_deny']: c.execute(f'DROP TRIGGER IF EXISTS {name}')
    elif schema_editor.connection.vendor=='postgresql':
        for table in ['audit_auditevent','exams_scorerevision','exams_question','exams_quiz']: c.execute(f'DROP TRIGGER IF EXISTS immutable ON {table}')
        for name in ['exam_append_only','exam_published_question','exam_published_quiz']: c.execute(f'DROP FUNCTION IF EXISTS {name}()')
    elif schema_editor.connection.vendor=='mysql':
        for name in ['audit_auditevent_update_deny','audit_auditevent_delete_deny','exams_scorerevision_update_deny','exams_scorerevision_delete_deny','question_insert_deny','question_update_deny','question_delete_deny','quiz_update_deny','quiz_delete_deny']:
            c.execute(f'DROP TRIGGER IF EXISTS {name}')

class Migration(migrations.Migration):
    dependencies=[('exams','0003_networkprofile_attempt_question_order_and_more'),('audit','0001_initial')]
    operations=[migrations.RunPython(install,uninstall)]
