# v1.0 release checklist

From today (2026-11-09) to Kestrel v1.0 public, in order.
- **Who does what:** **[owner]** marks a step only the owner does by hand. Every other step can be run by Claude
  Code with the owner's OK.
- **Running commands:** from the repo root in PowerShell. While Claude Code runs `kestrel-mcp`, use
  `uv run --no-sync` (CLAUDE.md).
- **Groq:** the only Groq use left is the combined PR's two CI runs, inside the owner's 2026-11-09 approval
  (STATUS, ~120k; 51,696 used by #79).

## Done

- [x] Section 9's verdict: PASS by 9.6 (`evals/reports/phase4-section9-verdict.md`, PR #87).
- [x] The fix, PR #79, merged per 9.6: the router ships opt-in, `KESTREL_PROVIDERS=routed`, off by default.
- [x] Pre-public privacy audit of the whole history (2026-11-09).
  - **Clean:** gitleaks found no leaks, and no `.env`, `data/` or `logs/` was ever committed.
  - **The unredacted Phase 3 copy:** kept in history by the owner's decision, and disclosed in `evals/after-runs/phase3/README.md`.
  - **Usernames:** home paths are replaced with `<home>` in the current docs.

## 1. The combined PR (agent-affecting: CI evals, ~56k)

`post-s9/combined` holds `fix/fetch-private-block` + `fix/voice-console-week` + `phase4/integration`. On top of
those: rules `tiers-v2.2` (agent `8a8174d8f17d`) and both MCP servers in `.mcp.json`.

1. **Wait for quota.** It must print OK for 56,000:
   ```powershell
   uv run --no-sync python scripts/groq_quota.py --need 56000
   ```
2. **Bring in `main` and run every check** (CLAUDE.md, "Working rules"):
   ```powershell
   git switch post-s9/combined
   git merge origin/main
   uv run --no-sync ruff check . ; uv run --no-sync ruff format --check . ; uv run --no-sync mypy ; uv run --no-sync pytest
   cd console; npm run typecheck; npm run lint; npm run build; cd ..
   ```
3. **Push, open the PR, merge when green.** Push once: every push to an open PR re-runs the evals.
   ```powershell
   git push -u origin post-s9/combined
   gh pr create --base main --head post-s9/combined --title "Post-section-9: fetch block (tiers-v2.2), voice week, results kit, MCP servers"
   gh pr checks <PR> --watch
   gh pr merge <PR> --merge
   ```
4. **Record the two runs:** the PR run and the merge-to-`main` run, with their pass rates and billable tokens, in
   STATUS's approvals table (docs PR).
5. **Delete the merged branches,** after `git merge-base --is-ancestor <branch> origin/main` confirms each:
   ```powershell
   foreach ($b in 'fix/fetch-private-block','fix/voice-console-week','phase4/integration','post-s9/combined','phase4/tainted-to-teacher') { git push origin --delete $b; git branch -d $b }
   ```

## 2. README and release notes (`docs/readme-v1`, docs only)

1. **Bring in `main`:** `git switch docs/readme-v1; git merge origin/main`.
2. **Replace the draft headline** in `README.md` and `docs/release-notes-v1.0.md` with the owner's.
3. **Replace the two "_(pending)_" markers:** the combined PR's CI run, with its run ID and result.
4. **Release notes:** remove the "DRAFT, not tagged" banner.
5. **PR, check, merge:** confirm "KestrelBench not run" in the PR run's log, then merge.

## 3. Blog post 2 (`docs/blog-phase4`, docs only)

1. **Bring in `main`:** `git merge origin/main`.
2. **Final read:** change the header's "Draft" date line.
3. **PR, check, merge:** "KestrelBench not run", then merge.

## 4. STATUS and CLAUDE.md (docs PR to `main`)

- **Status:** Phase 4 done (gate passed by section 9); the "Next" line becomes Phase 5's launch items.
- **Owner decision D3:** re-enable the weekly KestrelBench cron now that the Phase 3 and 4 gates are passed? It
  spends ~200k a week if on. Not required for the release.

## 5. Tag and GitHub release

On `main`, after steps 1-4 are merged:
```powershell
git switch main; git pull
git tag -a v1.0.0 -m "Kestrel v1.0: tiers v2.2, section 9 PASS, opt-in router"
git push origin v1.0.0
gh release create v1.0.0 --title "Kestrel v1.0" --notes-file docs/release-notes-v1.0.md
```

## 6. Before the repo goes public

1. **Re-run the history audit on every ref.** It should print "no leaks found":
   ```powershell
   gitleaks git . --log-opts="--all" --redact
   ```
2. **No secrets in the tree:** `git ls-files .env logs data` must print nothing.
3. **GitHub settings, after it's public:**
   - turn on secret scanning and push protection;
   - set the workflows to "Require approval for all outside collaborators" (fork PRs).
   - **Already safe:** `ci.yml` uses `pull_request`, so a fork's PR gets no `GROQ_API_KEY`. Under D2 its eval job
     fails loudly rather than spending quota.

## 7. Going public (owner)

1. **[owner] Switch the repo to public:** GitHub, Settings, General, Danger Zone, "Change visibility", or:
   ```powershell
   gh repo edit Ecko-maker/kestrel --visibility public --accept-visibility-change-consequences
   ```
2. **[owner] Check it as a stranger would,** in a private window:
   - the README renders;
   - the CI badge shows;
   - the demo link, the release page and the links in Results open.
3. **[owner] Publish blog post 2** (`docs/blog/02-distillation.md`) wherever the blog lives, with links back to the
   repo. Post 1 is `docs/blog/01-safe-agent.md`.
4. **[owner] Edit the YouTube description:** paste the three-line update and the README link (draft from
   2026-11-09) into the demo video's description.
