# Contributing

Contributions are welcome — bug fixes, target classes, artifact handling, docs.

bench-recon is source-available, and I sell commercial licences for use above the
[PolyForm Small Business](LICENSE) limits. That only works if the rights over
every line are clean, so two rules apply to every contribution.

## Sign off every commit (DCO)

Each commit must carry a [Developer Certificate of Origin](https://developercertificate.org/)
sign-off. It's your statement that you wrote the change, or otherwise have the
right to submit it. Add it with `-s`:

```sh
git commit -s -m "fix(store): describe the change"
```

That appends `Signed-off-by: Your Name <you@example.com>`, matching your git
identity. A pull request with an unsigned commit won't be merged — fix it with
`git commit --amend -s` or `git rebase --signoff main` and force-push.

## Licensing of contributions

By contributing, you license your contribution under the project's current
license, the [PolyForm Small Business License 1.0.0](LICENSE). You also agree
that it may be included in commercially licensed versions of bench-recon, and
that the maintainer may relicense it, along with the rest of the project, under
any license they choose, including an open-source license such as MIT or GPL.

## Before you start

Open an issue before anything large — a new subsystem, a dependency, a schema
change, anything that touches the benchhud projection. A quick note up front
saves a rewrite later. Small fixes can go straight to a pull request.

Never put real engagement data in an issue, a pull request or a test fixture:
no client or target names, scope text, findings, credentials or dumps. Use
synthetic placeholders.

Style is enforced by the tooling, not by this file. Before you push:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
```
