# Debian 12 VPS — post-update operational acceptance (2026-11-07)

Status: **PARTIAL ACCEPTANCE**. Operator-observed interactive checks; not a complete security re-attestation or end-to-end acceptance.

## Deployment context
- Repository branch: `dev/v1-completion`, deployed base commit `db6fc72fb1eba1162f2336cafe5689da52ecbfb3`.
- Debian 12 VPS; project `/opt/botdownload`; services `tgbot.service` and `tgbot-worker.service` run as unprivileged `tgbot`.
- Telegram API mode: `cloud`. Operator-specific `config.yaml` is intentionally modified locally and **must not be committed**. `.env` contains secrets and **must not be committed**.
- Systemd autostart intentionally **disabled**; both services were manually started for this acceptance.

## Verified after update (operator-reported)
1. `bot.admin doctor`: ffmpeg, ffprobe, deno, aiogram 3.22.0, yt-dlp 2026.10.26, PyYAML 6.0.2, python-dotenv 1.1.1, and configuration passed. Worker isolation checked separately.
2. `scripts/check_installation_permissions.py`: required checks passed (root-owned source/venv read-only to service account, runtime dirs, logs, `.env`, `config.yaml`). Optional `backups` directory was initially absent; created as `tgbot:tgbot` mode `0700`.
3. Root-owned installed `isolation.py` and `confirm_worker_isolation.py` each had the same SHA-256 as their repository counterparts.
4. Existing `/etc/botdownload/worker-isolation.json` was root-owned and recorded `children_confined=true`, `dns_https_ok=true`, `loopback_denied=true`, and `unit=tgbot-worker.service`. Recorded boot ID matched the running kernel's boot ID.
5. The recorded `unit_sha256` **matched** the current worker configuration when computed using the canonical `unit_digest()` algorithm in installed `isolation.py` (includes unit paths and applicable drop-ins). A preliminary SHA-256 comparison against the single unit file alone yielded `MISMATCH`, but that comparison used the wrong algorithm and is **not** evidence of changed isolation.
6. Calling the root confirmation helper without a prepared probe request returned `No such file or directory: '/run/botdownload-probe.request'`. **No fresh probe/re-attestation was performed**; the existing attestation was inspected and matched against current boot and unit digest. The confirmation record was not replaced.
7. Manual `systemctl start tgbot-worker.service` and `systemctl start tgbot.service` succeeded. Both reported `ActiveState=active`, `SubState=running`, `User=tgbot`, `NRestarts=0`.
8. Telegram startup/restart notification arrived. `/start` responded with supported HTTPS video sources and `/myid`, `/cancel_all` commands.
9. A YouTube HTTPS link produced a preview card and quality options.
10. After several links, the bot displayed: “Превышен лимит ссылок. Новые ссылки временно заблокированы. /cancel_all и /myid доступны.” **PASS: rate-limit activation observed**.
11. `/myid` responded while link submissions were blocked; the actual ID was not recorded.
12. `/cancel_all` returned “Отменено: 4. Ожидают подтверждения остановки: 0.” **PASS: cancellation response observed**, not independent proof of all worker process termination.
13. Subsequent `systemctl show` confirmed both services remained `active/running` with `NRestarts=0`.

## Open / not yet verified
- Automatic expiration/removal of the link-submission block.
- Successful post-update video download, delivery and playback in Telegram; test was interrupted by rate limiting.
- Independent verification that cancelled worker processes fully terminated.
- Fresh worker isolation network probe following this update (not performed; existing confirmation matched current boot and canonical unit digest).
- Full acceptance of all edge cases and any enablement of systemd autostart.

## Safety and scope
This report records observed behavior, not a change to `docs/TECHNICAL_SPEC_V1.md`, `docs/SECURITY_POLICY_V1.md`, or the worker isolation implementation. No token, administrator ID, private configuration content, or video URL is included. Continue stepwise testing before upgrading status from partial acceptance.
