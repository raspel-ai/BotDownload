# BotDownload v1

Telegram-сервис для видео YouTube, Instagram и TikTok; интерфейс на русском.
Два постоянных процесса: `tgbot.service` и `tgbot-worker.service`, один UID `tgbot`.
Только бот работает с SQLite. Worker выполняет inspect, yt-dlp и FFmpeg.
Транспорт — приватный Unix socket, JSON Lines v1, максимум 64 КиБ.

Нормативные документы: [ТЗ](docs/TECHNICAL_SPEC_V1.md) и
[политика безопасности](docs/SECURITY_POLICY_V1.md). Политика имеет приоритет.
README описывает эксплуатацию, не добавляет новых продуктовых требований.

**Статус: ветка разработки, публичный запуск пока не разрешён.**
Локально проверяются IPC, очереди, состояние, отмена, реальная обработка локальных
MP4/MP3 и транспорт Telegram с тестовыми ответами. Приёмка постоянных юнитов,
реальных платформ, Telegram и Debian 12/Fake-IP требует VPS и токена; без её
фактического выполнения безопасная реальная загрузка не объявляется включённой.
Протокол результатов: [VALIDATION.md](docs/VALIDATION.md).
Продолжение работы: [IMPLEMENTATION_PROGRESS.md](docs/IMPLEMENTATION_PROGRESS.md).

## Устройство и поведение

`Telegram → validate_url → SQLite/карточка → диспетчер → socket → worker →
проверенный файл → выбранный Telegram API → транзакция SQLite → очистка`.

- Исходная ссылка проверяется локально, без DNS и раскрытия redirects. Только
  HTTPS, точные домены и пути из ТЗ. У YouTube `/watch` только `v=ID`; у остальных
  форм нет query. Даже `si`, `t`, `utm_*`, явный `:443` и пустой `#` отклоняются.
- Одна загрузка/обработка; параллельные inspect ограничены `worker.max_inspects`.
  По умолчанию до 20 ожидающих загрузок, FIFO с временным пропуском при нехватке
  диска; исходное время ожидания не сбрасывается. Отправка идёт независимо.
- Карточки независимы. Callback содержит только код качества; владелец,
  сообщение, доступный вариант и состояние сверяются с SQLite. Повторный полный
  inspect при нажатии не выполняется. Недостоверные качества не показываются:
  при неудачном анализе доступны только «Скачать видео» / «Скачать аудио».
  Если не подтверждена сама защита worker, задача прекращается с диагностикой.
- Выбранное разрешение не подменяется. Короткая сторона определяет 480p/720p/1080p,
  включая портретную ориентацию. Русская аудиодорожка предпочтительна, иначе
  оригинальная. Результат: MP4 H.264/AAC-LC или MP3 VBR `q:a=0`.
- Изменяемые настройки — `config.yaml`, секрет Telegram — `.env`. Worker не
  загружает `.env`; задания не содержат токенов, proxy, cookies или произвольных путей.
- По умолчанию API `local`. `cloud` использует только `https://api.telegram.org`.
  Автоматического переключения нет. Локальный Bot API Server устанавливается
  оператором отдельно и не входит в поставку. `aiogram` использует `is_local=True`;
  файлы отправляются multipart, абсолютные локальные пути не превращаются в URL.
- Квота — 3 подтверждённых новых выдачи за UTC-сутки; `trusted_users` не ограничены.
  Кэш по точному варианту не тратит квоту. `file_id` сохраняется транзакционно.
  Неудачи/отмены не списываются. Повторный результат Telegram при аварии возможен,
  двойное списание по одной задаче исключено ключом SQLite.
- `/start`, `/myid`, `/cancel_all`. Первые пять попыток ссылок за 600 секунд
  разрешены, шестая блокирует новые ссылки на 3600 секунд. Состояние сохраняется.
  Команды и обычный текст не расходуют этот лимит.

## Проверенные версии

Целевая система — Debian 13, Python 3.13 (в локальной проверке 3.13.5).
FFmpeg/ffprobe — системный пакет Debian; локально 7.1.5. Прямые зависимости:
`aiogram 3.22.0`, `yt-dlp 2026.10.26`, `yt-dlp-ejs 0.8.0`, `deno 2.9.8`,
`PyYAML 6.0.2`, `python-dotenv 1.1.1`. Полный набор Python-зависимостей фиксируется
в `requirements.lock`. Deno устанавливается в venv; скрипты с CDN во время задания
не скачиваются. Плагины yt-dlp, пользовательские конфиги и автообновления отключены.
Обновляет зависимости только администратор через проверяемое изменение lock-файла.

