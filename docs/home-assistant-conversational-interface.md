# Home Assistant conversational interface

## Authoritative boundaries

Home Assistant remains the authoritative device/entity registry, state source,
history source, and automation engine. Home-AI holds only bounded semantic
adapters and conversation referents. Public web search is not used for home
inventory, state, history, routine, or control answers.

Normal household scope is enabled, non-hidden `light.*` and `switch.*`
entities authorized by Home-AI. Update entities, helpers, diagnostics, and
other domains are excluded from ordinary counts. A physical-device count is
deduplicated by Home Assistant `device_id`; individual channels remain explicit
entities. Scenes, scripts, and automations are separately listed read-only.

State, availability, connectivity, and freshness are distinct. `off` is a
functional state. `unavailable` and `unknown` are never counted as off.
Availability is what Home Assistant reports; it is not proof of physical LAN
or cloud connectivity. `last_changed` is not an offline detector. Every live
view includes its observation time and source.

## Actual inventory (2026-09-18)

Home Assistant 2026.9.2 reports four areas (Living Room, Kitchen, Bedroom,
Office), no configured floors, 16 devices, and 29 entities. The normal Home-AI
household scope currently contains nine physical Tuya devices/entities:

| Area | Entity | Friendly/device name | Capability | Observed availability |
|---|---|---|---|---|
| Office | `light.office_light` | Office Light | brightness, colour temperature, HS colour | unavailable |
| Unassigned | `light.bedroom_lamp` | Bedroom Lamp | brightness, colour temperature, HS colour | unavailable |
| Bedroom | `light.music_star_light` | Music Star Light | brightness, HS colour, white | unavailable |
| Living Room | `light.light_fixture_1` | Light fixture 1 | brightness, colour temperature, HS colour | available |
| Living Room | `light.light_fixture_2` | Light Fixture 2 | brightness, colour temperature, HS colour | available |
| Living Room | `light.light_fixture_3` | Light Fixture 3 | brightness, colour temperature, HS colour | available |
| Office | `switch.neon_lights_socket_1` | Neon Lights Socket 1 | on/off only; attached-load semantics not observed | available |
| Office | `switch.neon_lights_socket_1_2` | Neon lights Socket 1 | on/off only; attached-load semantics not observed | available |
| Living Room | `switch.neon_light_socket_1` | Neon Light Socket 1 | on/off only; attached-load semantics not observed | available |

The unassigned Bedroom Lamp is a real naming/area ambiguity and is not guessed
into the Bedroom area. No floors, energy meters, battery sensors, temperature
sensors, or connectivity sensors for these devices are currently configured.

## Tool responsibilities

- `home_find_device`: scoped inventory, capability discovery, physical/entity counts.
- `home_get_state`: current functional state and availability for a device,
  group, area, or broad state selector.
- `home_get_area_state`: exact Home Assistant area snapshot.
- `home_control`: bounded capability-aware control and bounded HA-reported
  outcome verification.
- `home_activate_scene`: exact scene activation; availability requires separate
  policy review of scene effects.
- `home_get_activity`: bounded retained Home Assistant state history with
  explicit attribution limitations.
- `home_list_routines`: read-only scenes/scripts/automations inventory.

## Control policy

Unavailable/unknown targets are reported, not treated as off. Brightness and
colour operations require actual advertised capability. Relative brightness
requires a known baseline for every selected light; each member receives its
own bounded target. Setting brightness/colour on an
off light refuses rather than silently energizing it. Whole-home writes exclude
every switch/outlet unless its exact entity is operator-classified in
`HOME_BULK_SAFE_ENTITIES`; names alone never make an outlet safe. New entities
receive read visibility only within the domain scope and never implicit write
approval. Individual writes are restricted to the nine existing entities in
the migration policy (`HOME_WRITE_ALLOWED_ENTITIES`); scene activation defaults
closed through `HOME_SCENE_ALLOWED_ENTITIES`.

Service submission, Home Assistant's reported target state, verification
timeout, partial success, and indeterminate outcome are separate results.
Reported state is not independent physical verification. Retries use explicit
target-state operations; non-idempotent toggle is not exposed.

## Known limitations and later work

The first implementation uses a bounded authenticated WebSocket snapshot for
registries and state. A persistent subscribed projection remains a measured
optimization, not a second authoritative registry. Scheduling writes are not
implemented; existing Home Assistant mechanisms must be inspected and tested
for persistence, timezone/DST, missed triggers, cancellation, and permission
changes first. Scene effects are not yet introspected. Tuya unavailability does
not establish whether the device, cloud provider, account, or integration is
the failing layer. No camera, lock, alarm, garage, occupancy, or host-control
scope is added.

## Qualification and deployment (2026-09-18)

- Supported WebSocket registry/state snapshot: 13.7 ms; 24-hour REST history:
  44.9 ms. This did not justify a persistent subscribed cache yet.
- Automated qualification: 57 core/property/failure tests and 130
  production-shaped Assistant conversation tests passed. Writes used the
  in-memory fake backend only, including partial groups, exact-set exclusions,
  30% brightness, and protected-load bulk shutdown.
- Real Open WebUI-network reads passed through Open WebUI → Home-AI → Tools for
  whole-home state, room follow-up, unavailable-device clarification, repeated
  read, counts, capability discovery, history, diagnosis, and routine listing.
  Normal structured reads were approximately 35–112 ms in the acceptance run.
- Production images are `home-ai-tools:conversational-20260918` and
  `home-ai-assistant:conversational-20260918`. Home Assistant and Google Home
  were not recreated or reconfigured. The dedicated Frigate network contract,
  health checks, authenticated production smoke, and template pins passed.
- No live device control was executed. A physical check remains separate and
  requires an explicitly named device and action.

Representative live read dialogue:

- “What's on?” → no authorized devices on; unavailable devices were not counted off.
- “What about the bedroom?” → retained the `on` filter and applied Bedroom scope.
- “What's unavailable?” → three named unavailable lights.
- “How long has that one been unavailable?” → asked which of the three; “Bedroom
  Lamp” then reported HA's continuous unavailable observation and declined to
  invent a connectivity cause.
- “Can that light be dimmed?” → reported the retained light's advertised capability.
- “When did Light Fixture 1 turn on?” → no retained matching event, explicitly
  not “never.”
- “What scenes do I have?” → no enabled scenes/scripts/automations currently exist.

## Rollback

Rollback is the previous qualified Home-AI Tools and Assistant image plus their
captured container definitions. Home Assistant configuration, registries,
Google Home, device entity IDs, areas, and automations are not modified by this
adapter change.
