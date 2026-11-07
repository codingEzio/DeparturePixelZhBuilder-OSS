# Local source maintenance

Run from the builder repository with Python 3.12+, uv, and Git. `uv run
--frozen` installs the exact dependencies in `uv.lock`; its first run needs
network access. The font repository may be anywhere: pass its path explicitly.
No installed fonts, browser account, or personal service is required.

```sh
uv run --frozen python scripts/maintain.py --font-root /path/to/X-DeparturePixelZh
```

Stage the intended files first. This command audits the exact Git indexes of
both repositories, exports temporary source-only copies, then runs the existing
builder tests against the exported builder. It does not stage files, commit,
install fonts, synchronize consumers, contact a device, or publish. A failed
audit or test exits nonzero. Temporary source copies are removed on exit. This
uses the pinned dependency environment, not a fresh dependency install or a
full upstream font build.

## Review and export

`public-source.json` enumerates every selected source file, including the
license notices and manifest itself. A newly staged file requires an explicit
manifest review. Local evidence belongs in ignored `Local/`; do not add its
paths or contents to the manifest. The generic project AGENTS instructions in
both repositories are intentionally included.

```sh
uv run --frozen departurepixelzh-builder audit-public --root .
uv run --frozen departurepixelzh-builder audit-public --root . --index
uv run --frozen departurepixelzh-builder audit-public --root . --index --export /new/source-directory
uv run --frozen departurepixelzh-builder audit-public --root /path/to/X-DeparturePixelZh --index --export /new/font-source-directory
```

The first command checks working files, including non-ignored untracked files.
`--index` checks staged blobs, so an unstaged edit cannot hide staged private
content. Exports require an exact allowlist match, reject links, submodules and
unresolved merges, preserve executable modes, and read back file hashes. The
export directory must not exist; its parent must exist. `SOURCE-RECEIPT.json`
contains relative paths and SHA-256 values, without local checkout paths. A
source export contains no Git history and is a disposable candidate, not a
second development repository or release-approved font package.

The public-source audit recognizes a small set of private-path,
authenticated-URL, token, private-key and binary-metadata patterns. It does not
prove the absence of every secret. Review images, source provenance, licenses,
and every new public file. Existing commits, refs, authors, messages and blobs
need a separate read-only history review before publishing. These commands do
not rewrite or sanitize history.

The selected source includes
`assets/departurepixelzh-social-card.png`. On 2026-10-29, the owner confirmed it
is owner-owned, approved public redistribution and selected CC BY 4.0 for this
card, to the extent of the rights held. See `docs/ARTWORK-LICENSE.md`. This
card grant is separate from the SVG grant. Review dependency terms,
third-party material, trademarks and the relevant history before any new
distribution. An audit or source export does not clear them.

The pinned Python Brotli dependency is 1.2.1 (MIT), acquired through the exact
locked PyPI artifact hashes. Version 1.2.0 is in the affected range for
[CVE-2026-111497](https://github.com/advisories/GHSA-9v8h-qqgm-4m35). WOFF2
verification opens compressed font data; a hash in a supplied build's own
provenance does not independently authenticate that build. Use trusted inputs.
Changing this builder's lock changes its source revision and requires a
separately reviewed recipe pin and rebuild before claiming a release build.

## Rebuild after a maintenance change

Commit the reviewed builder changes locally, then update both the font recipe's
`builder.revision` and `builder-version` to that exact commit. Preserve upstream
source hashes, font identity, geometry and component license notices.

From the exact clean pinned builder checkout:

```sh
uv run --frozen departurepixelzh-builder build --recipe /path/to/X-DeparturePixelZh/recipe.json --output /new/font-build
uv run --frozen departurepixelzh-builder check --recipe /path/to/X-DeparturePixelZh/recipe.json --output /new/font-build --release
```

A fresh output downloads the recipe's pinned upstream assets and validates
their hashes. For clean dependency evidence, also use a new uv cache and
project virtual environment. Keep build output outside the source payload.
`package` remains the font archive entry; it runs release checks and includes
all notices. Release checks reject common local source locations, URL credentials,
query strings, and common private-path patterns because original locations are
included in provenance. These checks cannot prove that a host is public or detect
every secret: review every source URL and release document before
distribution. Do not use `--development` to claim a release-ready build.
Publication and installation remain separate actions.