## Новая установка на Debian 13

Команды ниже выполняются на VPS после проверки PR. Не вводите настоящий токен
в чат, командную строку, Git или отчёт. При использовании другого пути вместо
`/opt/botdownload` измените **оба unit-файла** (WorkingDirectory и ExecStart);
менять Python-код не требуется.

1. В SSH-терминале под администратором установите системные зависимости.
   Ожидается успешная установка Python/venv и FFmpeg из репозиториев Debian.

   ```bash
   sudo apt update
   sudo apt install python3 python3-venv ffmpeg git
   ```

2. Там же создайте отдельного непривилегированного пользователя без входа и каталог.
   Если пользователь/каталог уже существует, сначала выясните назначение; не удаляйте его.

   ```bash
   sudo useradd --system --no-create-home --home-dir /opt/botdownload --shell /usr/sbin/nologin tgbot
   sudo install -d -o tgbot -g tgbot -m 700 /opt/botdownload
   ```

3. Получите код от имени `tgbot`. Ниже указана рабочая ветка; для выпуска следует
   выбрать проверенный коммит. Ожидается чистая рабочая копия без секретов.

   ```bash
   sudo -u tgbot git clone --branch dev/v1-completion https://github.com/raspel-ai/BotDownload.git /opt/botdownload
   ```

4. Перейдите в каталог проекта. Создайте venv и установите зафиксированные зависимости.
   Ожидаются отсутствие ошибок pip и сообщение об отсутствии конфликтов.

   ```bash
   cd /opt/botdownload
   sudo -u tgbot chmod 700 data
   sudo -u tgbot python3 -m venv .venv
   sudo -u tgbot .venv/bin/python -m pip install -r requirements.lock
   sudo -u tgbot .venv/bin/python -m pip check
   ```

5. Скопируйте пример секрета и заполните файл **локальным редактором на VPS**.
   Не публикуйте содержимое. `.env` должен читаться только `tgbot`.

   ```bash
   sudo -u tgbot cp .env.example .env
   sudo -u tgbot chmod 600 .env
   sudo -u tgbot nano .env
   ```

6. Локально отредактируйте `config.yaml`: `admin_telegram_id`, `trusted_users`, режим
   API и его адрес, лимиты, хранение. Не публикуйте реальные ID. `files_dir` — отдельный
   относительный приватный каталог, по умолчанию `files`. `worker.socket` одинаков
   у обоих сервисов, по умолчанию `/run/botdownload/worker.sock`; не `/tmp`.

   ```bash
   sudo -u tgbot nano config.yaml
   sudo -u tgbot .venv/bin/python -m bot.admin doctor
   ```

   Ожидается PASS конфигурации и зависимостей. Эта команда **не доказывает** сетевую
   изоляцию. Ошибки конфигурации называют параметр и причину, не секретное значение.

7. Установите два постоянных юнита от администратора. Затем обязательно задайте
   ограничения памяти, CPU и процессов соразмерно вашему VPS; пример не является
   требованием иметь определённый объём RAM или диска.

   ```bash
   sudo install -m 644 deploy/tgbot.service /etc/systemd/system/tgbot.service
   sudo install -m 644 deploy/tgbot-worker.service /etc/systemd/system/tgbot-worker.service
   sudo mkdir -p /etc/systemd/system/tgbot-worker.service.d
   sudo cp deploy/resources.conf.example /etc/systemd/system/tgbot-worker.service.d/resources.conf
   sudo nano /etc/systemd/system/tgbot-worker.service.d/resources.conf
   sudo systemctl daemon-reload
   sudo systemd-analyze verify /etc/systemd/system/tgbot.service /etc/systemd/system/tgbot-worker.service
   ```

8. **До реальных inspect/download выполните приёмку защиты**, описанную ниже.
   Отсутствие поддержки cgroup-BPF/IPAddressDeny или совместимого DNS означает
   остановку включения загрузки, а не снятие запрета. Не запускайте worker через
   `sudo`, `systemd-run`, namespace-обходы или как дочерний процесс бота.

9. После проверки включите автозапуск и оба сервиса. Ожидаются два активных процесса
   под UID `tgbot`; бот отправляет администратору уведомление, когда Telegram доступен.

   ```bash
   sudo systemctl enable tgbot-worker.service tgbot.service
   sudo systemctl start tgbot-worker.service tgbot.service
   sudo systemctl status tgbot-worker.service tgbot.service
   ```

   При недоступном выбранном Bot API бот повторяет соединение с экспоненциальной
   задержкой 1–300 секунд, не меняя режим. Проверьте также отвечающий `/myid`.

## Приёмка защиты на каждом VPS

