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
