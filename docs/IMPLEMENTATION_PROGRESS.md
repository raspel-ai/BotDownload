# BotDownload v1 — implementation progress (non-normative)

Updated: 2026-11-06. Branch: `feat/v1-secure-worker-ipc-20261106`. Base: `restore/verified-691-tests`.

## Verified repository state
- Branch HEAD before this note: `6b2206df3b99583680cc2066cf0c52112f18c5da`.
- GitHub API returned **no open pull requests** on 2026-11-06; the previously reported PR #3 is **not open**. Check its state before reopening or creating a replacement.
- Existing commits include strict original URL validation, metadata-failure UI fallback, two systemd unit drafts and a malformed-query fix.
- Normative sources re-read: `AGENTS.md`, `docs/TECHNICAL_SPEC_V1.md`, `docs/SECURITY_POLICY_V1.md`. Neither normative document was changed.

## Requirements checklist / current evidence
- Original URL admission (§4, policy §1): implementation present; no executed tests in this session; INCONCLUSIVE.
- Independent services and worker egress isolation (§23, policy §6): unit drafts exist; actual runtime and VPS isolation untested; INCONCLUSIVE.
- IPC JSON Lines inspect/download/cancel/status (§23.4): **NOT DONE**. Existing worker_ipc uses obsolete operations, 4096-byte messages and SQLite; FAIL.
- SQLite owned only by bot (§23.4): **NOT DONE**. Existing worker_ipc and worker_media still open database; FAIL.
- Bot must not spawn worker (§23): **NOT DONE**. bot/main.py still uses OfflineWorkerSupervisor; FAIL.
- Protected real inspect (§4, §23): **NOT DONE**. bot/metadata.py still has legacy network-guard execution; FAIL.
- Download, conversion, queues, cancellation, recovery, delivery (§5–§9, §23–§24): NOT DONE / not verified.
- VPS, Telegram, Debian 13 acceptance (§21 and policy §10): INCONCLUSIVE; owner will run on test server when implementation is ready.

## Important concrete defects / dependencies
1. Worker service currently defaults to /tmp and accepts BOTDOWNLOAD_WORKER_DB, contrary to v1.
2. Both unit drafts use RuntimeDirectory=botdownload; systemd ownership/lifecycle must be checked so a restart of one unit cannot remove the shared socket directory.
3. Unit drafts do not yet grant all runtime paths and logging paths needed by the existing app; do not deploy as-is.
4. No end-to-end tests were executed here; GitHub API is available, but container git clone previously failed DNS.

## Next concrete work
Implement one integrated secure runtime slice: replace worker_ipc with bounded versioned JSONL protocol and validated payloads; remove all SQLite access from worker; wire real yt-dlp inspect exclusively in worker with process limits; add bot IPC client and replace bot metadata direct execution; remove bot worker auto-spawn; repair shared runtime-dir lifecycle and unit writable paths. Add integration tests and execute them before enabling downloads. Then implement download/FFmpeg, cancellation, queue, Telegram delivery, recovery, installation and rollback.

Do not mark service ready or ask for VPS acceptance until integrated scenario is demonstrably functional.

## 2026-11-06 subsequent implementation
- `a5b31752`: bot IPC client replaced with versioned JSONL request API.
- `42798c81`: worker entrypoint no longer passes SQLite path; default socket moved to /run/botdownload.
- `8b53cb3d`: replaced legacy SQLite-backed IPC server with UID-authenticated inspect/status/cancel/download action parsing and bounded messages; real inspect executes yt-dlp in worker. Download and safe cancel remain unimplemented; no success is faked.
- `90bb82f5`: bot metadata.inspect delegates to protected worker IPC.
- `2fdc07b1`: bot main no longer starts OfflineWorkerSupervisor.
- **NOT TESTED**: integration, systemd, yt-dlp, Telegram, VPS. Worker metadata module still contains legacy imports; older worker modules remain and must be retired. New IPC status client callers may need migration. Worker inspect currently uses asyncio.to_thread and cannot forcibly terminate a stuck extractor; must replace with cancellable process isolation before deployment.
- Next: audit runtime imports and test failures, replace thread inspect with supervised subprocess/process group; implement download and FFmpeg with safe cancellation, then queues/delivery/recovery; repair unit lifecycle and deployment paths.

## 2026-11-06 latest continuation
- Protected inspect subprocess fixes committed: 9e07634f, a2c037bf, 91c3f839.
- Shared socket runtime ownership fixed: bot unit no longer declares RuntimeDirectory; worker unit owns it and preserves on restart (d648c664, 1b951ff9).
- IPC request ID matching corrected (68f78cf4); protocol rejection tests added (466649a6).
- Tests NOT RUN: container cannot resolve github.com for git clone. New test suite and real worker process still need execution.
- Remaining: secure download and ffmpeg, confirmed cancellation, DB queue/worker orchestration, recovery, delivery, installation, VPS systemd isolation verification. No release claim.
