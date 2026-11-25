"""Encrypted database backup/restore. Keep backup and exam encryption keys separate."""
import argparse,os,sqlite3,subprocess,tempfile
from pathlib import Path
from cryptography.fernet import Fernet
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'.env')
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('action',choices=['backup','restore']);p.add_argument('file',type=Path)
p.add_argument('--confirm-empty-target',action='store_true',help='Restore requires a new empty target; stop app and worker first.')
a=p.parse_args()
key=os.getenv('BACKUP_ENCRYPTION_KEY')
if not key: p.error('Set BACKUP_ENCRYPTION_KEY to an independent Fernet key kept outside the project.')
f=Fernet(key.encode())
engine=os.getenv('DB_ENGINE','sqlite').lower()
pg=engine=='postgresql'
mysql=engine=='mysql'
if engine not in ('sqlite','postgresql','mysql'):
    p.error('DB_ENGINE must be sqlite, mysql, or postgresql.')
env=os.environ.copy()
for source,target,default in [('DB_NAME','PGDATABASE','secure_exam'),('DB_USER','PGUSER','exam_app'),('DB_PASSWORD','PGPASSWORD',''),('DB_HOST','PGHOST','127.0.0.1'),('DB_PORT','PGPORT','5432')]:env[target]=os.getenv(source,default)
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'database.dump'
    mysql_defaults=None
    if mysql:
        mysql_defaults=Path(tmp)/'mysql.cnf'
        def quote_option(value):
            return '"'+value.replace('\\','\\\\').replace('"','\\"')+'"'
        mysql_defaults.write_text(
            '[client]\n'
            +'user='+quote_option(os.getenv('DB_USER','exam_app'))+'\n'
            +'password='+quote_option(os.getenv('DB_PASSWORD',''))+'\n'
            +'host='+quote_option(os.getenv('DB_HOST','127.0.0.1'))+'\n'
            +'port='+quote_option(os.getenv('DB_PORT','3306'))+'\n',
            encoding='utf-8')
        try:mysql_defaults.chmod(0o600)
        except OSError:pass
    if a.action=='backup':
        if pg: subprocess.run(['pg_dump','--format=custom','--file',str(path)],env=env,check=True)
        elif mysql:
            subprocess.run(['mysqldump','--defaults-extra-file='+str(mysql_defaults),'--single-transaction','--routines','--triggers','--hex-blob','--result-file='+str(path),os.getenv('DB_NAME','secure_exam')],check=True)
        else:
            source=sqlite3.connect(str(ROOT/'db.sqlite3'));dest=sqlite3.connect(path)
            with dest: source.backup(dest)
            source.close();dest.close()
        a.file.write_bytes(f.encrypt(path.read_bytes()))
        try:a.file.chmod(0o600)
        except OSError:pass
        print('Encrypted backup created:',a.file)
    else:
        if not a.confirm_empty_target:p.error('Stop the application and select a new empty database. Then use --confirm-empty-target.')
        data=f.decrypt(a.file.read_bytes());path.write_bytes(data)
        if pg:
            # Deliberately no --clean; an existing target must not be silently overwritten.
            subprocess.run(['pg_restore','--exit-on-error','--no-owner','--dbname',env['PGDATABASE'],str(path)],env=env,check=True)
        elif mysql:
            database=os.getenv('DB_NAME','secure_exam')
            client=['mysql','--defaults-extra-file='+str(mysql_defaults),'--database='+database]
            result=subprocess.run(client+['--batch','--skip-column-names','--execute','SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()'],check=True,capture_output=True,text=True)
            if result.stdout.strip()!='0':p.error('Restore target must be an existing empty MySQL database.')
            with path.open('rb') as dump:
                subprocess.run(client,stdin=dump,check=True)
        else:
            target=ROOT/'db.sqlite3'
            if target.exists():p.error('Target db.sqlite3 exists. Restore to a fresh project directory.')
            target.write_bytes(data)
        print('Restored. Restore the matching EXAM_ENCRYPTION_KEY before running the application.')
