"""Bot-only SQLite state and verified online backups. No worker imports this module."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from .files import private_dir

SCHEMA = '''
CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS v1_meta(version INTEGER NOT NULL);
INSERT INTO v1_meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM v1_meta);
CREATE TABLE IF NOT EXISTS jobs(
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
 message_id INTEGER NOT NULL, input_message_id INTEGER NOT NULL,
 url TEXT NOT NULL, platform TEXT NOT NULL, resource_id TEXT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'inspect', metadata TEXT NOT NULL DEFAULT '{}',
 format TEXT, created REAL NOT NULL, queued REAL, download_attempts INTEGER NOT NULL DEFAULT 0,
 upload_attempts INTEGER NOT NULL DEFAULT 0, next_upload REAL NOT NULL DEFAULT 0,
 execution_id INTEGER, ready_file TEXT, file_id TEXT, cache_key TEXT,
 cache_hit INTEGER NOT NULL DEFAULT 0, quota_exempt INTEGER NOT NULL DEFAULT 0,
 cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT, last_status TEXT,
 UNIQUE(chat_id,input_message_id));
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(phase,queued,id);
CREATE TABLE IF NOT EXISTS executions(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id INTEGER NOT NULL,action TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS result_cache(key TEXT PRIMARY KEY,file_id TEXT NOT NULL,valid INTEGER NOT NULL DEFAULT 1, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quota(job_id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,day TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS received_updates(chat_id INTEGER,message_id INTEGER,result TEXT,PRIMARY KEY(chat_id,message_id));
CREATE TABLE IF NOT EXISTS attempts(user_id INTEGER NOT NULL,at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS attempts_user ON attempts(user_id,at);
CREATE TABLE IF NOT EXISTS bans_v1(user_id INTEGER PRIMARY KEY,until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notices(id INTEGER PRIMARY KEY,code TEXT NOT NULL UNIQUE,text TEXT NOT NULL);
'''
TERMINAL = {'completed', 'failed', 'cancelled'}

def cache_key(platform,resource_id,code,language):
    return json.dumps([platform,resource_id,'mp3' if code in {'MP3','audio'} else 'video',code,language,'mp3-vbr0' if code in {'MP3','audio'} else 'h264-aac'],separators=(',',':'))

class StateUnavailable(RuntimeError):
    pass

def day():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')

class Store:
    def __init__(self, path=Path('data/bot.sqlite')):
        self.path = Path(path)

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def verify(path):
        if path.is_symlink() or not path.is_file():
            return False
        try:
            from urllib.parse import quote
            c = sqlite3.connect('file:' + quote(str(path.absolute())) + '?mode=ro', uri=True)
            try:
                return c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            finally:
                c.close()
        except sqlite3.Error:
            return False

    def initialize(self, backup_config):
        private_dir(self.path.parent)
        if not self.path.exists() and list(self.path.parent.glob('database_corrupt_*.sqlite')):
            raise StateUnavailable('Сохранена повреждённая база; требуется db restore или явный db init.')
        if self.path.exists() and not self.verify(self.path):
            self.preserve_corrupt()
            candidates = sorted(Path('backups').glob('*.sqlite'), reverse=True)
            for p in candidates:
                if self.verify(p) and self.compatible(p):
                    self.restore(p)
                    self.notice('db_recovery', 'База восстановлена из проверенной копии.')
                    break
            else:
                raise StateUnavailable('SQLite повреждена; обработка остановлена, нужна проверенная копия или db init.')
        if self.path.exists():
            # Before any schema addition/migration, preserve a checked snapshot.
            self.backup(backup_config['max_copies'])
        with self.db() as db:
            # executescript commits; only used before any task processing starts.
            db.executescript(SCHEMA)
            version = db.execute('SELECT version FROM v1_meta').fetchone()[0]
            if version != 1:
                raise StateUnavailable('Несовместимая схема SQLite')
            self.migrate_legacy(db)
        os.chmod(self.path, 0o600)

    def migrate_legacy(self, db):
        """One-time data migration, not an adapter for the former architecture.

        Original tables and all files are preserved. Old unverified local results
        are never sent as if they had passed the new worker validation.
        """
        if db.execute("SELECT 1 FROM migrations WHERE name='legacy'").fetchone():
            return
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'tasks' in tables:
            from .urls import validate_url
            for old in db.execute('SELECT * FROM tasks ORDER BY id').fetchall():
                r = dict(old)
                try:
                    u = validate_url(r['source_url'])
                    platform, resource_id, url = u.platform, u.resource_id, u.url
                    phase = r['status'] if r['status'] in TERMINAL else 'queued'
                    error = None
                except ValueError:
                    platform,resource_id,url = '', '', r['source_url']
                    phase,error = 'failed','legacy_url_rejected'
                code = r['variant']
                if code not in {'480p','720p','1080p','MP3','video','audio'}:
                    phase,error = 'failed','legacy_format_rejected'
                try:
                    queued = datetime.fromisoformat(r['created_at'].replace('Z','+00:00')).timestamp()
                except (ValueError,KeyError):
                    queued = time.time()
                variants = [{'code':code,'size':None,'approximate':True}] if code not in {'video','audio'} else []
                db.execute("""INSERT OR IGNORE INTO jobs
                    (id,user_id,chat_id,message_id,input_message_id,url,platform,resource_id,
                    phase,metadata,format,created,queued,download_attempts,upload_attempts,file_id,error)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (r['id'],r['user_id'],r['chat_id'],r.get('status_message_id') or r['message_id'],r['message_id'],url,platform,resource_id,
                     phase,json.dumps({'variants':variants}),code,queued,queued,r['download_attempts'],r['upload_attempts'],r.get('telegram_file_id'),error))
            if 'daily_usage' in tables:
                db.execute('INSERT OR IGNORE INTO quota(job_id,user_id,day) SELECT task_id,user_id,utc_day FROM daily_usage')
            if 'link_attempts' in tables:
                db.execute('INSERT INTO attempts SELECT user_id,attempted_at FROM link_attempts')
            if 'bans' in tables:
                db.execute('INSERT OR REPLACE INTO bans_v1 SELECT user_id,banned_until FROM bans')
            db.execute("INSERT OR IGNORE INTO notices(code,text) VALUES('legacy_data',?)",
                ('Данные прежней схемы сохранены. Очередь и квоты перенесены; старые локальные файлы без подтверждения проверки не отправляются. Исходные файлы и таблицы не удалены.',))
        db.execute("INSERT INTO migrations VALUES('legacy')")

    @staticmethod
    def compatible(path):
        try:
            c = sqlite3.connect(f'file:{path.absolute()}?mode=ro', uri=True)
            try:
                row = c.execute('SELECT version FROM v1_meta').fetchone()
                return row is not None and row[0] == 1
            finally:
                c.close()
        except sqlite3.Error:
            return False

    def preserve_corrupt(self):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        target = self.path.with_name(f'database_corrupt_{stamp}.sqlite')
        for suffix in ('', '-wal', '-shm'):
            p = Path(str(self.path) + suffix)
            if p.exists():
                os.replace(p, Path(str(target) + suffix))
        return target

    def backup(self, max_copies=3):
        root = private_dir('backups')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        target = root / f'backup_{stamp}.sqlite'
        try:
            source = sqlite3.connect(self.path)
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
            finally:
                dest.close()
                source.close()
            if not self.verify(target):
                raise StateUnavailable('Ошибка проверки резервной копии')
            os.chmod(target, 0o600)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        for old in sorted(root.glob('backup_*.sqlite'), reverse=True)[max_copies:]:
            old.unlink()
        return target

    def restore(self, source):
        if not self.verify(source) or not self.compatible(source):
            raise StateUnavailable('Копия повреждена или несовместима')
        if self.path.exists():
            self.preserve_corrupt()
        a, b = sqlite3.connect(source), sqlite3.connect(self.path)
        try:
            a.backup(b)
        finally:
            a.close(); b.close()
        if not self.verify(self.path):
            raise StateUnavailable('Ошибка проверки восстановленной базы')

    def rows(self, sql, args=()):
        with self.db() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def get(self, job_id):
        rows = self.rows('SELECT * FROM jobs WHERE id=?', (job_id,))
        return rows[0] if rows else None

    def update(self, job_id, **values):
        allowed = {'phase','metadata','format','queued','download_attempts','upload_attempts','next_upload','execution_id','ready_file','file_id','cache_key','cache_hit','quota_exempt','cancel_requested','error','last_status','message_id'}
        if not values.keys() <= allowed:
            raise ValueError('invalid_update')
        with self.db() as db:
            db.execute('UPDATE jobs SET ' + ','.join(k+'=?' for k in values) + ' WHERE id=?', (*values.values(), job_id))

    def create(self, user_id, chat_id, input_message_id, message_id, url):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO jobs(user_id,chat_id,input_message_id,message_id,url,platform,resource_id,created) VALUES(?,?,?,?,?,?,?,?)',
                (user_id,chat_id,input_message_id,message_id,url.url,url.platform,url.resource_id,time.time()))
            return db.execute('SELECT id FROM jobs WHERE chat_id=? AND input_message_id=?',(chat_id,input_message_id)).fetchone()[0]

    def execution(self, job_id, action):
        with self.db() as db:
            # Random positive SQLite ids prevent reusing a worker execution after backup restore.
            eid = int.from_bytes(os.urandom(8),'big') & ((1<<63)-1)
            while not eid or db.execute('SELECT 1 FROM executions WHERE id=?',(eid,)).fetchone():
                eid=int.from_bytes(os.urandom(8),'big') & ((1<<63)-1)
            db.execute('INSERT INTO executions(id,job_id,action) VALUES(?,?,?)',(eid,job_id,action))
            db.execute('UPDATE jobs SET execution_id=? WHERE id=?',(eid,job_id))
            return eid

    def rate_allow(self, user, cfg, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            ban = db.execute('SELECT until FROM bans_v1 WHERE user_id=?',(user,)).fetchone()
            if ban and ban[0] > now:
                return False
            db.execute('DELETE FROM attempts WHERE at<=?',(now-cfg['window_seconds'],))
            count = db.execute('SELECT count(*) FROM attempts WHERE user_id=?',(user,)).fetchone()[0]
            if count >= cfg['link_attempts']:
                db.execute('INSERT OR REPLACE INTO bans_v1 VALUES(?,?)',(user,now+cfg['ban_duration_seconds']))
                return False
            db.execute('INSERT INTO attempts VALUES(?,?)',(user,now))
            return True

    def intake(self,user,chat,input_id,url,cfg):
        now=time.time()
        with self.db() as db:
            if db.execute('SELECT 1 FROM received_updates WHERE chat_id=? AND message_id=?',(chat,input_id)).fetchone():
                return 'duplicate',None
            ban=db.execute('SELECT until FROM bans_v1 WHERE user_id=?',(user,)).fetchone()
            db.execute('DELETE FROM attempts WHERE at<=?',(now-cfg['window_seconds'],))
            count=db.execute('SELECT count(*) FROM attempts WHERE user_id=?',(user,)).fetchone()[0]
            if ban and ban[0]>now:
                result='blocked'
            elif count>=cfg['link_attempts']:
                db.execute('INSERT OR REPLACE INTO bans_v1 VALUES(?,?)',(user,now+cfg['ban_duration_seconds']))
                result='blocked'
            else:
                db.execute('INSERT INTO attempts VALUES(?,?)',(user,now))
                result='accepted' if url else 'invalid'
            db.execute('INSERT INTO received_updates VALUES(?,?,?)',(chat,input_id,result))
            jid=None
            if result=='accepted':
                jid=db.execute('INSERT INTO jobs(user_id,chat_id,input_message_id,message_id,url,platform,resource_id,created) VALUES(?,?,?,?,?,?,?,?)',
                    (user,chat,input_id,0,url.url,url.platform,url.resource_id,now)).lastrowid
            return result,jid

    def used(self, user):
        return self.rows('SELECT count(*) AS n FROM quota WHERE user_id=? AND day=?',(user,day()))[0]['n']

    def notice(self, code, text):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO notices(code,text) VALUES(?,?)',(code,text))

    def delivered(self, job_id, file_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row['phase'] == 'completed':
                return False
            db.execute("UPDATE jobs SET phase='completed',file_id=? WHERE id=?",(file_id,job_id))
            if not row['quota_exempt'] and not row['cache_hit']:
                db.execute('INSERT OR IGNORE INTO quota VALUES(?,?,?)',(job_id,row['user_id'],day()))
            db.execute('INSERT OR REPLACE INTO result_cache VALUES(?,?,1,?)',(row['cache_key'],file_id,row['metadata']))
            return True

    def select(self, job_id, user, chat, message, code, max_pending):
        with self.db() as db:
            r = db.execute('SELECT * FROM jobs WHERE id=? AND user_id=? AND chat_id=? AND message_id=?',(job_id,user,chat,message)).fetchone()
            if not r or r['phase'] != 'card' or r['cancel_requested']:
                return False
            meta = json.loads(r['metadata'])
            allowed = [x['code'] for x in meta.get('variants', [])] or ['video','audio']
            if code not in allowed:
                return False
            key = cache_key(r['platform'],r['resource_id'],code,meta.get('language',''))
            cache = db.execute('SELECT file_id FROM result_cache WHERE key=? AND valid=1',(key,)).fetchone()
            if not cache and db.execute("SELECT count(*) FROM jobs WHERE phase='queued'").fetchone()[0] >= max_pending:
                raise OverflowError('Очередь заполнена. Попробуйте позже.')
            db.execute('UPDATE jobs SET download_attempts=0,upload_attempts=0,next_upload=0,ready_file=NULL,error=NULL,format=?,cache_key=?,phase=?,queued=?,cache_hit=?,quota_exempt=?,file_id=? WHERE id=?',
                (code,key,'upload_pending' if cache else 'queued',time.time(),bool(cache),bool(cache),cache[0] if cache else None,job_id))
            return True
