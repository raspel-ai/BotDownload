# Block 2 / Stage 2 — remaining acceptance gates

**Normative v1 policy:** [SECURITY_POLICY_V1.md](SECURITY_POLICY_V1.md)
(approved 2026-11-03) overrides earlier network/isolation scope. In v1
block only IPv4 `127/8` and IPv6 `::1/128` for yt-dlp and children;
other private and Fake-IP destinations are intentionally not blocked.

This branch contains experimental namespace and egress modules. **They are not
production enabled and Stage 2 is not complete.** In particular, do not
connect `run_media_dispatcher` to the production SQLite queue until acceptance.
The current experimental dispatcher attempts real execution and stages a
validated result; failures still mark tasks failed.

## What can be tested offline

```bash
.venv/bin/python -m pytest -q tests/test_media_namespace_bridge.py tests/test_media_namespace_runner.py tests/test_media_egress_gateway.py tests/test_media_egress_socket.py
```

## Required before enabling media IPC

1. Enforce a **filesystem and Unix-socket boundary** in addition to network
   namespaces. An unprivileged network namespace does not hide the host
   filesystem or host Unix sockets. A worker must not reach arbitrary host
   services via their Unix sockets.
2. Use the approved **single unprivileged service UID** in v1. A distinct
   worker/gateway Unix account is deferred to v2. Verify gateway Unix-socket
   ownership, limited scope and that the worker cannot bypass the mandatory
   v1 loopback restriction. One UID does not isolate bot secrets.
3. Verify that all yt-dlp extractor, media, and redirect requests are proxied.
   Reject requests to unexpected domains; add a safe, explicit domain policy
   for required YouTube dependencies. Verify actual IPv4 `127/8` and IPv6
   `::1` rejection under DNS, redirects and yt-dlp internal requests, with
   ordinary/Fake-IP system DNS still functional. Other internal addresses
   are not blocked under v1.
4. Apply cumulative bytes, process-tree, CPU, memory and disk quotas, including
   child processes, partial fragments and intermediate files. Current
   `--max-filesize` is not a cumulative quota.
5. Experimental worker IPC `execute_media` and lease verification exist.
   Validate cancellation, deadline expiry/renewal, bounded results and
   fail-closed behavior under worker interruption. Keep SQLite writes in the
   trusted broker.
6. Stage the resulting regular file atomically in a bot-owned private
   directory and publish via a compare-and-swap status transition. Ensure
   cancellation and crashes cannot publish stale files.
7. Perform a real YouTube download on Debian 12 under `tgbot` through the
   configured gateway; record sanitized logs; verify blocked loopback and
   no host-network fallback. The explicit smoke command is
   `python -m bot.media_integration_smoke --url URL --download-dir PATH`.
   A green offline test suite does not prove live network isolation.
8. Test restart, kill -9, timeout, cancellation, disk full, malformed output,
   and gateway outage before production enablement.

The code must not claim successful real-network validation until the Debian
host has actually run that test.

## Debian 12 test evidence — 2026-11-04 (operator reported)

The operator ran the following on the Debian 12 test host. These results are
recorded as **operator-reported evidence**, not independently reproduced by
GitHub CI or this repository review.

- Live opt-in media smoke, as unprivileged `tgbot`, using
  `https://www.youtube.com/watch?v=jNQXAC9IVRw`: **PASS**;
  task status `upload_pending`; output `jNQXAC9IVRw.webm`, **474494 bytes**.
  This confirms a positive media path on the host with its existing DNS/Fake-IP
  configuration; it is not a full security acceptance test.
- `python -m pytest -q tests/test_media_egress_gateway.py`:
  **32 passed in 0.08s**.
- Combined offline regression suite covering egress gateway/socket,
  namespace bridge/runner, media IPC/queue, cancellation, crash recovery,
  startup recovery and stage-4 integration: **58 passed in 1.20s**.
