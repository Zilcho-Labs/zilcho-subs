# Versioning

The app version lives in **one place**: `zilcho_subs/__init__.py`. The footer,
the API, the Python package and the test-build names all read it from there.

Raise it in the same pull request as the change:

| Change | Example | Bump |
|---|---|---|
| Bug fix, wording, small tweak | 0.2.0 → 0.2.1 | last number |
| New feature or visible behavior change | 0.2.1 → 0.3.0 | middle number, last back to 0 |
| 1.0 and anything after it | 0.x → 1.0.0 | **owner decides** |

Notes-only or test-only changes don't need a bump. Add a line for every bump to
`CHANGELOG.md` in plain words.

Test builds are named `<version>-rc.<GitHub run number>`, for example
`0.2.0-rc.44001789863`. Two test builds of the same version are the same
features; the run number tells them apart.
