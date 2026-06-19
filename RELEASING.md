# Releasing

commit-gate follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and keeps a [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) `CHANGELOG.md`.

- **MAJOR** — incompatible changes (e.g. config keys removed/renamed, hook
  behavior that breaks existing setups).
- **MINOR** — backwards-compatible features (new config options, new hooks).
- **PATCH** — backwards-compatible bug fixes.

Tags are the version prefixed with `v` (e.g. `v1.2.0`).

## During development

Add a bullet under the `## [Unreleased]` heading in `CHANGELOG.md` as you make
each notable change, under the right [Keep a Changelog] section:
`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security` (in that order).

## Cutting a release `vX.Y.Z`

1. **Pick the version** per SemVer based on what's in `[Unreleased]`.
2. **Promote the changelog:**
   - Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` (today's date, ISO 8601).
   - Add a fresh empty `## [Unreleased]` above it.
   - Update the link-reference footer:
     ```
     [Unreleased]: https://github.com/GhostlyGawd/commit-gate/compare/vX.Y.Z...HEAD
     [X.Y.Z]: https://github.com/GhostlyGawd/commit-gate/releases/tag/vX.Y.Z
     [<prev>]: https://github.com/GhostlyGawd/commit-gate/releases/tag/v<prev>
     ```
3. **Bump the plugin version** — set `"version": "X.Y.Z"` in
   `.claude-plugin/plugin.json` (this is what triggers `autoUpdate` for teams).
4. **Commit** both files together:
   ```
   git add CHANGELOG.md .claude-plugin/plugin.json
   git commit -m "release: vX.Y.Z"
   ```
5. **Tag** the release commit (annotated) and **push**:
   ```
   git tag -a vX.Y.Z -m "commit-gate vX.Y.Z"
   git push origin main
   git push origin vX.Y.Z
   ```
6. **Publish the GitHub release**, with notes taken straight from the changelog
   section so they never drift:
   ```
   python - CHANGELOG.md /tmp/notes.md <<'PY'
   import re, sys
   t = open(sys.argv[1], encoding="utf-8").read()
   m = re.search(r"## \[X\.Y\.Z\][^\n]*\n(.*?)\n\[", t, re.S)   # set X.Y.Z
   open(sys.argv[2], "w", encoding="utf-8").write(m.group(1).strip() + "\n")
   PY
   gh release create vX.Y.Z --repo GhostlyGawd/commit-gate --title "vX.Y.Z" --notes-file /tmp/notes.md
   ```

## Verify

```
gh release view vX.Y.Z --repo GhostlyGawd/commit-gate --json tagName,name,isDraft,isPrerelease,url
```

Confirm `isDraft: false`, the tag matches, and (for stable releases)
`isPrerelease: false`.
