# Проверки v1 — 2026-11-07

Ненормативный протокол; требования задаются только ТЗ и политикой безопасности.
Публичный запуск не разрешён до обязательной внешней приёмки.

Среда: Debian 13.6 (контейнер, не VPS с systemd), Python 3.13.5,
FFmpeg/ffprobe 7.1.5, aiogram 3.22.0, yt-dlp 2026.10.26, yt-dlp-ejs 0.8.0,
Deno 2.9.8. Дополнительно регрессия выполнялась на Python 3.12.14.

| Обязательная проверка | Результат | Доказательство / граница |
|---|---|---|
| ТЗ §4; политика §1,11: позитивные/негативные URL, отсутствие DNS в валидаторе | PASS | `tests/test_urls_protocol.py`: 10 позитивных и 33 негативных формы, monkeypatch DNS запрещает запросы |
| ТЗ §23.4: JSON Lines, версия, размер, поля, дубли JSON, идентификаторы | PASS | `test_protocol_exact`, `test_real_unix_socket`: реальный локальный Unix socket, лишние поля не запускают инструмент |
| ТЗ §23; политика §2,5: worker без SQLite, один путь медиа, отсутствие автозапуска | PASS | AST-обход импортов worker, тесты архитектуры; старые offline/namespace/egress реализации удалены |
| ТЗ §10,23.4: одна загрузка, параллельный inspect, идемпотентность | PASS | `test_admission_concurrency_and_cancel`: вторая загрузка отвергнута, inspect завершается при активной первой |
| ТЗ §15; политика §7: подтверждение отмены после остановки группы | PASS | Реальное дерево Python → sleep убивается; отмена до первого шага coroutine; при разрыве IPC намерение сохраняется, успех не объявляется |
| ТЗ §5–8: правдивые качества, портретный 720p, русская дорожка, fallback | PASS | Тесты планов/выбора; ровно две fallback-кнопки; повторного inspect по выбору нет |
| ТЗ §7–8: обработка MP4/H.264/AAC и MP3 | PASS | Синтетический файл генерируется настоящим FFmpeg; реальные ffprobe, FFmpeg remux/MP3 и полный decode; скачивание источника в этом тесте заменено локальной копией |
| ТЗ §10–11: 20 ожидающих, FIFO/диск, 30 минут, неизвестный размер строго больше порога | PASS | `test_queue_timeout_and_twenty_pending`, `test_queue_fifo_skip_disk_without_reset`, `test_unknown_disk_threshold` |
| ТЗ §11–12: UTC-квота, атомарное однократное списание, точный кэш вне очереди | PASS | `test_quota_atomic_once_and_cache`; ограничения при приёме/старте и transaction SQLite |
| ТЗ §6,17: повтор updates/callback, владельцы, антиспам после рестарта | PASS | `test_duplicate_invalid_and_valid_updates_are_durable`, `test_idempotency_ownership_and_capacity`, `test_rate_persists` |
| ТЗ §14: независимые попытки отправки, сохранение расписания, retry_after | PASS | `test_upload_retry_persists_and_respects_429`, тестовые ответы Telegram; реальная сеть отдельно ниже |
| ТЗ §16: восстановление готового файла до нового скачивания, порча результата | PASS | `test_not_found_checks_ready_before_redownload`, `test_corrupt_ready_never_sent_and_can_redownload`: размер/манифест/SHA-256 сверяются |
| ТЗ §16; политика §8: оба режима keep_downloaded_files | PASS | `test_delivery_retention_and_once_quota` параметризован true/false; промежуточные файлы удаляются независимо от режима |
| ТЗ §19: Backup API, проверка/ротация, восстановление, запрет пустой замены повреждённой БД | PASS | `test_backup_restore_and_corruption`, `test_unrecoverable_corrupt_db_never_implicitly_replaced` |
| Сохранение данных при смене внутренней архитектуры | PASS | `test_safe_legacy_data_migration`: исходные таблицы остаются, задачи/квоты/блокировки переносятся после backup; старые непроверенные локальные файлы не выдаются как проверенные |
| ТЗ §9,18: cloud/local endpoint и is_local без fallback | PASS | `test_telegram_endpoints` проверяет настройки aiogram; проверка реальной передачи отдельно ниже |
| Ошибочная конфигурация, обязательные инструменты/пакеты | PASS | `test_invalid_config`, `bot.admin doctor`; Python lock установлен, `uv pip check` без конфликтов |
| Fail-closed вне постоянного worker / без подтверждения защиты | PASS | `test_isolation_fail_closed`, прямой вызов guard отклонён как `worker_outside_permanent_unit` |
| Неизменность двух утверждённых документов | PASS | `git diff` от исходной ветки по обоим файлам пуст |
| Связанный сценарий ссылка → карточка → IPC → FFmpeg → доставка → кэш | PASS | `test_local_end_to_end.py`: настоящий Unix IPC и FFmpeg; сеть платформ и Telegram явно заменены тестовыми реализациями; одна загрузка и одно списание квоты |
| Полная локальная регрессия | PASS | `python -m pytest -q`: 82 тест на Python 3.13.5; это не старый набор «691» и не внешняя приёмка |
| Фактический запуск обоих постоянных systemd-юнитов | INCONCLUSIVE | В контейнере `/run/systemd` недоступен для записи; `systemd-analyze verify` завершился ошибкой среды. На VPS не выполнялось |
| Реальный запрет IPv4/IPv6 loopback в штатном worker + baseline | INCONCLUSIVE | Нет работающего systemd/cgroup-BPF VPS; предоставлены `bot.probe`, два unit и инструкция. Root-подтверждение не создано |
| Наследование фильтра дочерними yt-dlp/FFmpeg, DNS/HTTPS при фильтре | INCONCLUSIVE | Требует предыдущей проверки в постоянном worker; не заменено локальными mock-тестами |
| Реальные YouTube, Instagram, TikTok: inspect → download MP4/MP3 | INCONCLUSIVE | Защищённого постоянного worker здесь нет; незащищённые сетевые вызовы намеренно не выполнялись |
| Полный цикл Telegram с настоящим токеном: cloud | INCONCLUSIVE | Токен не предоставлен; фактических отправок не было |
| Полный цикл Telegram: local, локальные пути, видео/MP3, кэш и повторы | INCONCLUSIVE | Нет Local Bot API Server и токена; конструктор endpoint и тестовые ответы проверены отдельно |
| Сеть Telegram: реальная длительная потеря соединения, 429, восстановление | INCONCLUSIVE | Локально проверены расписания и обработка ошибок; реальная сеть не испытывалась |
| Рестарты постоянных юнитов на всех этапах реального цикла | INCONCLUSIVE | Локальное восстановление/IPC проверены; VPS/Telegram приёмка не выполнялась |
| Debian 12 с Fake-IP без изменения маршрутов/DNS | INCONCLUSIVE | Такой VPS не подключён |

