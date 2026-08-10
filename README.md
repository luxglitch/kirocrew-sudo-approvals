# KiroCrew Sudo Approvals

[![CI and Release](https://github.com/luxglitch/kirocrew-sudo-approvals/actions/workflows/ci-release.yml/badge.svg)](https://github.com/luxglitch/kirocrew-sudo-approvals/actions/workflows/ci-release.yml)

A community-maintained KiroCrew app for reviewing privileged command requests
and using explicitly armed, server-enforced auto-approval windows.

This is not an official KiroCrew distribution. The installed app name remains
`sudo-approvals` for stable routes and upgrades.

## What it does

- Shows the requested command and working directory before manual approval.
- Supports manual **Approve** and **Deny** decisions.
- Offers bounded auto-approval windows of **5 minutes, 15 minutes, 30 minutes,
  1 hour, or 2 hours**.
- Labels unattended decisions as `AUTO-APPROVED` in Recent history.
- Includes a generic `sudo-with-approval` wrapper with configurable FIFO paths.
- Runs without a fixed backend port or KiroCrew-internal Python imports.

## Important security warning

An active auto-approval window permits unattended privileged execution for its
remaining duration. Only arm one when you trust the active automation and can
monitor its work. Disarm it as soon as unattended access is no longer needed.

Approval is not operating-system authentication. This app never receives or
stores a sudo password. The wrapper ultimately executes `sudo -A`, so the host
must already have a suitable askpass or passwordless sudo configuration.

## Requirements

- KiroCrew 0.1.3 or newer
- Linux or macOS
- Python 3.10 or newer
- Bash
- A working `sudo -A` configuration for the intended commands

## Install from Git

```bash
git clone https://github.com/luxglitch/kirocrew-sudo-approvals.git
cd kirocrew-sudo-approvals
kirocrew app install "$PWD"
kirocrew app enable sudo-approvals
mkdir -p "$HOME/.local/bin"
install -m 755 bin/sudo-with-approval "$HOME/.local/bin/sudo-with-approval"
```

Ensure `$HOME/.local/bin` is on the automation tool's `PATH`. If the KiroCrew
gateway was already running during installation, restart it so stable KiroCrew
`0.1.3` launches the new backend. Confirm the app is healthy before routing any
privileged command through the wrapper.

The full-page app works through KiroCrew's documented `AppHost` API and does not
require a dashboard-core patch. A KiroCrew build that supports installed-app
panel tabs can additionally show it in the chat right panel and auto-open it
for a new pending request. This repository does not modify KiroCrew core.

## Use the wrapper

Configure the automation tool to call `sudo-with-approval` instead of `sudo`:

```bash
sudo-with-approval systemctl status example.service
```

The wrapper writes the request, waits for an exact `y` or `n` response, and
only invokes `sudo -A` after approval. It fails closed if the approval service
or FIFOs are unavailable.

## Auto-approval windows

1. Select 5 minutes, 15 minutes, 30 minutes, 1 hour, or 2 hours.
2. Select **Arm auto-approve**.
3. Review the warning and explicitly confirm.
4. Use **Disarm now** at any time to stop automatic decisions.

The backend owns and enforces the deadline using a monotonic clock. A backend
restart clears the window, and unsupported durations are rejected.

## Configuration

The app and wrapper share these optional environment variables:

| Variable | Default |
|---|---|
| `SUDO_APPROVAL_RUNTIME_DIR` | `/tmp/sudo-approvals-<uid>-<install-hash>` (mode `0700`) |
| `SUDO_APPROVAL_REQUEST_FIFO` | `<runtime-dir>/request.fifo` (mode `0600`) |
| `SUDO_APPROVAL_RESPONSE_FIFO` | `<runtime-dir>/response.fifo` (mode `0600`) |

The install hash is the first 12 hexadecimal characters of SHA-256 over the
canonical app installation path. This keeps separate KiroCrew homes owned by
the same Unix user off each other's FIFOs. An installed or symlinked wrapper
uses its own app root; a copied wrapper resolves
`${KIROCREW_HOME:-$HOME/.kiro/crew}/apps/sudo-approvals`.

Set identical explicit overrides in the KiroCrew gateway environment and wrapper
environment when using custom paths. Existing paths must be owner-controlled
FIFOs; regular-file substitution and ownership mismatches fail closed.

## FIFO protocol

The parser recognizes `CMD:` and `CWD:` lines while preserving and displaying
the complete request. A minimal request is:

```text
CMD: sudo systemctl restart example.service
CWD: /srv/example
```

The response FIFO contains exactly `y` or `n` followed by a newline.

## Security model

- Auto-approval is off by default and cannot be permanent or unbounded.
- Arming requires explicit UI confirmation and always has a server-owned expiry.
- Backend restarts reset auto-approval.
- Automatic and manual decisions remain distinguishable in audit history.
- The backend binds to loopback and requires KiroCrew's signed
  `X-KiroCrew-Proxy` header for every `/api/*` request.
- The runtime directory is owner-only (`0700`) and its FIFOs are owner-only
  (`0600`).
- The backend rejects unsafe pre-existing paths instead of silently replacing
  or accepting them.
- Per-installation secrets, runtime data, and installation metadata are excluded
  from source and release archives.

## Repository layout

```text
kirocrew-sudo-approvals/
├── .github/workflows/ci-release.yml
├── .gitignore
├── LICENSE
├── README.md
├── app.json
├── backend/
│   ├── server.py
│   └── test_server.py
├── bin/
│   └── sudo-with-approval
└── ui/
    └── index.mjs
```

## Development

The project uses only the Python standard library and host-provided UI modules.
Run the local checks with:

```bash
python3 -B -m unittest -v backend/test_server.py
node --check ui/index.mjs
bash -n bin/sudo-with-approval
python3 -m json.tool app.json >/dev/null
```

## Releases

A tag named `v<version>` must exactly match the `version` in `app.json`.
GitHub Actions validates Linux and macOS, then publishes `.tar.gz` and `.zip`
archives plus `SHA256SUMS`. Release archives contain a top-level
`sudo-approvals/` directory ready for `kirocrew app install`.

## Compatibility policy

- The manifest declares `minKiroCrewVersion: 0.1.3`.
- The UI consumes only host-provided React, `@kirocrew/app-sdk`, and Lucide.
- API calls use the stable `/apps/<name>/api/*` reverse-proxy path.
- The backend does not import KiroCrew internals or assume a fixed backend port.
- Right-panel integration is optional; full-page operation remains the portable
  compatibility baseline.

## License

KiroCrew Sudo Approvals is available under the [MIT License](LICENSE).
