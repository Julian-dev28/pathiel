**What this changes**

**Why**

**Install path touched**

- [ ] public install (plugin, launcher, container)
- [ ] maintainer checkout
- [ ] neither (docs, tests, tooling)

**Checks**

- [ ] Tests for this change are in this pull request
- [ ] The maintainer path is unchanged, or the change is about it
- [ ] No performance claims, em dashes or banned words in user-facing text
- [ ] No key, secret or machine-bound path in the diff

**Gate**

<!-- Paste the last line of:
PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest -q --no-header -p no:cacheprovider -->
