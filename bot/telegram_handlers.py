"""Telegram commands and conservative link intake."""
from .url_validation import validate_url, URLValidationError

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

router = Router()


def validate_media_url(value: str) -> bool:
    try:
        validate_url(value)
        return True
    except URLValidationError:
        return False


@router.message(Command("start"))
async def start(message: Message) -> None:
    await message.answer("Привет! Отправь HTTPS-ссылку на YouTube, Instagram или TikTok для анализа.")



@router.message(Command("cancel"))
async def cancel_command(message: Message) -> None:
    """Cancel one owned task by ID; never disclose another user's tasks."""
    if message.from_user is None:
        return
    parts = (message.text or "").split()
    if (len(parts) != 2 or not parts[1].isascii()
            or not parts[1].isdecimal() or len(parts[1]) > 19):
        await message.answer("Использование: /cancel ID_задачи")
        return
    task_id = int(parts[1])
    if task_id <= 0 or task_id > 9223372036854775807:
        await message.answer("Некорректный номер задачи")
        return
    from pathlib import Path
    from .storage import database, cancel_task
    with database(Path("data/bot.sqlite")) as db:
        cancelled = cancel_task(db, task_id=task_id, user_id=message.from_user.id)
    await message.answer(
        f"❌ Задача №{task_id} отменена."
        if cancelled else "Задача не найдена, не принадлежит вам или уже завершена."
    )

@router.message(Command("myid"))
async def myid(message: Message) -> None:
    if message.from_user:
        await message.answer(f"Твой Telegram ID: {message.from_user.id}")


@router.message(F.text)
async def link(message: Message) -> None:
    value = (message.text or "").strip()
    if not validate_media_url(value):
        await message.answer("Отправь одну HTTPS-ссылку на YouTube, Instagram или TikTok.")
        return
    from .metadata import AnalysisError, inspect
    from .media_admission import admit_inspect, AdmissionDenied
    from .config import load_settings
    settings = load_settings()
    progress = await message.answer("⏳ Анализирую ссылку…")
    try:
        approved = admit_inspect(value)
        info = await inspect(approved.url, timeout=settings.data["timeouts"]["analysis_timeout_seconds"])
    except (AnalysisError, AdmissionDenied) as exc:
        await progress.edit_text("⚠️ Не удалось определить метаданные. Можно попробовать скачать видео или аудио.")
        info = {}
    from .media_variants import size_label
    duration = info.get("duration")
    duration_text = (f"{int(duration) // 60}:{int(duration) % 60:02d}"
                     if isinstance(duration, (int, float)) and not isinstance(duration, bool)
                     and duration >= 0 else "неизвестна")
    date = info.get("upload_date", "")
    published = f"{date[6:8]}.{date[4:6]}.{date[:4]}" if len(date) == 8 and date.isdigit() else "неизвестна"
    views = info.get("view_count")
    views_text = f"{views:,}".replace(",", " ") if isinstance(views, int) and views >= 0 else "неизвестно"
    uploader = info.get("uploader") or "неизвестен"
    buttons = []
    choices = []
    for variant in info.get("variants", []):
        quality = variant["quality"]
        kind = variant["kind"]
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0:
            limit = settings.data["limits"]["max_audio_duration_seconds" if kind == "mp3" else "max_video_duration_seconds"]
            if duration > limit:
                continue
        size = size_label(variant["size"], variant["approximate"])
        label = f"🎵 MP3 ({size})" if kind == "mp3" else f"{quality} ({size})"
        choices.append(InlineKeyboardButton(text=label, callback_data=f"preview:{quality}"))
    if not choices:
        choices = [
            InlineKeyboardButton(text="Скачать видео", callback_data="preview:video"),
            InlineKeyboardButton(text="Скачать аудио", callback_data="preview:audio"),
        ]
    for index in range(0, len(choices), 2):
        buttons.append(choices[index:index + 2])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="preview:cancel")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    card = (f"{info.get('title') or 'Видео'} | {uploader}\n"
            f"Дата публикации: {published}\n"
            f"Продолжительность: {duration_text}\n"
            f"Просмотров: {views_text}\n\n"
            f"Выбранный язык: {info.get('language', 'Язык аудио не определён')}\n\n"
            "Выберите качество:" if len(buttons) > 1 else
            f"{info.get('title') or 'Видео'}\nДоступные качества не определены.")
    # Do not ask Telegram to fetch arbitrary third-party URLs.
    # Only YouTube's dedicated thumbnail CDN is accepted for this preview.
    thumbnail = info.get("thumbnail", "")
    from urllib.parse import urlsplit as _split
    try:
        thumb_url = _split(thumbnail)
        trusted_thumbnail = (thumb_url.scheme == "https" and
                             thumb_url.hostname in ("i.ytimg.com", "img.youtube.com") and
                             not thumb_url.username and not thumb_url.password and
                             thumb_url.port in (None, 443))
    except ValueError:
        trusted_thumbnail = False
    sent = None
    if trusted_thumbnail and len(card) <= 1024:
        try:
            sent = await message.answer_photo(photo=thumbnail, caption=card, reply_markup=keyboard)
            try:
                await progress.delete()
            except Exception:
                pass
        except Exception:
            # Thumbnail failures should not prevent a metadata card.
            pass
    if sent is None:
        await progress.edit_text(card, reply_markup=keyboard)
        sent = progress
    if message.from_user:
        from pathlib import Path
        from .storage import database, save_preview
        with database(Path("data/bot.sqlite")) as db:
            save_preview(db, chat_id=sent.chat.id, message_id=sent.message_id,
                         user_id=message.from_user.id, source_url=value,
                         media_key=f"{info.get('extractor', '')}:{value}",
                         variants=[button.callback_data.removeprefix("preview:") for button in choices])


