# Third-Party Notices

The project currently vendors no runtime libraries, fonts, or frontend assets. The Python packages installed by `requirements.lock` retain their package metadata and license files.

| Component | Version | License | Source |
|---|---:|---|---|
| Flask | 3.1.3 | BSD-3-Clause | <https://github.com/pallets/flask> |
| Werkzeug | 3.1.8 | BSD-3-Clause | <https://github.com/pallets/werkzeug> |
| Jinja2 | 3.1.6 | BSD-3-Clause | <https://github.com/pallets/jinja> |
| Click | 8.4.2 | BSD-3-Clause | <https://github.com/pallets/click> |
| itsdangerous | 2.2.0 | BSD-3-Clause | <https://github.com/pallets/itsdangerous> |
| Blinker | 1.9.0 | MIT | <https://github.com/pallets-eco/blinker> |
| MarkupSafe | 3.0.3 | BSD-3-Clause | <https://github.com/pallets/markupsafe> |
| setuptools | 83.0.0 | MIT | <https://github.com/pypa/setuptools> |

Dependency maintenance observations and accepted risk are documented in [`docs/dependencies.md`](docs/dependencies.md).

Future vendored components must be recorded here with their name, version, source URL, license, copyright notice, and the location of the complete license text. Runtime assets must not be loaded from a CDN.
