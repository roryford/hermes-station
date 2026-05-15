# Security Policy

## Supported versions

Only the latest release is actively maintained. Security fixes are not backported to older versions.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report them privately via [GitHub's "Report a vulnerability" feature](https://github.com/roryford/hermes-station/security/advisories/new) (Security → Advisories → New draft advisory). This keeps details out of the public record until a fix is ready.

Include:
- A description of the vulnerability and its impact
- Steps to reproduce (config, request, or environment needed)
- The version of hermes-station and hermes-agent you're running

You can expect an acknowledgement within 3 business days and a resolution or status update within 14 days.

## Scope

In-scope:
- The hermes-station Python codebase (`hermes_station/`)
- The Docker image and its build process (`Dockerfile`)
- Authentication and session handling (`admin/auth.py`)
- The HTTP proxy and header-injection surface (`proxy.py`)
- Secret storage and file permission handling (`config.py`, `secrets.py`)

Out of scope (report upstream instead):
- Vulnerabilities in [hermes-agent](https://github.com/NousResearch/hermes-agent)
- Vulnerabilities in [hermes-webui](https://github.com/nesquena/hermes-webui)
- Third-party LLM provider APIs

## Deployment hardening notes

The most common misconfiguration is leaving `HERMES_ADMIN_PASSWORD` and `HERMES_WEBUI_PASSWORD` unset. Without these, both UIs are open to anyone who can reach the container. See [`docs/configuration.md`](docs/configuration.md) for hardening guidance.