Worker допускает реальные задания только внутри `/tgbot-worker.service` и при наличии
root-owned подтверждения `/etc/botdownload/worker-isolation.json`. Подтверждение
привязано к boot ID и содержимому unit/drop-in. Оно не заменяет фактических тестов;
после перезагрузки VPS или изменения юнита проверку и подтверждение повторяют.

Политика v1 запрещает **только** `127.0.0.0/8` и `::1/128` для worker/его детей.
Остальные private/link-local/metadata/Fake-IP адреса не блокируются. Не меняйте DNS,
маршруты, IP платформ, не используйте `PrivateNetwork=yes`. Бот сохраняет loopback
для local Bot API; это другой cgroup. Один UID не изолирует секреты бота от
скомпрометированного worker; полная защита SSRF не заявляется.

1. В отдельном SSH-терминале оператора на VPS запустите заведомо работающий локальный
   HTTP-сервис на IPv4 loopback. Он нужен только для теста, не является компонентом
   приложения. Из каталога без пользовательских файлов:

   ```bash
   python3 -m http.server 18765 --bind 127.0.0.1 --directory /var/empty
   ```

   Если `/var/empty` отсутствует, создайте отдельный пустой тестовый каталог или укажите
   существующий пустой каталог. Не используйте каталог проекта/секретов.
   Из второго терминала подтвердите HTTP 200 без фильтра и сохраните вывод:

   ```bash
   curl --noproxy '*' -I http://127.0.0.1:18765/
   ```

2. Аналогично подтвердите слушающий TCP-сервис на `::1:18765`, если IPv6 включён.
   Невозможность создать IPv6 baseline фиксируется как INCONCLUSIVE, не как PASS.
   Для второго тестового сервера используйте отдельный терминал:

   ```bash
   python3 -m http.server 18765 --bind ::1 --directory /var/empty
   curl --noproxy '*' -g -I 'http://[::1]:18765/'
   ```

