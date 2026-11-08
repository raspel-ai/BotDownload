"""Telegram transport in exactly one configured mode, plus thin user handlers."""
import asyncio
import logging
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.filters import Command
from aiogram.types import BotCommand, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.backoff import BackoffConfig
from .urls import validate_url, InvalidURL

class DeliveryError(Exception):
    def __init__(self, permanent=False, retry_after=0, invalid_cache=False):
        self.permanent, self.retry_after, self.invalid_cache = permanent,retry_after,invalid_cache

def keyboard(buttons):
    if buttons is None:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t,callback_data=c)] for t,c in buttons])

class Telegram:
    def __init__(self, settings):
        c = settings.data['telegram']
        self.mode = c['api_mode']
        api = TelegramAPIServer.from_base(c['api_base_urls'][self.mode],is_local=self.mode=='local')
        self.bot = Bot(settings.token,session=AiohttpSession(api=api,timeout=3600))
        self.retry_config = c['reconnect']
        self.failed_since = None
        self.admin = settings.data['admin_telegram_id']
        self.on_recovered=None
        self.status_after=0
        async def track(make_request,bot,method):
            try:
                result=await make_request(bot,method)
            except (TelegramNetworkError,TelegramServerError):
                self.failed_since=self.failed_since or time.time()
                raise
            else:
                if self.failed_since and time.time()-self.failed_since>=300 and self.on_recovered:
                    self.on_recovered()
                self.failed_since=None
                return result
        self.bot.session.middleware(track)

    async def status(self, chat, message, text, buttons=None):
        if time.time()<self.status_after:
            raise DeliveryError(retry_after=self.status_after-time.time())
        try:
            await self.bot.edit_message_text(text,chat_id=chat,message_id=message,reply_markup=keyboard(buttons),request_timeout=20)
            return message
        except TelegramRetryAfter as exc:
            self.status_after=time.time()+exc.retry_after
            raise
        except TelegramBadRequest as exc:
            if 'message is not modified' in str(exc).lower():
                return message
            if 'no text' in str(exc).lower() or 'message is not modified' in str(exc).lower():
                await self.bot.edit_message_caption(chat_id=chat,message_id=message,caption=text,reply_markup=keyboard(buttons),request_timeout=20)
                return message
            sent = await self.bot.send_message(chat,text,reply_markup=keyboard(buttons),request_timeout=20)
            return sent.message_id

    async def card(self,job,text,buttons,thumbnail):
        try:
            u=urlsplit(thumbnail)
            host=u.hostname or ''
            allowed=host in {'i.ytimg.com','img.youtube.com'} or any(host.endswith('.'+suffix) for suffix in ('cdninstagram.com','fbcdn.net','tiktokcdn.com','tiktokcdn-us.com','ibytedtos.com'))
            if not allowed or u.scheme!='https' or u.username or u.password or u.port is not None or len(text)>1024:
                raise ValueError
            sent=await self.bot.send_photo(job['chat_id'],thumbnail,caption=text,reply_markup=keyboard(buttons),request_timeout=20)
            try:
                await self.bot.edit_message_text('Анализ завершён; карточка ниже.',chat_id=job['chat_id'],message_id=job['message_id'],request_timeout=20)
            except Exception:
                pass
            return sent.message_id
        except (ValueError,TelegramBadRequest):
            return await self.status(job['chat_id'],job['message_id'],text,buttons)

    async def notify(self,user,text):
        await self.bot.send_message(user,text,request_timeout=20)

    async def send(self,job,source,*,cache=False):
        try:
            media = source if cache else FSInputFile(source)
            if job['format'] in {'MP3','audio'}:
                sent = await self.bot.send_audio(job['chat_id'],media,request_timeout=3600)
                return sent.audio.file_id
            sent = await self.bot.send_video(job['chat_id'],media,supports_streaming=True,request_timeout=3600)
            return sent.video.file_id
        except TelegramRetryAfter as exc:
            raise DeliveryError(retry_after=exc.retry_after) from None
        except TelegramBadRequest as exc:
            text = str(exc).lower()
            invalid = cache and any(s in text for s in ('wrong file identifier','file_id','file reference','file not found','wrong remote file'))
            raise DeliveryError(permanent=not invalid,invalid_cache=invalid) from None
        except TelegramForbiddenError:
            raise DeliveryError(permanent=True) from None
        except (TelegramNetworkError,TelegramServerError,OSError):
            raise DeliveryError() from None

    async def reconnect(self, stop, operation):
        c = self.retry_config
        delay = c['initial_delay_seconds']
        while not stop.is_set():
            try:
                result = await operation()
            except TelegramRetryAfter as exc:
                wait = max(delay,exc.retry_after)
            except (TelegramNetworkError,TelegramServerError,OSError):
                wait = delay
            else:
                if self.failed_since and time.time()-self.failed_since >= 300:
                    try:
                        await self.notify(self.admin,'Соединение с Telegram восстановлено после длительного сбоя.')
                    except Exception:
                        pass
                self.failed_since = None
                return result
            self.failed_since = self.failed_since or time.time()
            try:
                await asyncio.wait_for(stop.wait(),timeout=wait)
            except TimeoutError:
                pass
            delay = min(c['max_delay_seconds'],delay*c['backoff_multiplier'])

    async def polling(self,broker,stop):
        store,cfg = broker.store,broker.config
        router = Router()
        @router.message(Command('start','help'))
        async def start(m):
            await m.answer('Отправьте одну HTTPS-ссылку на видео YouTube, Instagram или TikTok.\n/myid — ваш ID\n/cancel_all — отменить все ваши незавершённые задачи.')
        @router.message(Command('myid'))
        async def myid(m):
            if m.from_user:
                await m.answer(f'Ваш Telegram ID: {m.from_user.id}')
        @router.message(Command('cancel_all'))
        async def cancel(m):
            if m.from_user:
                done,pending = await broker.cancel_all(m.from_user.id)
                await m.answer(f'Отменено: {done}. Ожидают подтверждения остановки: {pending}.')
        @router.message(F.text)
        async def link(m):
            if not m.from_user or (m.text or '').startswith('/'):
                return
            value = m.text or ''
            if not re.search(r'(?:https?://|www\.|youtube\.|youtu\.be|tiktok\.|instagram\.)',value,re.I):
                await m.answer('Отправьте одну HTTPS-ссылку на видео.')
                return
            result,jid,error=broker.accept(m.from_user.id,m.chat.id,m.message_id,value)
            if result=='duplicate':
                return
            if result=='blocked':
                await m.answer('Превышен лимит ссылок. Новые ссылки временно заблокированы. /cancel_all и /myid доступны.')
            elif result=='invalid':
                await m.answer(error)
            # Accepted jobs are persisted before any Telegram send; the scheduler
            # creates the status message and binds its identity durably.
        @router.callback_query()
        async def choose(q):
            if not q.message:
                return
            rows = store.rows('SELECT id FROM jobs WHERE chat_id=? AND message_id=? AND user_id=?',(q.message.chat.id,q.message.message_id,q.from_user.id))
            if not rows:
                await q.answer('Карточка недоступна.',show_alert=True)
                return
            try:
                ok = store.select(rows[0]['id'],q.from_user.id,q.message.chat.id,q.message.message_id,q.data,cfg['limits']['max_pending_tasks'])
            except OverflowError as exc:
                await q.answer(str(exc),show_alert=True)
                return
            await q.answer('Выбор принят.' if ok else 'Выбор уже принят или недоступен.')
            if ok:
                await broker.status(store.get(rows[0]['id']),'Выбор принят; подготовка результата…',[])
        dispatcher = Dispatcher()
        dispatcher.include_router(router)
        await self.reconnect(stop,self.bot.get_me)
        if stop.is_set():
            return
        await self.reconnect(stop,lambda:self.bot.set_my_commands([BotCommand(command='start',description='Помощь'),BotCommand(command='myid',description='Мой Telegram ID'),BotCommand(command='cancel_all',description='Отменить все мои задачи')]))
        while not stop.is_set():
            try:
                await dispatcher.start_polling(self.bot,close_bot_session=False,handle_signals=False,
                    polling_timeout=30,backoff_config=BackoffConfig(min_delay=self.retry_config['initial_delay_seconds'],max_delay=self.retry_config['max_delay_seconds'],factor=self.retry_config['backoff_multiplier'],jitter=0))
            except (TelegramNetworkError,TelegramServerError):
                await self.reconnect(stop,self.bot.get_me)
            else:
                break
