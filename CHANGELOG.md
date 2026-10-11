# Changelog

All notable changes to this integration are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html):

- **MAJOR** – breaking changes: removed or renamed entities, actions or options, or anything that
  requires you to change automations or reconfigure the integration.
- **MINOR** – new features that keep existing setups working.
- **PATCH** – bug fixes and small behaviour tweaks.

While the version is `0.x`, the integration is still settling in, and minor versions may include small
breaking changes. These are always listed under **Changed** or **Removed**.

## [Unreleased]

## [0.6.0] - 2026-10-11

### Added
- **Block prey** switch, the same setting as the app's switch: on blocks the flap when prey is detected, off
  only records events. It shows the requested state immediately, and `confirmed_by_device` / `pending`
  attributes show when the device has applied it.

## [0.5.0] - 2026-10-11

### Added
- **Slow down GIFs** option (0–200 %, default 25 %) under *Settings → Devices & services → ZeroMOUSE →
  Configure*. It applies to the animation entity, the Media panel and the download action.
- Integration icon: a cat stepping through a cat flap.

## [0.4.1] - 2026-10-11

### Changed
- Event GIFs play 25 % slower than capture speed.

### Fixed
- GIF frame timing is rounded to the format's 10 ms steps instead of being cut short.

## [0.4.0] - 2026-10-11

### Added
- Event history in the **Media** panel: ZeroMOUSE → device → day → event, covering the last 30 days, with
  thumbnails. Events play as GIFs built on demand.
- `zeromouse.download_images` can download the photos of any event in the history.

### Fixed
- GIF colours are taken from all frames, so frames with very different lighting (for example when the
  infrared light switches on) are no longer merged.

## [0.3.0] - 2026-10-11

### Added
- **Latest event animation** image entity: an animated GIF of all photos of the newest event, timed by each
  photo's capture time.
- `zeromouse.download_images` also saves `animation.gif`.

## [0.2.0] - 2026-10-11

### Added
- Live events through the AppSync real-time subscription the app uses. Entities, the Detection event and
  `zeromouse_event` update within seconds.
- **Live updates** diagnostic sensor.

### Changed
- Polling is every 5 minutes while the live connection is up, and every 60 seconds otherwise.
- The image entity retries when the photo is uploaded after the event.

## [0.1.0] - 2026-10-11

### Added
- First release: sign in with the ZeroMOUSE account, device status from the cloud, latest event photo,
  Detection event entity, prey and classification sensors, entry and exit modes, `zeromouse_event` bus
  event and the `zeromouse.download_images` action.
- English and Norwegian (Bokmål) translations.

[Unreleased]: https://github.com/kevinsch95/ha-zeromouse/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/kevinsch95/ha-zeromouse/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/kevinsch95/ha-zeromouse/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/kevinsch95/ha-zeromouse/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/kevinsch95/ha-zeromouse/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/kevinsch95/ha-zeromouse/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kevinsch95/ha-zeromouse/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kevinsch95/ha-zeromouse/releases/tag/v0.1.0