3. Остановите оба юнита; временно добавьте диагностический ExecStartPre **в постоянный
   worker-юнит**, не в transient unit. Основной worker ещё не допущен к заданиям.

   ```bash
   sudo systemctl stop tgbot.service tgbot-worker.service
   sudo systemctl edit tgbot-worker.service
   ```

   В редакторе добавьте:

   ```ini
   [Service]
   ExecStartPre=/opt/botdownload/.venv/bin/python -m bot.probe
   ```

   Сохраните; затем запустите только worker и смотрите журнал:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl start tgbot-worker.service
   sudo journalctl -u tgbot-worker.service -n 80 --no-pager
   ```

   `bot.probe` проверяет принадлежность постоянному cgroup, отказ loopback IPv4/IPv6,
   отказ для дочернего процесса, DNS и HTTPS YouTube. Отказ соединения считается
   доказательством фильтра **только вместе с работающим baseline из шага 1/2**.
   Сохраните данные DNS/Fake-IP, журнал и effective-настройки:

   ```bash
   sudo systemctl show tgbot-worker.service -p User -p IPAddressDeny -p NoNewPrivileges -p ProtectSystem -p ProtectHome -p PrivateTmp -p KillMode -p ControlGroup
   ```

4. При PASS всех обязательных проверок оператор может убрать диагностический
   ExecStartPre из drop-in (не удаляя ресурсы и сетевые ограничения), выполнить
   daemon-reload и рестарт worker. Проверьте, что тот же постоянный юнит и IPAddressDeny
   сохранены. Только после этого запишите подтверждение:

   ```bash
   sudo .venv/bin/python scripts/confirm_worker_isolation.py
   ```

   Скрипт показывает effective-настройки, **не выполняет тесты вместо оператора** и
   требует строку `VERIFIED-PERMANENT-WORKER`. Enter отменяет операцию. Не выдавайте
   этому скрипту автоматическое подтверждение. При изменении unit/drop-in повторите
   фактические тесты; при несовместимом DNS остановитесь, не отключайте фильтр.
   Завершите два тестовых HTTP-сервера через Ctrl+C.

5. На VPS с действующей защитой проверьте реальный цикл для каждой платформы:
   ссылка → карточка → доступное качество → MP4 / MP3 → Telegram; отмену и рестарты
   на разных этапах; cloud и local отдельно. Отдельно обязательна проверка Debian 12
   с Fake-IP. Результаты запишите в протокол, не заменяйте их локальными mock-тестами.

## Диагностика и файлы

Из `/opt/botdownload` под `tgbot` доступны команды без токена в аргументах:

```bash
sudo -u tgbot .venv/bin/python -m bot.admin --help
sudo -u tgbot .venv/bin/python -m bot.admin status
sudo -u tgbot .venv/bin/python -m bot.admin queue
sudo -u tgbot .venv/bin/python -m bot.admin disk
sudo -u tgbot .venv/bin/python -m bot.admin cache
sudo -u tgbot .venv/bin/python -m bot.admin errors
sudo -u tgbot .venv/bin/python -m bot.admin db status
sudo -u tgbot .venv/bin/python -m bot.admin db backups
sudo -u tgbot .venv/bin/python -m bot.admin db check
sudo -u tgbot .venv/bin/python -m bot.admin db backup
```

Справка каждой подкоманды: `python -m bot.admin <подкоманда> --help`.
Для просмотра логов из каталога проекта: `tail -F logs/bot.log`; Ctrl+C завершает
только просмотр. Ротация по `logging`; логи не содержат токенов и полных URL.

SQLite — `data/bot.sqlite`, содержит текущие карточки, состояния задач/отправки,
идемпотентность updates, попытки, UTC-квоты, блокировки и кэш `file_id`.
Рабочие данные не являются отдельной пользовательской историей скачиваний.
Резервные копии находятся в `backups/`, создаются SQLite Backup API каждые 24 часа
и перед миграцией/обновлением, проверяются; остаётся до `backup.max_copies`.
Повреждённые базы `data/database_corrupt_*.sqlite` сохраняются отдельно и не
удаляются автоматически. Секреты, БД, копии, логи и медиа исключены из Git.

Временные каталоги — `data/tmp/<execution_id>/`, готовые результаты — `files/`.
Каталоги `files` и `data/tmp` должны находиться на одной файловой системе;
worker проверяет это при старте. Манифест рядом с результатом содержит размер, длительность, формат, SHA-256 и
подтверждение проверки worker. Бот сверяет его и файл перед отправкой; FFmpeg в
боте не запускается. При `keep_downloaded_files: true` успешный результат остаётся
в приватном `files/`; при `false` удаляется. Незавершённые/повреждённые файлы не
сохраняются этой настройкой; готовый результат ожидающей повторной отправки сохраняется.
После окончательной ошибки отправки результат удаляется в обоих режимах.

Для инвентаризации/очистки сиротских временных каталогов **сначала остановите оба
сервиса**. CLI удерживает блокировки обоих компонентов, чтобы исключить гонку.
Успешно сохранённые медиа не входят в автоматическую или сиротскую очистку.

```bash
sudo systemctl stop tgbot.service tgbot-worker.service
sudo -u tgbot .venv/bin/python -m bot.admin tempfiles list
sudo -u tgbot .venv/bin/python -m bot.admin tempfiles clean --dry-run
sudo -u tgbot .venv/bin/python -m bot.admin tempfiles clean
```

Последняя команда покажет список и запросит `yes`; Enter/no отменяет удаление.
Администратор отдельно принимает решение об удалении сохранённых успешных результатов,
старых операционных записей и архивов повреждённой БД; автоматически по возрасту они
не удаляются. Перед удалением операционных данных сохраните проверенную резервную
копию и остановите сервисы; полное обнуление доступно только через подтверждённый `db init`.

Ручная диагностика бота из корня: `.venv/bin/python -m bot` под `tgbot`, когда
штатный бот остановлен. Она не запускает worker. `.venv/bin/python -m bot.worker`
вне постоянного защищённого юнита может обслуживать диагностику IPC, но реальные
inspect/download будут отклонены. Никогда не снимайте защиту ради ручного теста.

## Безопасное обновление

1. В SSH-терминале перейдите в `/opt/botdownload`. Проверьте ветку, чистоту копии
   и доступ к GitHub. Если есть локальные изменения исходников/config, остановитесь
   и сохраните их отдельно; не используйте `reset --hard` или `git clean`.

   ```bash
   cd /opt/botdownload
   sudo -u tgbot git status --short --branch
   sudo -u tgbot git ls-remote origin
   ```

2. Остановите оба сервиса и проверьте завершение процессов. Worker останавливает
   группы yt-dlp/FFmpeg, systemd завершает остаток cgroup; данные SQLite сохраняются.

   ```bash
   sudo systemctl stop tgbot.service tgbot-worker.service
   sudo systemctl status tgbot.service tgbot-worker.service
   ```

3. Создайте проверенную копию SQLite штатной командой (не `cp` открытой базы).
   Для старой версии без такой команды сначала сделайте SQLite Backup API из
   остановленного проекта под `tgbot` в отдельный защищённый каталог; проверьте
   `PRAGMA integrity_check`. Не изменяйте оригинал и не создавайте фиктивную копию.

   ```bash
   sudo -u tgbot .venv/bin/python -m bot.admin db backup
   sudo -u tgbot .venv/bin/python -m bot.admin db check
   ```

4. Запишите текущий коммит **вне заменяемого дерева кода**, например в ваш журнал
   обслуживания. Он нужен для отката. Сохраните локально `.env` и `config.yaml` с
   приватными правами; не добавляйте их в Git.

   ```bash
   sudo -u tgbot git rev-parse HEAD
   ```

5. Получите проверенное обновление. `fetch` сам не заменяет рабочие файлы.
   Затем подставьте выбранный проверенный SHA вместо `ПРОВЕРЕННЫЙ_SHA`.
   `.env`, БД, backups, files и logs не затрагиваются. Если Git сообщает конфликт
   локального config.yaml, остановитесь и вручную совместите настройки по образцу
   новой версии. Не перезаписывайте свои ID/endpoint/секреты образцом.

   ```bash
   sudo -u tgbot git fetch origin
   sudo -u tgbot git switch --detach ПРОВЕРЕННЫЙ_SHA
   ```

6. Обновите зависимости и проверьте конфигурацию. При переходе с прежней ветки
   удалите `storage.download_dir` из локального YAML и задайте `files_dir`,
   `keep_downloaded_files` и раздел `worker` по текущему образцу. Старые алиасы не
   поддерживаются. Старые таблицы/файлы сохраняются; квоты, блокировки и задачи
   переносятся после проверенной копии. Ранее не проверенные файлы не отправляются
   без нового защищённого приготовления. Не удаляйте прежний каталог медиа вручную
   до инвентаризации и завершения переноса.

   ```bash
   sudo -u tgbot .venv/bin/python -m pip install -r requirements.lock
   sudo -u tgbot .venv/bin/python -m pip check
   sudo -u tgbot .venv/bin/python -m bot.admin doctor
   ```

7. Установите обновлённые юниты, сохраните свои drop-in ресурсов. Выполните daemon-reload,
   проверку защиты постоянного worker и новое подтверждение, если unit изменился.
   Затем запустите worker и бот в этом порядке.

   ```bash
   sudo systemctl start tgbot-worker.service tgbot.service
   sudo systemctl status tgbot-worker.service tgbot.service
   ```

8. Проверьте `/myid`, состояние очереди, восстановленные готовые файлы и логи.
   Администратору приходит уведомление о старте. Обычные ошибки отдельных ссылок
   не вызывают отдельных административных сообщений.

## Восстановление базы и откат

При повреждении обработка прекращается, повреждённые файлы сохраняются, последняя
проверенная совместимая копия восстанавливается с повторной проверкой. При отсутствии
исправной копии пустая база автоматически не создаётся, даже при следующем старте.

Для ручного восстановления остановите **оба** сервиса; сохраните исходные повреждённые
файлы и все копии отдельно. Работайте с копиями, не экспериментируйте с оригиналом.
Из каталога проекта под `tgbot` сначала просмотрите проверки, затем выберите файл
внутри `backups`:

```bash
sudo -u tgbot .venv/bin/python -m bot.admin db restore --list
sudo -u tgbot .venv/bin/python -m bot.admin db restore --backup ИМЯ_КОПИИ.sqlite
```

CLI откажет при работающем боте; покажет путь/дату/проверку, предупредит о потере
изменений после копии и запросит `yes`. Enter/no отменяет операцию. Текущая база
сохраняется отдельно; восстановленная проверяется повторно.
Если исправных копий нет, `db init` доступен только после осознанного подтверждения:

```bash
sudo -u tgbot .venv/bin/python -m bot.admin db init
```

Он предупреждает, что квоты, блокировки, очередь и кэш не восстановятся, сохраняет
имеющиеся файлы отдельно и требует `yes`. Секреты и медиа эта команда не удаляет.

Если обновление не работает, остановите оба сервиса, верните сохранённый SHA кода
через `git switch --detach`, восстановите соответствующие зависимости и проверьте
совместимость схемы. Не откатывайте SQLite автоматически. При необходимости отдельно
выберите проверенную копию с предупреждением о потере новых изменений и явным
подтверждением. Сохраните конфигурацию установленного сервиса и повторно проверьте
защиту worker до реальных загрузок.

## Тесты разработки

Из корня проекта с установленными зависимостями:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Тесты используют временные каталоги, локальные синтетические медиа и тестовый Telegram.
Они не используют настоящий токен и не выполняют незащищённую загрузку с платформ.
Публичная приёмка требует всех внешних сценариев из протокола VALIDATION.md.