- A separate Unix-socket CONNECT probe run as `tgbot` against
  `bot.media_egress_gateway.handle_connect`: **3/3 denied** with HTTP 403
  for `127.0.0.1`, `127.255.255.254`, and `[::1]`.
  Gateway warnings `EgressDenied: Destination not permitted` are expected.

**Evidence limits:** the CONNECT probe is a gateway request-validation test:
these literal IP destinations are also rejected by the gateway's domain
allowlist, so this alone does **not** establish that DNS-resolved loopback IPs
are blocked or that yt-dlp/its children cannot bypass the gateway. Offline
cancellation/recovery tests do not establish live process-tree termination.

**Additional operator-reported DNS-filter test (2026-11-04): PASS 3/3.**
With `asyncio` event-loop `getaddrinfo` patched to return only a loopback
answer for allowed host `www.youtube.com`, `resolve_public()` raised
`EgressDenied` for `127.0.0.1`, `127.255.255.254` and `::1`.
This confirms the gateway's DNS-answer filtering logic, **not** live
network-namespace enforcement or bypass resistance.

**Additional operator-reported namespace route test (2026-11-04): PASS 2/2.**
As `tgbot`, `unshare --user --map-root-user --net --fork` started a fresh
network namespace. Direct TCP attempts to `1.1.1.1:443` and
`198.18.0.44:443` both failed with `OSError: [Errno 101] Network is
unreachable`. This demonstrates no direct IPv4 route in that freshly
created namespace, **not** a complete no-bypass proof for the actual
media child-process tree or host Unix sockets. Fake-IP remains permitted
through the v1 gateway.

**Additional operator-reported child-process IPv4/IPv6 route test (2026-11-04): PASS 2/2.**
An unprivileged `tgbot` network namespace launched a child Python process.
Direct TCP to `1.1.1.1:443` failed with `Errno 101 Network is unreachable`;
direct TCP to `[2606:4700:4700::1111]:443` failed with
`Errno 99 Cannot assign requested address`. Both attempts were denied.
This demonstrates no direct IPv4/IPv6 access for that test child, but does
not prove actual yt-dlp/FFmpeg process-tree isolation or host Unix-socket
confinement.

**Additional operator-reported OS process-group timeout probe (2026-11-04): PASS.**
As `tgbot`, a Python subprocess started in a new session was timed out after
0.5 seconds, its process group was sent `SIGKILL`, and the parent awaited
termination with exit code `-SIGKILL`. This tests the OS-level mechanism,
not cancellation or cleanup of an actual media worker/process tree.

**Additional operator-reported parent/child process-group kill probe (2026-11-04): PASS.**
A test parent launched a sleeping child in the same process group under
`tgbot`; `SIGKILL` sent to the process group terminated the parent and the
child (child absent or zombie in `/proc`). This is OS process-group evidence,
not proof of the actual media worker cancellation/cleanup path.

**Additional operator-reported cancellation/recovery regression suite (2026-11-04): PASS 21/21 in 1.20s.**
Executed `tests/test_task_cancellation.py`, `test_cancel_worker_lifecycle.py`,
`test_offline_active_cancellation.py`, `test_worker_crash_recovery.py`,
`test_worker_recovery_extended.py`, `test_ready_recovery.py`,
`test_media_ipc_integration.py`, and `test_media_queue_dispatcher.py`.
Some tests overlap the previously reported 58-test suite; counts are not
additive. This is offline regression evidence, not a live media cancellation
or cleanup test.

**Still open before Stage 2 acceptance:** prove DNS-resolved IPv4/IPv6 loopback denial on the **live media path**
(beyond the mocked resolver test); prove no direct-network/Unix-socket bypass
from the namespace (including child processes); exercise live timeout,
cancellation, kill -9, gateway outage, cleanup and cumulative resource limits.
The full Telegram metadata → choice → download → delivery path remains
unverified and production media dispatch remains disabled.

### Active media cancellation implementation block — completed (operator verification 2026-11-04)

