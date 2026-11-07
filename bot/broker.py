"""Single admission dispatcher and durable bot-side scheduling."""
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import shutil
import time
from .client import WorkerUnavailable
from .files import checked, clean_task, private_dir, UnsafeFile
from .state import TERMINAL,cache_key
from .urls import validate_url

class Broker:
    def __init__(self, store, client, config, telegram):
        self.store, self.client, self.config, self.telegram = store, client, config, telegram
        self.last_backup = time.time()
        self.uploads = {}

    def accept(self,user,chat,input_id,raw):
        try:
            u=validate_url(raw)
            error=''
        except ValueError as exc:
            u=None
            error=str(exc)
        result,jid=self.store.intake(user,chat,input_id,u,self.config['rate_limit'])
        return result,jid,error

    def payload(self, job, action):
        u = validate_url(job['url'])
        if (u.platform,u.resource_id) != (job['platform'],job['resource_id']):
            raise ValueError('stored_resource_changed')
        p = {'url':u.url,'platform':u.platform,'resource_id':u.resource_id}
        if action == 'download':
            allowed = [v['code'] for v in json.loads(job['metadata']).get('variants',[])] or ['video','audio']
            if job['format'] not in allowed:
                raise ValueError('stored_format_changed')
            p['format'] = job['format']
        return p

    async def status(self, job, text, keyboard=None):
        if job['last_status'] == text and keyboard is None:
            return
        try:
            mid = await self.telegram.status(job['chat_id'],job['message_id'],text,keyboard)
            self.store.update(job['id'], last_status=text, message_id=mid)
        except Exception:
            logging.warning('Не удалось обновить статус задачи %s', job['id'])

    async def card(self, job, meta, failure=False):
        # Worker responses are untrusted; filter all data before saving or displaying.
        clean = {}
        for k, limit in [('title',200),('uploader',100),('language',40),('upload_date',8),('thumbnail',2048)]:
            v = meta.get(k)
            clean[k] = ''.join(c for c in v if c >= ' ' and c != '\x7f')[:limit] if type(v) is str else ''
        for k in ('duration','view_count'):
            v = meta.get(k)
            clean[k] = v if type(v) in (int,float) and 0 <= v < 10**15 else None
        clean['variants'] = []
        for v in meta.get('variants',[]) if type(meta.get('variants')) is list else []:
            if type(v) is dict and type(v.get('code')) is str and v.get('code') in {'480p','720p','1080p','MP3'}:
                s = v.get('size')
                clean['variants'].append({'code':v['code'],'size':s if type(s) is int and s > 0 else None,'approximate':True,'peak_bytes':v.get('peak_bytes') if type(v.get('peak_bytes')) is int and v['peak_bytes']>0 else None})
        duration = clean['duration']
        if duration and duration > max(self.config['limits']['max_video_duration_seconds'],self.config['limits']['max_audio_duration_seconds']):
            return await self.fail(job,'Материал превышает допустимую продолжительность.')
        if clean['variants'] and duration:
            clean['variants'] = [v for v in clean['variants'] if duration <= self.config['limits']['max_audio_duration_seconds' if v['code']=='MP3' else 'max_video_duration_seconds']]
            if not clean['variants']:
                return await self.fail(job,'Материал превышает допустимую продолжительность.')
        self.store.update(job['id'], metadata=json.dumps(clean), phase='card')
        job = self.store.get(job['id'])
        variants = clean['variants'] or [{'code':'video','size':None},{'code':'audio','size':None}]
        keyboard = []
        for v in variants:
            code = v['code']
            label = {'video':'Скачать видео','audio':'Скачать аудио'}.get(code,code)
            if v['size']:
                label += f" ≈ {v['size']/1024**2:.1f} МиБ"
            key=cache_key(job['platform'],job['resource_id'],code,clean.get('language',''))
            if self.store.rows('SELECT 1 FROM result_cache WHERE key=? AND valid=1',(key,)):
                label+=' ✓ из кеша'
            keyboard.append((label,code))
        prefix = 'Анализ недоступен. Можно попробовать загрузку без предварительных сведений.\n' if failure or not clean['variants'] else ''
        duration_text = f"{int(duration)//60}:{int(duration)%60:02}" if duration else 'неизвестна'
        text = (prefix + f"{clean['title'] or 'Видео'}\n{clean['uploader']}\nПродолжительность: {duration_text}\n"
            + f"Язык аудио: {clean['language'] or 'не определён'}\nВыберите результат:")
        if hasattr(self.telegram,'card'):
            try:
                mid=await self.telegram.card(job,text,keyboard,clean['thumbnail'])
                self.store.update(job['id'],message_id=mid,last_status=text)
                return
            except Exception:
                pass
        await self.status(job,text,keyboard)

    async def alternatives(self,job,reason):
        meta=json.loads(job['metadata'])
        limit=self.config['telegram']['max_upload_size_mb'][self.config['telegram']['api_mode']]*1024**2
        variants=[v for v in meta.get('variants',[]) if v['code']!=job['format']]
        if reason=='file_too_large':
            variants=[v for v in variants if type(v.get('size')) is int and v['size']<=limit]
        if not variants:
            return False
        self.remove_files(job)
        meta['variants']=variants
        self.store.update(job['id'],ready_file=None)
        await self.card(job,meta)
        return True

    async def fail(self, job, message, code='failed'):
        self.store.update(job['id'],phase='failed',error=code)
        self.remove_files(job)
        await self.status(self.store.get(job['id']), 'Ошибка: '+message)

    def remove_files(self, job):
        if job.get('execution_id'):
            clean_task(job['execution_id'])
        if job.get('ready_file') or (job.get('execution_id') and job.get('format')):
            name=f"{job['execution_id']}.{'mp3' if job['format'] in {'MP3','audio'} else 'mp4'}"
            p = Path(job['ready_file']) if job.get('ready_file') else Path(self.config['storage']['files_dir'])/name
            root = Path(self.config['storage']['files_dir']).absolute()
            if p.absolute().parent == root:
                p.unlink(missing_ok=True)
                p.with_suffix('.json').unlink(missing_ok=True)

    def ready(self, job, result=None):
        eid = job['execution_id']
        audio = job['format'] in {'audio','MP3'}
        p = private_dir(self.config['storage']['files_dir']) / f'{eid}.{ "mp3" if audio else "mp4"}'
        limit = self.config['telegram']['max_upload_size_mb'][self.config['telegram']['api_mode']] * 1024**2
        checked(p,p.parent,limit)
        manifest = checked(p.with_suffix('.json'),p.parent,65536)
        m = json.loads(manifest.read_text())
        expected = {'filename','size','duration','format','validated','sha256'}
        if set(m) != expected or m['filename'] != p.name or m['validated'] is not True or m['format'] != p.suffix[1:] or m['size'] != p.stat().st_size:
            raise UnsafeFile('invalid_manifest')
        d = m['duration']
        if type(d) not in (int,float) or not 0 < d <= self.config['limits']['max_audio_duration_seconds' if audio else 'max_video_duration_seconds']:
            raise UnsafeFile('invalid_duration')
        with p.open('rb') as stream:
            digest = hashlib.file_digest(stream,'sha256').hexdigest()
        if digest != m['sha256'] or (result is not None and m != result):
            raise UnsafeFile('invalid_result')
        return str(p)

    async def cancel_all(self, user):
        rows = self.store.rows("SELECT * FROM jobs WHERE user_id=? AND phase NOT IN ('completed','failed','cancelled')",(user,))
        pending = 0
        for job in rows:
            self.store.update(job['id'],cancel_requested=1)
            if job['id'] in self.uploads:
                # In-flight Bot API delivery can be accepted. Await result, suppress future retries.
                pending += 1
                continue
            if not await self.cancel(self.store.get(job['id'])):
                pending += 1
        return len(rows)-pending,pending

    async def cancel(self, job):
        if job['phase'] in {'inspecting','running'}:
            try:
                result = await self.client.call(job['execution_id'],'cancel')
            except WorkerUnavailable:
                return False
            if result['status'] not in {'cancelled','not_found','completed','failed'}:
                return False
        self.store.update(job['id'],phase='cancelled')
        self.remove_files(job)
        await self.status(self.store.get(job['id']),'Задача отменена.')
        return True

    async def submit(self, job, action):
        # Persist execution identity BEFORE IPC; a lost reply is reconciled by status.
        payload = self.payload(job,action)
        eid = self.store.execution(job['id'], action)
        values = {'phase':'inspecting' if action=='inspect' else 'running'}
        if action=='download':
            values['download_attempts'] = job['download_attempts']+1
        self.store.update(job['id'],**values)
        try:
            reply = await self.client.call(eid,action,payload)
        except WorkerUnavailable:
            return
        await self.reconcile(self.store.get(job['id']),reply)

    async def reconcile(self, job, reply):
        state = reply['status']
        if state in {'accepted','running'}:
            stage = reply.get('result',{}).get('stage','')
            text = 'Анализирую ссылку…' if job['phase']=='inspecting' else 'Конвертация MP3…' if stage=='conversion' else 'Обработка/объединение…' if stage=='processing' else f"Скачивание, попытка {job['download_attempts']}…"
            await self.status(job,text)
            return
        if job['cancel_requested']:
            await self.cancel(job)
            return
        if job['phase']=='inspecting':
            if state=='not_found':
                # It may be a lost admission, or a worker restart. Safe to resubmit inspect.
                self.store.update(job['id'],phase='inspect')
            else:
                await self.card(job,reply.get('result',{}) if state=='completed' else {},state!='completed')
            return
        if state in {'completed','not_found'}:
            try:
                ready = await asyncio.to_thread(self.ready,job,reply.get('result') if state=='completed' else None)
            except (OSError,ValueError):
                self.remove_files(job)
                if state=='completed':
                    return await self.fail(job,'Результат не прошёл проверку.','invalid_result')
            else:
                self.store.update(job['id'],phase='upload_pending',ready_file=ready)
                await self.status(self.store.get(job['id']),'Файл готов к отправке.')
                return
        err = reply.get('error',{})
        if err.get('code') in {'file_too_large','quality_unavailable'} and await self.alternatives(job,err['code']):
            return
        if err.get('code')=='worker_busy':
            # Not admitted: do not consume a download attempt.
            self.store.update(job['id'],download_attempts=max(0,job['download_attempts']-1),phase='queued')
            return
        if err.get('code')=='disk_full':
            self.store.notice('disk_full','Диск заполнен; задача остановлена, временные файлы удалены.')
        if state=='not_found' or not err.get('permanent',False):
            if job['download_attempts'] < self.config['max_download_attempts']:
                clean_task(job['execution_id'])
                self.store.update(job['id'],phase='queued')
                return
        await self.fail(job,'Скачать выбранный вариант не удалось. Вы можете отправить ссылку заново и выбрать другой вариант.',err.get('code','download_failed'))

    def disk_allows(self, job, free=None):
        free = shutil.disk_usage(private_dir('data/tmp')).free if free is None else free
        meta = json.loads(job['metadata'])
        variant = next((v for v in meta.get('variants',[]) if v['code']==job['format']),{})
        peak = variant.get('peak_bytes')
        if type(peak) is not int or peak <= 0:
            return free > self.config['storage']['unknown_size_fallback_mib']*1024**2
        return free >= peak

    async def tick(self):
        rows = self.store.rows("SELECT * FROM jobs WHERE phase NOT IN ('completed','failed','cancelled') ORDER BY created,id")
        unresolved = False
        for job in rows:
            if job['cancel_requested'] and job['id'] not in self.uploads:
                if not await self.cancel(job):
                    unresolved = True
                continue
            if job['phase'] in {'running','inspecting'}:
                try:
                    reply = await self.client.call(job['execution_id'],'status')
                except WorkerUnavailable:
                    unresolved = True
                    continue
                await self.reconcile(job,reply)
        rows = self.store.rows("SELECT * FROM jobs WHERE phase NOT IN ('completed','failed','cancelled') ORDER BY created,id")
        inspections = sum(j['phase']=='inspecting' for j in rows)
        for job in rows:
            if job['phase']=='inspect' and not job['cancel_requested'] and inspections < self.config['worker']['max_inspects']:
                await self.submit(job,'inspect')
                inspections += 1
        active = any(j['phase']=='running' for j in rows)
        queued = sorted((j for j in rows if j['phase']=='queued'),key=lambda j:(j['queued'],j['id']))
        for pos, job in enumerate(queued,1):
            if job['cancel_requested']:
                continue
            if not job['download_attempts'] and time.time()-job['queued'] >= self.config['limits']['queue_wait_timeout_minutes']*60:
                await self.fail(job,'Истекло время ожидания в очереди.','queue_timeout')
                continue
            await self.status(job,f'В очереди: позиция {pos}.')
            if job['download_attempts'] >= self.config['max_download_attempts']:
                await self.fail(job,'Исчерпаны попытки скачивания.','attempts_exhausted')
                continue
            if active or unresolved:
                continue
            # Reserve remaining daily slots across already-prepared deliveries.
            waiting = self.store.rows("SELECT count(*) AS n FROM jobs WHERE user_id=? AND phase IN ('running','upload_pending','uploading') AND quota_exempt=0 AND cache_hit=0",(job['user_id'],))[0]['n']
            if not job['quota_exempt'] and job['user_id'] not in self.config['trusted_users'] and self.store.used(job['user_id'])+waiting >= self.config['limits']['daily_download_limit']:
                await self.fail(job,'Дневной лимит исчерпан.','quota')
                continue
            if not self.disk_allows(job):
                self.store.notice('low_disk','Недостаточно свободного места для ожидающей задачи.')
                continue
            await self.submit(job,'download')
            active = True
        # Delivery is independent of the single active download.
        for job in self.store.rows("SELECT * FROM jobs WHERE phase IN ('upload_pending','uploading') ORDER BY id"):
            if job['id'] not in self.uploads and not self.uploads and job['next_upload'] <= time.time():
                t = asyncio.create_task(self.upload(job))
                self.uploads[job['id']] = t
        for jid,t in list(self.uploads.items()):
            if t.done():
                try:
                    t.result()
                except Exception:
                    logging.exception('Ошибка доставки задачи %s',jid)
                del self.uploads[jid]
        for notice in self.store.rows('SELECT * FROM notices'):
            try:
                await self.telegram.notify(self.config['admin_telegram_id'],notice['text'])
            except Exception:
                break
            with self.store.db() as db:
                db.execute('DELETE FROM notices WHERE id=?',(notice['id'],))
        if time.time()-self.last_backup >= self.config['backup']['interval_hours']*3600:
            self.store.backup(self.config['backup']['max_copies'])
            self.last_backup = time.time()

    async def upload(self, job):
        from .telegram import DeliveryError
        if job['cancel_requested']:
            return await self.cancel(job)
        if job['upload_attempts'] >= self.config['telegram_upload_max_attempts']:
            return await self.fail(job,'Исчерпаны попытки отправки.','upload_attempts_exhausted')
        attempt = job['upload_attempts']+1
        self.store.update(job['id'],phase='uploading',upload_attempts=attempt)
        await self.status(self.store.get(job['id']),f'Отправка, попытка {attempt}…')
        try:
            source = job['file_id'] if job['cache_hit'] else await asyncio.to_thread(self.ready,job)
            file_id = await self.telegram.send(job,source,cache=bool(job['cache_hit']))
        except DeliveryError as exc:
            job = self.store.get(job['id'])
            if job['cancel_requested']:
                return await self.cancel(job)
            if exc.invalid_cache and job['cache_hit']:
                with self.store.db() as db:
                    db.execute('UPDATE result_cache SET valid=0 WHERE key=?',(job['cache_key'],))
                count=self.store.rows("SELECT count(*) AS n FROM jobs WHERE phase='queued'")[0]['n']
                if count >= self.config['limits']['max_pending_tasks']:
                    return await self.fail(job,'Кэш недоступен, очередь заполнена. Попробуйте позже.','queue_full')
                self.store.update(job['id'],phase='queued',cache_hit=0,file_id=None,upload_attempts=0,queued=time.time())
                return
            if exc.permanent or attempt >= self.config['telegram_upload_max_attempts']:
                return await self.fail(job,'Не удалось отправить файл.','upload_failed')
            delay = self.config['telegram_upload_second_attempt_delay_seconds' if attempt==1 else 'telegram_upload_third_attempt_delay_seconds']
            self.store.update(job['id'],phase='upload_pending',next_upload=time.time()+max(delay,exc.retry_after))
        except (OSError,ValueError):
            self.remove_files(job)
            if job['download_attempts'] < self.config['max_download_attempts'] and not job['cancel_requested']:
                self.store.update(job['id'],phase='queued',ready_file=None,upload_attempts=job['upload_attempts'])
            else:
                await self.fail(job,'Готовый файл отсутствует или повреждён.','unsafe_ready')
        else:
            self.store.delivered(job['id'],file_id)
            job = self.store.get(job['id'])
            if self.config['storage']['keep_downloaded_files']:
                if job['execution_id']:
                    clean_task(job['execution_id'])
            else:
                self.remove_files(job)
            left = 'без ограничений' if job['user_id'] in self.config['trusted_users'] else str(max(0,self.config['limits']['daily_download_limit']-self.store.used(job['user_id'])))
            await self.status(job,f'Готово. Остаток дневного лимита: {left}.')

    async def run(self, stop):
        while not stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop.wait(),timeout=1)
            except TimeoutError:
                pass
        if self.uploads:
            await asyncio.gather(*self.uploads.values(),return_exceptions=True)