@router.callback_query(F.data.in_({"preview:480p", "preview:720p", "preview:1080p", "preview:MP3", "preview:video", "preview:audio"}))
async def preview_format(callback: CallbackQuery) -> None:
    from pathlib import Path
    from .storage import database, select_preview, enqueue_task
    from .media_admission import admit_download, AdmissionDenied
    from .config import load_settings
    if not callback.message:
        await callback.answer("Карточка недоступна", show_alert=True)
        return
    variant = (callback.data or "").removeprefix("preview:")
    queue_full = False
    with database(Path("data/bot.sqlite")) as db:
        selection = select_preview(
            db, chat_id=callback.message.chat.id, message_id=callback.message.message_id,
            user_id=callback.from_user.id, variant=variant)
        if selection is None:
            task_id = None
            created = False
        else:
            url, media_key = selection
            try:
                task_id, created = enqueue_task(
                    db,
                    max_pending=load_settings().data["limits"]["max_pending_tasks"],
                    user_id=callback.from_user.id,
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    callback_key=f"{callback.message.chat.id}:{callback.message.message_id}:{callback.from_user.id}:{variant}",
                    source_url=url,
                    media_key=media_key,
                    variant=variant,
                )
                try:
                    admit_download(db, task_id=task_id)
                except AdmissionDenied:
                    if created:
                        db.execute("DELETE FROM tasks WHERE id=? AND status='queued'", (task_id,))
                    task_id, created = None, False
            except OverflowError:
                task_id, created = None, False
                queue_full = True
    if queue_full:
        await callback.answer("Очередь заполнена. Попробуйте позже.", show_alert=True)
    elif task_id is None:
        await callback.answer("Эта кнопка недоступна или карточка отменена", show_alert=True)
    else:
        note = "Выбор сохранён" if created else "Выбор уже сохранён"
        await callback.answer(note)
        status = f"⏳ Выбрано: {variant}\nЗадача №{task_id} зарегистрирована.\nСкачивание пока отключено."
        if callback.message.photo:
            await callback.message.edit_caption(caption=status)
        else:
            await callback.message.edit_text(status)


@router.callback_query(F.data == "preview:cancel")
async def cancel_preview(callback: CallbackQuery) -> None:
    from pathlib import Path
    from .storage import database, cancel_preview_record
    if not callback.message:
        await callback.answer("Карточка недоступна", show_alert=True)
        return
    with database(Path("data/bot.sqlite")) as db:
        cancelled = cancel_preview_record(
            db, chat_id=callback.message.chat.id,
            message_id=callback.message.message_id, user_id=callback.from_user.id)
    if not cancelled:
        await callback.answer("Нельзя отменить чужую или уже отменённую карточку", show_alert=True)
        return
    if callback.message.photo:
        await callback.message.edit_caption(caption="❌ Выбор отменён")
    else:
        await callback.message.edit_text("❌ Выбор отменён")
    await callback.answer("Выбор отменён")
