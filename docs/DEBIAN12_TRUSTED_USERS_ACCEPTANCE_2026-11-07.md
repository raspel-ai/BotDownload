# Debian 12 post-update acceptance — 2026-11-07

## Scope and provenance

Owner-reported manual verification on the Debian 12 VPS after deploying commit `7b5cd86c121e07b3be53c84d89e2d65860219cd9` from `dev/v1-completion`. This report records observed behavior, not a claim that the assistant independently accessed the VPS.

## Deployment and configuration

- Before update, the VPS was at `db6fc72` with a locally modified `config.yaml`; both services were active.
- The owner created a root-private backup of the local config and verified `BACKUP_OK`.
- Both services were stopped, the branch was fetched and advanced to `7b5cd86`; `git status --short` showed only `M config.yaml` afterward.
- The owner added their own numeric admin Telegram ID to local `trusted_users`, without disclosing or committing the ID.
- Local YAML checks returned `CONFIG_OK` and `ADMIN_TRUSTED_OK`.
- The owner reported both `tgbot-worker.service` and `tgbot.service` active, with `NRestarts=0` for each. Autostart was deliberately not enabled.

## Manual acceptance observations (owner-reported)

| Scenario | Observation |
| --- | --- |
| Trusted-user link acceptance after previous link-rate ban | A new link was accepted, downloaded and delivered to Telegram after the trusted-user update; PASS for this user's path |
| Video delivery | Downloaded video was delivered to Telegram; PASS |
| Additional video | Another video downloaded successfully; PASS |
| Audio | Audio was downloaded and played successfully; PASS |
| Cached video | Bot offered a cached video when available; PASS |
| Language track | On one English-language YouTube video offering Russian auto-translation, the delivered video had Russian-language audio; observed once, not a general guarantee |

## Limits and outstanding checks

- Codex reported 120 passing tests on Python 3.13.5 for the trusted-user change. These results were not independently rerun here.
- Neither system Python nor the existing `.venv` on Debian 12 had `pytest` installed; the full suite was **not** run on VPS/Python 3.11. No global packages were installed.
- Ordinary-user rate limiting after this deployment was not manually retested.
- Crash recovery, long-running stability, and other exceptional scenarios remain outside this acceptance session.
- Playback of downloaded *video* was not explicitly confirmed by the owner; audio playback was confirmed.
- Existing rate-limit database records were not manually deleted; the trusted-user exception is intended to ignore prior bans while the ID remains trusted.
- Local `config.yaml`, `.env`, private IDs and tokens must not be committed. No changes to worker isolation, systemd units, or normative security/specification documents are part of this record.

## Outcome

The owner confirmed that the bot works for the tested video, audio, cached-video and trusted-user scenarios after the update. This is a successful **functional smoke acceptance**, not full regression or security re-attestation.

## Follow-up acceptance — 2026-11-08 (owner-reported)

The owner performed additional Telegram and VPS checks after approximately 20 hours of operation:

| Scenario | Observation |
| --- | --- |
| Ordinary-user link limit | Tested from a different Telegram account; the link-attempt limit still works; PASS |
| Unavailable/deleted or child-directed YouTube videos | Bot did not download these tested videos and replied: «Ошибка: Скачать выбранный вариант не удалось. Вы можете отправить ссылку заново и выбрать другой вариант.»; graceful error reporting observed |
| Cancel active download | After link analysis and selection, the bot displayed «Скачивание, попытка 1…». Owner waited about a minute and sent `/cancel_all`; reply was «Отменено: 6. Ожидают подтверждения остановки: 0.»; canceled video did not arrive later; PASS for observed cancellation |
| Cached video across a restart | Owner confirmed the bot still offers a previously downloaded video from cache after restart; PASS |
| Continued operation | Owner sent more links the following day; bot downloaded and delivered videos successfully; PASS |

### Service state observed 2026-11-08

The owner ran `systemctl is-active` and `systemctl show` for both services. Results:

| Unit | ActiveState | SubState | ActiveEnterTimestamp | NRestarts |
| --- | --- | --- | --- | --- |
| `tgbot.service` | active | running | Sat 2026-11-07 17:10:29 MSK | 0 |
| `tgbot-worker.service` | active | running | Sat 2026-11-07 17:10:29 MSK | 0 |

Both units were active with no systemd-recorded automatic restarts. This is evidence of stable service state over the observation window, not a complete audit of uninterrupted operation; journals were not reviewed.

### Updated scope and remaining work

The earlier outstanding ordinary-user limit, active cancellation, cache-after-restart and approximately 20-hour service checks have now been covered by owner-reported manual testing. Full Python 3.11 regression, crash recovery, exceptional recovery paths, and renewed security/isolation attestation remain unverified in this session. No code or VPS configuration changes were made while recording this follow-up; autostart remains intentionally disabled.