**Status: IMPLEMENTED / REGRESSION PASS (16/16).** On `feature/stage-2-telegram`, the worker IPC `cancel_reservation` operation signals an active media job; `MediaJobController` matches task ID and token and passes an `asyncio.Event` to the isolated runner, whose process-group termination is covered by a real parent/child subprocess test. Three new tests in `tests/test_media_active_cancellation.py` cover ownership, subprocess-group termination, and Unix IPC cancellation delivery. An initial test failed because the test request used a literal `\\n` rather than a newline; this test defect was corrected in commit `a89c5c3f5664bacaa7ba0ddcf652a043ded2721f`. Operator reports the subsequent six-module suite passed **16/16** on Debian 12 (2026-11-04). This closes the **implementation-and-regression block**, not the overall Stage 2 security acceptance. Outstanding: live cancellation during real yt-dlp/FFmpeg work, cleanup/no stale publication, worker crash/restart, gateway outage and resource-limit acceptance. Production media dispatch stays disabled.

### Next implementation block: durable cancellation during active media execution (2026-11-04)

**Implemented; awaiting Debian test execution, NOT accepted yet.** The worker now watches the authoritative SQLite running lease while executing media. Revocation (including user cancellation committed by a different broker process) sets the isolated runner cancellation event even when the execute IPC connection is busy. The existing runner then kills the isolated process group; the worker cleans its partial task workspace and returns a rejected response. New tests use a real temporary SQLite lease, a simulated in-progress isolated download writing a partial file, and assert cancellation, cleanup, and no ready-file publication. A separate fail-closed test covers a missing lease. These tests do not establish cancellation of a live YouTube/FFmpeg download; that remains an acceptance gate. Production dispatch remains disabled.

**Operator verification update (2026-11-04): PASS — 20 tests in 1.02s.** Ran as `tgbot`: `test_media_active_cancellation.py`, `test_media_namespace_runner.py`, `test_media_ipc_integration.py`, `test_media_queue_dispatcher.py`, `test_cancel_worker_lifecycle.py`, `test_offline_active_cancellation.py`, `test_worker_crash_recovery.py`. One non-fatal `PytestCacheWarning` occurred because `tgbot` could not write `/opt/BotDownload/.pytest_cache/v/cache/nodeids` (permission denied). Implementation/regression block **closed**; real network download cancellation and yt-dlp/FFmpeg child-tree verification remain open acceptance gates. To avoid the warning on subsequent tests, use `-p no:cacheprovider` or an independently writable pytest cache directory; do not change repository directory permissions merely for pytest cache.

### Full offline regression and live-worker cancellation probe (2026-11-04)

Operator reported **670 passed in 22.20s** for `sudo -u tgbot .venv/bin/python -m pytest -p no:cacheprovider -q tests/`. This is a full offline suite result, not a live network acceptance result.

Added `bot/media_live_cancel_smoke.py`: an **explicit opt-in**, unprivileged acceptance probe that creates a separate temporary SQLite DB and media root, starts the actual `bot.worker_service` via `OfflineWorkerSupervisor`, dispatches a real yt-dlp URL through the namespace/gateway, waits until a partial media file is observed, revokes the task via `cancel_task`, and verifies that the final task is `cancelled`, no ready/Telegram file ID was recorded, and the temporary media directory was cleaned. Exit 0 = PASS; exit 1 = FAIL; exit 2 = INCONCLUSIVE (download finished too quickly or no partial file was seen). This does not connect to production Telegram or production media storage. Added offline admission tests in `tests/test_media_live_cancel_smoke.py`. **Probe not yet run by operator; live cancellation gate remains OPEN.**

**Live worker cancellation acceptance (operator, 2026-11-04): PASS.** Command: `sudo -u tgbot .venv/bin/python -m bot.media_live_cancel_smoke --url 'https://www.youtube.com/watch?v=jNQXAC9IVRw' --wait-seconds 45`. Observed: `Task: cancelled ready_file: None telegram_file_id: None remaining: []` and `PASS: live worker cancellation, no staged file, workspace cleaned`. The worker/dispatcher logged `WorkerProtocolError: Загрузка не завершилась` on the expected rejected execution response; this did not cause test failure. Before live execution, operator reported **10/10** tests passing for `test_media_live_cancel_smoke.py` and `test_media_active_cancellation.py`. **Live cancellation + no publication + workspace cleanup gate accepted** for this scenario. Other Stage 2 gates (resource limits, crash/restart, complete Telegram delivery, broader environment) remain separately gated; production dispatch is not enabled.

