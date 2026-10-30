# Building documentation

Pourpoint owns `mkdocs.yml` and the shared assembly script. Each repository's
`README.md` supplies its website overview. Detailed pages come from `docs/site/`;
README images are copied from `assets/` only when referenced. Documentation links
are adapted for the combined site; repository-only links point back to GitHub.
Verification reports, planning and receipts are not published. HFX content is not
maintained as a second copy here.

Install the locked documentation tools with `uv sync --frozen --group docs`.
Provide an HFX checkout at the exact commit in `docs/hfx-revision`, then:

```bash
uv run --frozen --group docs python scripts/build_docs.py --hfx-source /path/to/hfx
```

The command rejects a different HFX HEAD, builds with strict link/reference
validation, and writes `.docs-build/site/source-revisions.json` with both exact
source commits. Use clean checkouts for reproducible release builds. The output
is disposable; never commit `.docs-build/`.

When reviewing proposed HFX changes, use the same command with `--proposed-hfx`.
This explicitly builds the supplied HFX checkout rather than the pin and does not
change `docs/hfx-revision`. Advance that file only to an approved exact HFX commit
when selecting content for a subsequent Pourpoint release.

## CI and repository access

Pourpoint pushes to `main` and pull requests check the combined site with HFX
`main`. HFX documentation, README and asset changes check their source revision
with Pourpoint `main`. Both use `--proposed-hfx`; neither publishes or changes the
release pin. Public companion repositories need no read keys.

`DOCS_SITE_WRITE_KEY` in Pourpoint is an SSH deploy key with write access only to
`pourpoint-tools/pourpoint-tools.github.io`. The generated repository must have an
initialized `main` branch. No secrets belong in sources or generated output.

## Release publication

Publishing a Pourpoint GitHub release runs `.github/workflows/publish-pypi.yml`:

1. Build the release tag's distributions with `uv build`.
2. Publish them to PyPI using trusted publishing in the `pypi` environment.
3. Only after PyPI succeeds, build the release tag's documentation with the exact
   HFX revision in `docs/hfx-revision`, retain an Actions artifact, and update the
   generated repository's `main` branch.
4. Update `gh-pages` only when the Pourpoint repository variable
   `DOCS_PUBLICATION_ENABLED` is exactly `true`.

Before the first release, configure a PyPI trusted publisher for project
`pourpoint`, GitHub owner `pourpoint-tools`, repository `pourpoint`, workflow
`publish-pypi.yml`, and environment `pypi`. Create that GitHub environment and
configure any required approval rules. The workflow does not configure PyPI or
fall back to an API token. A failed PyPI publication prevents documentation
publication. HFX has no separate publishing lifecycle.

For public documentation, configure the generated repository's Pages source to
branch `gh-pages`, root directory, and enable `DOCS_PUBLICATION_ENABLED`. The next
published release creates or updates that branch. `.nojekyll` keeps Pages from
interpreting the generated site as Jekyll source. The address is
https://pourpoint-tools.github.io/ with no custom domain.

The publication gate defaults to off. Normal pushes and checks cannot update
`gh-pages`. Do not publish a release or change repository settings just to test
builds. Local publication guard tests exercise this boundary without pushing
output or contacting Pages.
