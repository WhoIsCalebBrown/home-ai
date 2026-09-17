# Capability Gap Analysis — Unraid Management Agent MCP

Generated during the full-catalog live-validation round. The MCP server exposes
126 tools (see below for the read/write split); this document covers only the
gaps between what Home-AI could already answer and what a real Unraid host
question needs.

## MCP Inventory Summary

- **Total MCP tools**: 126
- **Read-only** (`readOnlyHint: true` or side-effect-free): 87
- **Write / state-changing / sensitive**: 39, including: `system_reboot`,
  `system_shutdown`, `array_action` (start/stop), VM lifecycle (`vm_action`,
  `clone_vm`, `restore_vm_snapshot`, `delete_vm_snapshot`), `container_action`
  (start/stop/restart/remove), `update_container`/`update_all_containers`,
  fan/CPU control (`set_fan_speed`, `set_cpu_governor`, `set_turbo_boost`),
  `parity_check_action`/`pause`/`resume`/`stop`, `execute_user_script`, an
  autonomous `agent_start_session`/`agent_approve_action` remediation
  subsystem, and more.
- **Exposed to Qwen directly**: 0. Only 6 new bounded Home-AI tools (below)
  ever call the MCP, each hardcoding exactly one read-only `tool_name`.

## Gap Matrix

| User Need | Existing Home-AI Tool | Existing Coverage | MCP Capability | Gap | Solution | Priority | Safety | Implemented? |
|---|---|---|---|---|---|---|---|---|
| Array/cache/disk capacity | `get_storage_status` | Only `/mnt/user` and `/mnt/cache` bytes, no per-disk or array-level detail | `get_array_status`, `list_disks` | No array%, no per-disk capacity, no cache-pool-specific number | New tool `unraid_storage_status` | HIGH | READ | **Yes** |
| Array/disk health (parity, SMART, temp) | none | none | `get_array_status`, `list_disks(include_smart)` | No health/SMART visibility at all | New tool `unraid_disk_health` | HIGH | READ | **Yes** |
| Named container uptime/network/ports/CPU/RAM | `get_container_status`/`list_containers` | Name/state/image only, no resource or network data | `get_container_info` | No resource or network facts | New tool `unraid_container_status` | HIGH | READ | **Yes** |
| "What's using the most RAM/CPU" | none | none | `list_containers` (per-container cpu_percent/memory), `get_docker_stats` | No leaderboard/ranking capability | New tool `unraid_container_metrics` | HIGH | READ | **Yes** |
| Container logs | `get_container_logs` | Full coverage already (docker.sock, lower latency, already validated) | `get_container_logs` | None — duplicate | Reuse existing tool | — | — | N/A (no new tool) |
| Quick server health summary | `get_server_overview` | Uptime + storage only, no array/container/CPU/RAM/alert picture | `get_health_status`, `get_firing_alerts` | No consolidated health view | New tool `unraid_system_health` | HIGH | READ | **Yes** |
| GPU utilization/VRAM/temp | `get_gpu_status` | Already correctly reports "no telemetry" (host genuinely has no exposed GPU) | `get_gpu_metrics` | None — same underlying absence, MCP unlikely to differ | No change | LOW | READ | N/A (not implemented, no evidence of benefit) |
| **Per-directory storage breakdown** ("what's using space in cache/appdata", "how much space does Plex use") | none | none | `list_shares` gives used/free/total, but confirmed via direct inspection that these numbers reflect the **underlying cache/array pool a share lives on, not that share's own actual consumption** (e.g. "Photos" and "appdata" shares showed identical used/free/total bytes despite being different shares, because both happen to live entirely on the same cache pool) | No true per-directory/per-share consumption data anywhere in the MCP | **Deferred — see below** | HIGH (most-requested capability in the brief) | Would need WRITE-classified `execute_user_script` | **No — REQUIRES USER APPROVAL** |
| Docker network membership ("what network is X on") | none | Folded into `unraid_container_status`'s `network_mode` field rather than a separate tool | `get_container_info.network_mode`, `list_docker_networks` | Minor — covered adequately by `unraid_container_status` | Reuse `unraid_container_status` | MEDIUM | READ | **Yes** (folded in, not a separate tool) |
| Port conflict detection | none | none | `get_port_conflicts` | Real gap, but no live user question in the brief needs it yet | Not built this round | LOW | READ | No |
| Parity check history/status | none | none | `get_parity_history`, `parity_check_action` (read-only status subset) | Could extend `unraid_disk_health` later | Deferred, low current demand | LOW | READ | No |
| UPS status | none | none | `get_ups_status`, `get_nut_status` | Only relevant if a UPS is actually attached; unconfirmed | Deferred pending confirmation a UPS exists | LOW | READ | No |
| ZFS pool/dataset/snapshot info | none | none | `get_zfs_pools`, `get_zfs_datasets`, `get_zfs_snapshots` | Only relevant if ZFS is in use on this host; unconfirmed (this array showed XFS/BTRFS-style disks, not ZFS) | Deferred pending confirmation ZFS is in use | LOW | READ | No |
| VM status/observability | none | none | `list_vms`, `get_vm_info`, `search_vms` | Real gap if VMs are used; `get_health_status` showed `total_vms: 0` on this host tonight, so currently no live user need | Deferred — no VMs currently running | LOW | READ | No |

