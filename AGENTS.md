# Home-AI Infrastructure and Privacy Change Rules

These repository rules apply whenever Home-AI changes can affect shared Unraid services, deployments, credentials, QA, cameras, media, or network access.

## Before deployment

1. Identify the container/service owner, all consumers, state owner, and sensitive data.
2. Capture the live configuration and a state-aware rollback; preserve P0 session, confirmation, authentication, and credential protections.
3. Produce a pre-deployment diff for image/digest, networks/aliases, ports/bind addresses, mounts/modes, capabilities/devices, entrypoint/command, health/restart, secret references, and exposed routes.
4. Serialize changes affecting the same container, template, network, alias, port, or shared state. Never let QA removal/recreation detach production dependencies.

## Privacy and authorization

- Indoor camera images, clips, audio, thumbnails, stream URLs, event metadata, and household activity descriptions are sensitive. Never put them in Git, CI, logs, QA fixtures, external model context, or documentation.
- Camera access is an explicit server-side capability, separate from ordinary read-only status access. QA has no camera-media access unless a deliberately scoped, tested policy grants it.
- Never expose privileged Tools/MCP credentials to models or browsers. Treat Docker socket access, broad appdata mounts, host networking, and proxy-header trust as security-sensitive even when marked read-only.

## Update compatibility

- Use supported upstream APIs/configuration where possible. Do not modify third-party image source at runtime, inject middleware, or apply ad-hoc packages without documenting the version-coupled exception, compatibility detection, and recovery plan.
- Keep custom integration logic in owned services. A change must not make an application’s normal manual operation depend on Home-AI.
- Do not upgrade third-party images, migrate databases, restart Frigate/cameras, alter retention, restart Docker/Unraid, change router/firewall/VLAN/VPN, or broadly rotate credentials without the required approval.

## Verification

- Use isolated stores/fake executors and synthetic private media for write or privacy tests. Missing fake configuration must fail closed and never fall back to production.
- Verify the original application independently of Home-AI, including required access and denied access. For camera-related work, verify recording continuity without inspecting household content.
- Update [[Security Home]] and the relevant baseline/access/runbook note after material infrastructure findings or changes.