GitHub Actions также завершился PASS для коммита `74a4408` (Python 3.13):
https://github.com/raspel-ai/BotDownload/actions/runs/42358809756

## Выполненные команды

- `work/venv313/bin/python -m pytest -q`
- `work/venv313/bin/python -m bot.admin doctor`
- `uv pip check --python work/venv313/bin/python`
- `python -m compileall -q bot scripts tests`
- `git diff --check`
- `systemd-analyze verify deploy/tgbot.service deploy/tgbot-worker.service` —
  ошибка окружения: `Failed to create directory '/run/systemd/': Read-only file system`.

## Оставшиеся ограничения

Внешние строки INCONCLUSIVE обязательны для готовности, а не факультативны.
Контейнерная локальная проверка не подтверждает устойчивость на VPS и доступность
платформ. Ограничение SSRF намеренно неполное, как согласовано политикой; один UID
не изолирует секреты от скомпрометированного worker. Подтверждение оператора требует
фактических baseline/worker/DNS/HTTPS-тестов и обновляется после boot/unit изменений.

При переходе с прежней схемы исходные таблицы кэша и медиа сохраняются как данные;
неоднозначные исторические параметры кэша не переименовываются в новые варианты.
Автоматическая выдача непроверенных исторических локальных файлов не выполняется.
Новые записи v1 `file_id` переживают следующие обновления кода без версии алгоритма
в ключе. Перед эксплуатацией переноса нужна проверка на копии реальных данных.

## Пересмотр процедуры самостоятельной приёмки

По решению пользователя первый внешний этап — Debian 13/cloud; local и Debian 12
Fake-IP отложены на последующие этапы, а не исключены из приёмки. На VPS команды
ещё не выполнялись, внешние результаты по-прежнему INCONCLUSIVE.

Исправлены обнаруженные ошибки процедуры: ECONNREFUSED/нет IPv6 больше не выдаются
за фильтрацию; проверяются родитель и ребёнок по IPv4/IPv6; окончательный unit не
изменяется после теста; root не исполняет файлы из tgbot-owned venv/рабочего дерева.
Подтверждение требует свежего отчёта того же boot/unit/InvocationID и повторного
HTTP-baseline с уникальным идентификатором под тем же UID вне worker. После этого
оператор отдельно вводит явное подтверждение. Это пока локально проверенная логика,
не доказательство работы cgroup-BPF на VPS.

`tests/test_operator_acceptance.py` покрывает ложные отказы соединений, точную
политику, stale/mismatched отчёты, child IPv6, drop-in hash, пропуск без запроса,
baseline под UID tgbot и read-only проверку прав без чтения/печати секретов.

После исправлений процедуры локальная регрессия: **100 тестов PASS** на Python 3.13.5;
compileall и git diff --check PASS. Ни один результат VPS не изменён на PASS.