### Live worker SIGKILL/restart gate (2026-11-04): probe implemented, NOT ACCEPTED

Added opt-in `bot.media_worker_crash_smoke` using temporary SQLite and media root. It starts a real worker, waits for a partial media file and child process, sends SIGKILL to the worker PID, snapshots surviving descendants, revokes the lease, restarts a fresh worker and checks for no ready/Telegram publication and no lease resurrection. Any surviving subprocess is reported as FAIL; test cleanup attempts to kill observed subprocess groups afterward. Added offline guard tests `tests/test_media_worker_crash_smoke.py`. The live probe is unexecuted and the crash/restart gate remains open. A failed orphan-process check must be fixed in service process supervision before acceptance; this probe alone does not implement a kernel-enforced death cascade.

**Operator acceptance (2026-11-04): PASS — live worker SIGKILL/restart.** As unprivileged `tgbot` on Debian 12, `pytest -p no:cacheprovider -q tests/test_media_worker_crash_smoke.py tests/test_media_active_cancellation.py tests/test_worker_crash_recovery.py` returned **14 passed in 0.59s**. Live `python -m bot.media_worker_crash_smoke --url 'https://www.youtube.com/watch?v=jNQXAC9IVRw' --wait-seconds 45` returned `After restart: ('failed', None, None) lease: ('cancelled',)` and `PASS: no orphan processes, no publication after worker restart`. The dispatcher logged `WorkerProtocolError: Неверный ответ media worker` when its IPC peer was SIGKILLed; expected for this probe. Gate accepted for this tested scenario; it does not establish safety under every crash mode, and production dispatch remains disabled pending full Stage 2 acceptance.

### Media resource-limit gate (2026-11-04): OPEN

Hardened `media_isolated_runner.execute_isolated` to signal its dedicated process group in `finally` even when the launcher exited, so grandchildren are not silently left behind. Added opt-in `bot.media_resource_limits_smoke` for real isolated-runner timeout and one-byte output-limit probes with private gateway/workspaces, plus `tests/test_media_resource_limits_smoke.py` argument guards. **Not yet run by operator**. `INCONCLUSIVE` is not acceptance; actual worker-wide resource and cleanup acceptance remains open. No production media dispatch enabled.

**Office test host `meet`, 2026-11-04 (NOT VPS):** operator ran `tests/test_media_resource_limits_smoke.py tests/test_media_active_cancellation.py tests/test_media_worker_crash_smoke.py`: **19 passed in 0.50s**. Live resource probe returned `INCONCLUSIVE` because namespace-local yt-dlp timed out but outer runner saw generic exit 1. Implemented dedicated exit 124 on namespace yt-dlp timeout and mapped it to `TimeoutError` in outer runner, with regression tests; **not yet operator retested**. Office results must not be represented as VPS acceptance. Full resource limits and cleanup gate remains OPEN.

### Opt-in end-to-end Telegram wiring (2026-11-05): IMPLEMENTED, NOT ACCEPTED

`bot.main.run_bot` now conditionally starts the real media dispatcher alongside Telegram polling and the already-running upload queue when `BOTDOWNLOAD_EXPERIMENTAL_MEDIA=1`. The worker gets the configured media root, and dispatcher stages completed downloads for upload queue. Telegram callback reports queued status only in this opt-in mode. Without the environment variable, download dispatch remains disabled by default in accordance with v1 security acceptance policy. This is **not yet a verified end-to-end Telegram delivery**. Current isolated media executor supports YouTube only and does not yet honor selected quality/MP3 variants; namespace support depends on host user namespace permissions, so universal VPS compatibility is NOT established. Do not advertise production-ready or enable by default until these gaps and real Telegram delivery are tested.
