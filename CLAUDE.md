# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

```bash
uv sync                                        # or: pip install -e ".[dev]"
pytest -q                                      # run all tests (anyio mode = auto)
pytest tests/test_pipeline.py::test_name       # run a single test
ruff check                                     # lint (line length 100, target py311)
mypy lora_bridge                               # strict typing
LORA_BRIDGE_CONFIG=config.yaml lora-bridge     # entrypoint defined in pyproject.scripts
```

Runtime env vars (read once in `lora_bridge/settings.py`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `LORA_BRIDGE_CONFIG` | `config.yaml` | YAML config path (supports `${ENV}` via envyaml) |
| `LORA_BRIDGE_DB`     | `lora_bridge.sqlite` | SQLite journal of intents (§11.1 recovery) |
| `LORA_BRIDGE_LOG`    | `INFO` | logging level |

## Architecture

The codebase follows **hexagonal architecture** (ports & adapters). The authoritative spec is `docs/ARCHITECTURE.md`; section references in code comments (§5, §6, AD-4 …) point there.

### Layering (strict, do not cross)

- `lora_bridge/domain/` — models (`Message`, `ChannelRef`, `DeliveryStatus`, `Room`, …) and the single `Transport` ABC. **Depends on nothing.** Both LoRa clients and messenger clients implement `Transport`; their differences live in `Capabilities` (max_text_bytes, egress_rate, supports_status_feedback, emits_tx_done).
- `lora_bridge/core/` — orchestration. Knows the domain ports but **not** MeshCore/Telegram. Pieces: `Bridge` (fan-in + routing), `CommitQueue` (bounded + token bucket + admission TTL), `EgressWorker` (one per node, gated on commit), `TtlDedup`, `LoopGuard`, `StatusDispatcher`, `DropNotifier`, `SqliteJournal`, `RoomRegistry`.
- `lora_bridge/transports/` — adapters. `meshcore/` for the LoRa radio, `telegram/` for the messenger. New transports plug in by implementing `Transport` and being registered in `wiring.py`.
- `lora_bridge/wiring.py` — fabrics: `AppConfig → NodeRuntime`/`Transport`/`RoomRegistry`. The only place that knows concrete transport classes.
- `lora_bridge/app.py` — composition root: load env settings, parse YAML via `EnvYAML`, run journal recovery, build `Bridge`, hand off to `anyio.run`.

### Central invariants

These come from the LoRa channel's physical constraints and must be preserved by any change to `core/`:

1. **LoRa as commit-log (AD-4).** A message from a messenger first goes to air; only **after commit** is it mirrored to the other messengers (`Bridge.on_committed`). Do not short-circuit this — messengers are mirrors of the channel, not parallel chats.
2. **One egress worker per node (AD-6).** Radio is half-duplex. Per-node `NodeRuntime` holds one `CommitQueue` and `Bridge.build_worker` creates exactly one `EgressWorker` consuming it. Never spawn extra producers/consumers around `node.transport.send()`.
3. **Persist-before-act (§11.1).** `EgressWorker.transmit` calls `journal.mark_transmitting()` **before** `lora.send()`. On restart `app.recover()` resurrects PENDING (re-enqueue) and turns TRANSMITTING into UNKNOWN — we do not retransmit because LoRa channel sends are at-most-once.
4. **All-or-nothing size (AD-11).** If `[type:nick] + text` exceeds `transport.capabilities.max_text_bytes`, reject with `TOO_LONG`. **Never truncate text.** Nick may be truncated (`LabelFormat.max_nick_bytes`).
5. **Dedup + loop-guard are mandatory (AD-9).** Every LoRa-ingress passes through `node.dedup.accept()` (mesh duplicates) and `node.loop_guard.is_echo()` (own TX echo). They share a TTL with `policies.dedup_ttl_seconds`.
6. **Commit semantics depend on endpoint type (AD-5 / §5.1).** `public`/`private` commit on MeshCore `MSG_OK` (flood, no real delivery); `room_server` commits on ACK `0x82` and has backfill. The core treats `SendResult.ok` uniformly; only the MeshCore adapter knows the difference. `SendResult.busy` (TABLE_FULL) is retried with backoff inside `EgressWorker.send_with_retry` — it is **not** a FAILED.

### Concurrency model

- Built on `anyio` (asyncio backend). `Bridge.run` creates a single task group: one consumer task per transport, one egress worker per node, one notifier flush loop. No raw `asyncio.create_task` — use the task group so cancellation propagates correctly.
- `CommitQueue` uses `anyio.create_memory_object_stream` for the queue and exposes `offer()` (non-blocking; returns `False` on full/rate-limited) and async iteration on the receive side. Don't await inside `offer`.
- Mirror-to-messenger errors are swallowed in `Bridge.mirror_to_messenger` on purpose (`# noqa: BLE001`) — a flaky messenger must not stall the LoRa pipeline.

### Configuration model

- `config.example.yaml` is the canonical example; never edit `config.yaml` (gitignored).
- Pydantic v2 models live in `lora_bridge/config/schema/`. `AppConfig` runs cross-validation (`validate_room_refs`) so that every `rooms[].lora` and `rooms[].subscribers[].lora` references an existing node + endpoint, and every messenger subscriber references an existing messenger id.
- `endpoints` is a **dict** keyed by name; that name becomes the `ChannelRef.channel` for that LoRa endpoint and the join key with `rooms[].lora.endpoint`.
- A `Room` is `1 LoRa endpoint ↔ N subscribers`. Subscribers can be messengers (Telegram chat + optional topic) **or** another LoRa endpoint (LoRa↔LoRa relay). The `1 LoRa + messengers` form is what `Bridge.admit` relies on (it picks `targets[0]`).
- The canonical `ChannelRef.channel` for a Telegram subscriber is built by `messenger_channel(chat, topic)` (`"chat#topic"` or just `"chat"`). The adapter **must** emit RX messages using the same encoding, otherwise `RoomRegistry.for_source` won't match.

### Tests

- `pytest-anyio` with `anyio_mode = "auto"` — async tests need no decorator. `tests/test_wiring_integration.py` is the only end-to-end path (YAML → wiring → Bridge), using `tests/helpers/fakes.py::FakeTransport` swapped via monkeypatch in `wiring` namespace. Other test files exercise units (`test_pipeline.py`, `test_dedup_loopguard.py`, `test_ttl_scenarios.py`, `test_transform.py`, `test_config_*.py`).
- When you change `core/`, also run the integration test — it's the cheapest way to catch a broken contract between `wiring`, `Bridge`, and a `Transport`.

### Project status / hardware notes

Per `README.md`: the core layer is implemented and covered by tests; the MeshCore and Telegram adapters are written against their library APIs but **not yet validated against live hardware/tokens**. Call sites that need verification are tagged `# verify` — keep that marker until checked on real devices.

### House style

- Source comments, docstrings and architecture docs are in **Russian**. Match that when editing existing code; section refs like `§6` or `AD-5` point into `docs/ARCHITECTURE.md`.
- `pyproject.toml` enforces `ruff` line-length 100 and `mypy --strict` for `lora_bridge`. New code must type-check cleanly.


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
