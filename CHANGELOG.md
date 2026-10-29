# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/compare/v0.4.0...v0.5.0) (2026-10-29)


### Features

* make syncing fully manual with an index-gated flat folder ([#24](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/24)) ([021c038](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/021c03890aa456c9d0e123240da3149a67f58119))
* report every transferred book in the review list ([#30](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/30)) ([8c85b18](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/8c85b18bbc44e0a46bafeeab31b6e6a36c89a276))
* report transfer speed and time left in the review window ([#27](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/27)) ([02f20c9](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/02f20c98055a68e3d46ca880afe3a720cc528719))
* upload several books at the same time ([#28](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/28)) ([85a42ac](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/85a42aca083b21dd43f74752de3433fa4f7f557e))
* write the index while the transfer runs ([#29](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/29)) ([3ca5160](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/3ca5160c3fbc53686c83fd1ab753b3d30b11487f))


### Tests

* cover every line of the manual sync rework ([#26](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/26)) ([69756b6](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/69756b6537aeeb6a04f175af70ab889d62f94a6f))

## [0.4.0](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/compare/v0.3.0...v0.4.0) (2026-10-26)


### Features

* add tree folder picker and direct export target ([#16](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/16)) ([7e22a2f](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/7e22a2f64bf497757c14c16470be4e1cab01f193))
* use a shared Entra app by default ([#20](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/20)) ([40b2c41](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/40b2c41c0b2024aedfeb032a6f17894b8dd7028d))


### Bug Fixes

* avoid false app id change warning ([#18](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/18)) ([0e6ffd8](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/0e6ffd8e67a30c15107bebe869d423eea94aa81e))
* enlarge the OneDrive sign-in dialog ([#17](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/17)) ([5148d66](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/5148d6622abf8afbc759acb550345a6dc9ee5e1f))
* make toolbar icon sync without a menu ([#19](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/19)) ([0219b9a](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/0219b9a809dcf6321a1c8455503a2dfc6d10483b))
* restrict OneDrive access to /me/drive ([#14](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/14)) ([b4ac898](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/b4ac898200eab3240c1bf3fed30acd92c34d511b))

## [0.3.0](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/compare/v0.2.0...v0.3.0) (2026-10-25)


### Features

* add Microsoft Graph OneDrive sync ([#3](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/3)) ([200adc3](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/200adc3fde5f3cafb4f95d3a2ec79d99eb0911a8))


### Bug Fixes

* honor draft release config in release-please ([#5](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/5)) ([021290c](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/021290c3554f08b8b474cf6896a6e2b7ff0e9c71))
* sanitize mirror options and prefill library path ([aa31188](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/aa31188ccc3161e2631b3c5a4e8e328535eb20ab))


### Miscellaneous Chores

* bootstrap calibre OneDrive sync plugin ([158b9fc](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/158b9fc979b37b144c08e3a12e7d2798761bb0d8))
* **main:** release 0.1.0 ([67371a9](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/67371a9348d2e5e89aa677d310ff1e5741d6ab68))
* **main:** release 0.2.0 ([#4](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/4)) ([6770dd6](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/6770dd6eb4170655b6a9c43bee1ae92a31641019))

## [0.2.0](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/compare/v0.1.0...v0.2.0) (2026-10-25)


### Features

* add Microsoft Graph OneDrive sync ([#3](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/issues/3)) ([200adc3](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/200adc3fde5f3cafb4f95d3a2ec79d99eb0911a8))

## 0.1.0 (2026-10-24)


### Bug Fixes

* sanitize mirror options and prefill library path ([aa31188](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/commit/aa31188ccc3161e2631b3c5a4e8e328535eb20ab))

## [0.1.0](https://github.com/JohannesHolzhaeuer/calibre-onedrive-sync/releases/tag/v0.1.0)

### Features

- Bootstrap the calibre plugin: toolbar action, configuration dialog, settings
  persistence, path validation, build tooling, tests, CI and release
  automation.
