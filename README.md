# Arcane for Home Assistant

[![Release](https://img.shields.io/github/v/release/Benni1123/home-assistant-arcane)](https://github.com/Benni1123/home-assistant-arcane/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)

A local Home Assistant integration for [Arcane](https://getarcane.app/) Docker
management. It exposes update controls and environment statistics through the
Arcane 2.x and 3.x APIs. The API is detected automatically, including for existing
Home Assistant entries.

Version 1.0.6 adds Arcane 3.0 compatibility: agent endpoints, image update checks
and records, container updates and lifecycle actions. Arcane 3 no longer provides
the old dashboard endpoint; the available-update count is derived from monitored
containers instead. No API key or entity ID changes are required.

Version 1.0.7 adds a dedicated **Start** button for stopped containers.
**Resume** only unpauses paused containers. Lifecycle buttons are available only
in appropriate states. After start, stop, restart, pause or resume, the affected
container's state is read directly immediately and at 3, 6, 9, 12 and 15 seconds.
These lightweight checks run in the background, independently of the normal
five-minute statistics polling, and are cancelled when the integration unloads.

Version 1.0.8 fixes update routing in Arcane 3: updates use Arcane's updater
endpoint, not the raw container redeploy endpoint. This restores Arcane's
self-update handling and retains its project-aware update strategy.

It also adds disk capacity/usage/free-space sensors, Docker reclaimable-space
estimates, and an admin-only selective cleanup action.

### Storage and cleanup

Storage sensors appear on the Arcane environment device. Host disk readings use
one short-lived system-stats WebSocket snapshot per normal polling cycle; the
connection closes immediately. They describe Arcane's configured disk-usage
path (or its root filesystem), **not every physical drive or SMART health**.
Docker reclaimable-size estimates are cached for 15 minutes, because Docker's
disk-usage calculation can be expensive. Missing permissions/readings are not
reported as zero.

In Home Assistant, open **Developer tools → Actions → Arcane: Clean up Docker
resources** (`arcane.prune`). Select the Arcane environment and any combination:

| Resource | Modes |
| --- | --- |
| Containers | None, stopped, older than a specified age |
| Images | None, dangling, all unused, older than a specified age |
| Networks | None, unused, older than a specified age |
| Volumes | None, anonymous unused, all unused |
| Build cache | None, unused, all cache, older than a specified age |

Every category defaults to **None**. Each age-filtered category has its own age
in hours. Confirm permanent deletion before submitting; **any volume deletion
requires a second volume-data confirmation**, including anonymous volumes.
Stopped containers may contain valuable writable-layer data. Deleting them can
also make their images and volumes eligible for deletion in the same run.
Back up important data first.

Cleanup requires Arcane 3 and an API key with `system:prune`; storage/progress
readings also need the corresponding read permissions. The integration never
performs cleanup during polling, startup or installation and never retries a
cleanup POST. The action accepts only one selected environment, not all agents.
Its optional response returns acceptance and an activity ID, not a claim of
completion. The cleanup-status sensor monitors the activity for up to two minutes;
if it cannot confirm completion it shows **Unknown**, and Arcane's activity
page remains authoritative. Storage/counts are refreshed after monitoring.

### Security

Arcane 3 security sensors expose its stored priority score/band, vulnerability
counts by severity, scan coverage, actionable/fixable findings, exposed
critical/high findings and (when threat intelligence has been synchronized)
known-exploited, overdue KEV and high-EPSS counts. Priority scores range from
**0 to 100; higher means higher priority**, not better protection. The score's
attributes include the highest-priority CVE/image/package and the available fix.

Security readings follow the normal polling interval. Missing permission,
disabled security features, unavailable scores and unsynchronized threat
intelligence do **not** become zero/safe readings. Counts cover scanned images,
not necessarily all installed images. The separate threat-intelligence sensor
indicates disabled, not synchronized, stale or ready data.

Press **Scan all images for vulnerabilities** on the environment device to
request scans of all its images, including unused images. This can consume
substantial CPU, disk and network resources and may download scanner databases.
The integration never starts scans during startup or periodic polling, never
installs patches and never retries scan POSTs. Bulk requests are limited to
1000 images and duplicate image IDs are submitted only once.

The **Security scan request** sensor shows submission progress and accepted/
failed counts, **not completion of all scans**. Requests run in the background,
with read-only result refreshes immediately and at 15, 60, 120 and 300 seconds
after submission. The scan button is disabled during this follow-up window to
avoid accidental duplicate requests. Scans already accepted by Arcane continue
if Home Assistant reloads or shuts down; remaining submissions are cancelled.
Arcane's activity page is authoritative for scan completion.

The API key needs vulnerability read access for sensors, plus image-list and
vulnerability-scan access for the button. A sample native HA gauge/history
dashboard is provided in `examples/security-dashboard.yaml`; replace the example
entity IDs with yours. No custom dashboard cards are required.

### Adding an agent later

Add another **Arcane** integration entry from **Settings → Devices & services →
Add integration → Arcane → Use an existing Arcane connection**. Select an
existing entry for your manager, then choose the new agent. The saved manager
URL, personal API key and TLS preference are copied internally; they are not
shown in the selection form and do not need to be entered again.

The manager's current environment list is fetched on each attempt. Already
configured environments on that manager and disabled agents are hidden.
If no new environment is listed, connect/enable the agent in Arcane and check
that the API key has access. Existing entries and entity IDs remain unchanged.
Each new entry stores its own connection settings, so changing/removing the
source entry later does not silently change the new entry's credentials.

**Connect a new Arcane manager** retains the manual setup path. Do not enter an
agent URL or enrollment/agent token: the manager proxies access to the selected
environment. Storage, security, cleanup and container actions are scoped to
that entry's selected environment.

## Features

- one Home Assistant `update` entity per Docker container
- install container updates through Home Assistant; Arcane keeps control of the
  update/recreate strategy
- manual registry scan button
- container, image, volume, network and project statistics
- published-port count, Docker CPU/memory and version diagnostics
- Arcane integration branding and dynamic container artwork
- multiple Arcane environments
- UI config flow, options flow and redacted diagnostics
- English and German translations

Container update entities use `iconLightUrl`/`iconDarkUrl` supplied by Arcane.
For the Arcane container itself, the integration falls back to Arcane's local
PWA icon. Other containers without icon metadata retain the Docker icon. Add
custom metadata in Arcane if you want a particular service icon to appear.

## Install with HACS

1. Open HACS, select **Integrations**, then **Custom repositories**.
2. Add `https://github.com/Benni1123/home-assistant-arcane` as an integration.
3. Download **Arcane** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration** and search for
   **Arcane**.

The canonical repository URL is:

`https://github.com/Benni1123/home-assistant-arcane`

## Manual installation

Copy `custom_components/arcane` to `/config/custom_components/arcane`, restart
Home Assistant, then add the integration from **Settings → Devices & services**.

When upgrading, replace the existing `arcane` directory and restart Home
Assistant. Existing config entries, API keys and entity IDs are retained.

## Configuration

- **Arcane URL:** for example `http://10.0.0.90:3552`
- **API key:** create a personal administrator API key in Arcane
- **Verify TLS:** disable only when you deliberately use self-signed HTTPS

Use the URL of the Arcane manager itself and a personal API key from Arcane's
settings. Do not add the Arcane manager as a remote (direct/edge) environment
and do not use an environment or agent key: Home Assistant talks to the manager,
which already includes the local Docker environment. Remote environments only
work in Home Assistant if they are connected and reachable in Arcane.

The integration reads Arcane's cached update status every five minutes by
default. A live registry query only runs when the **Check for updates** button
is pressed or Arcane performs its own scheduled check. Change the polling
interval through the integration options.

The API key needs read access to environments, containers, image updates,
images, volumes, networks, projects, ports and system information, plus update
check and container update permissions. Optional statistics with missing
permissions become unavailable without taking the integration offline.

Arcane's deliberately slow volume-size calculation endpoint is not polled.
The integration exposes volume counts but does not repeatedly scan Docker's
volume contents.

## Safety

Installing an update asks Arcane to pull the image and recreate the affected
container or project. It does not reboot the Docker host or LXC. Volumes and
bind mounts remain attached, but backups are still recommended before database
or other stateful-service updates.

## Development

Run the regression tests with Python 3.13 (matching CI):

```sh
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

### Automatic releases

GitHub Actions tests every pull request. After integration changes are merged
or pushed to `main`, the release workflow validates the latest `main`, builds an
installation ZIP, pushes a version tag and publishes a GitHub release with
release notes taken from the merged pull requests.

Put a `## Changelog` section in each PR description. The release notes include
that section and a link to the PR. If it is absent, the PR body (or title for an
empty body) is used. All merged PRs since the previous release are included,
including when several merges are combined into one release.

If the manifest version is newer than all existing stable version tags, it is
used as-is. Otherwise the workflow increments the highest patch version and
commits the manifest update to `main` as `github-actions[bot]`. For minor or major
releases, raise `custom_components/arcane/manifest.json` in the PR. A manual
**Actions → Release → Run workflow** run can retry a failed publication.

Only the built-in `GITHUB_TOKEN` is used; no extra secret is required. Repository
rules must allow GitHub Actions to push release commits to `main` and create
`v*` tags. If branch protection forbids direct bot pushes, the automatic patch
bump will fail safely; increase the manifest version in the PR instead.

The repository is structured for HACS and includes HACS/hassfest validation.
Please include Home Assistant, Arcane and integration versions when reporting
an issue. Diagnostics can be downloaded from the integration page; API keys are
redacted.

## License and trademarks

Integration code is available under the BSD 3-Clause License. Arcane brand
assets are sourced from the Arcane project and remain property of their
respective owner. See [NOTICE.md](NOTICE.md).