## Directory/Storage-Breakdown Gap — Detailed Writeup (REQUIRES USER APPROVAL)

This was the single most-requested capability in the brief ("what's using all
my cache", "what's inside appdata", "how much storage is Plex using") and it
is the one gap this round could not close safely.

**What was confirmed by direct inspection tonight:** `list_shares` returns
`used_bytes`/`free_bytes`/`total_bytes`/`usage_percent` per share, but these
values are pool-level, not share-level — two different shares living on the
same cache pool report identical numbers. Presenting these as "how much space
Plex is using" would be a real, confirmable factual error, so `unraid_storage_status`
deliberately only reports array/cache/disk-level capacity, never per-share
numbers, and no "storage breakdown" tool was built this round.

**Why it wasn't built anyway:** the only path to true per-directory
consumption (something `du`-equivalent, scoped to `/mnt/user/appdata`,
`/mnt/user/Media`, etc.) is Unraid's User Scripts plugin via the MCP's
`execute_user_script` — which is a **write-permission** MCP tool per its own
schema, and the hard safety boundary for this round explicitly excluded any
state-changing/write MCP capability. Beyond the permission classification,
building this safely also requires:

1. A specific, reviewed script (e.g. `du -sh /mnt/user/appdata/*/ | sort -rh`)
   that the user provisions and pre-approves in Unraid's User Scripts UI —
   Home-AI must never construct or select a filesystem path on its own.
2. A bounded Home-AI wrapper that calls only that one named, pre-approved
   script and parses its fixed output shape — never a generic
   `run_script(name)` or `run_command(...)` interface Qwen could redirect.

**Recommendation:** if this capability is wanted, the user should first
create and test one Unraid User Script (a fixed `du` breakdown of
`/mnt/user/appdata` and `/mnt/user/<share>`, one line per subfolder) via
Unraid's own User Scripts UI, then hand Home-AI its exact script name. A
bounded `unraid_storage_breakdown(target: "appdata"|"cache"|<share name>)`
tool could then wrap `execute_user_script` for that one named script only,
never accepting an arbitrary path or script name from the model. This is
explicitly deferred pending that approval and script provisioning — it was
not attempted with a lower-safety substitute.

## New Home-AI Tools Added This Round

| Tool | User Question Enabled | Backend MCP Tool(s) | Read/Write |
|---|---|---|---|
| `unraid_storage_status` | "How full is the cache drive?", "How much space is left on the array?", "Which disk is fullest?" | `get_array_status`, `list_disks` | READ |
| `unraid_disk_health` | "Is the array healthy?", "Are any disks having errors?", "Which drive is hottest?" | `get_array_status`, `list_disks` | READ |
| `unraid_container_status` | "How long has Plex been running?", "What network is Home-AI-Tools on?", "How much memory is Home-AI using?" | `get_container_info` | READ |
| `unraid_container_metrics` | "What's using the most RAM/CPU?", "Which containers are unhealthy?" | `list_containers` | READ |
| `unraid_system_health` | "Give me a quick server status.", "Is anything wrong with the server?" | `get_health_status`, `get_firing_alerts` | READ |

Not added (see gap matrix above for reasoning): `unraid_container_logs`
(duplicate), a storage-breakdown tool (deferred, needs approval), GPU/parity/
UPS/ZFS/VM tools (no confirmed live need on this host tonight).
