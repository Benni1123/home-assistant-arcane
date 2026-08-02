# Arcane for Home Assistant

[![Release](https://img.shields.io/github/v/release/Benni1123/home-assistant-arcane)](https://github.com/Benni1123/home-assistant-arcane/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)

A local Home Assistant integration for [Arcane](https://getarcane.app/) Docker
management. It exposes update controls and environment statistics through the
Arcane 2.x API. Version 1.0.0 was developed against the Arcane 2.6.0 OpenAPI
specification.

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

The repository is structured for HACS and includes HACS/hassfest validation.
Please include Home Assistant, Arcane and integration versions when reporting
an issue. Diagnostics can be downloaded from the integration page; API keys are
redacted.

For the initial GitHub publication and release steps, see
[PUBLISHING.md](PUBLISHING.md).

## License and trademarks

Integration code is available under the BSD 3-Clause License. Arcane brand
assets are sourced from the Arcane project and remain property of their
respective owner. See [NOTICE.md](NOTICE.md).
