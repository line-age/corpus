---
name: write-pr-description
description: >-
  Writes a GitHub pull-request description for the current branch by filling
  in the repo's PR template (.github/pull_request_template.md) from the
  branch's commits and diff against the default branch (main). Use when the
  user asks to "write a PR description", "describe this branch", "prepare a
  PR", "write up these changes for review", or similar. Produces one Markdown
  block ready to paste into GitHub; it does not open the PR.
---

# Write PR Description

Writes a pull-request description for the current branch by inspecting its
commits and diff against the default branch, then filling in
`.github/pull_request_template.md`.

The template owns the structure: its headings, their order and the hint under
each. This skill owns the procedure and the rules for filling each section.
Read the template at run time instead of keeping a copy of its sections here,
so the two can't drift.

The skill only produces text. It doesn't open the PR: `gh` isn't installed, so
don't try to create the PR with it. The user pastes the result into GitHub.

## Steps

1. **Resolve the base branch.** Run
   `git symbolic-ref --short refs/remotes/origin/HEAD`. It prints the default
   branch as a remote-tracking ref (`origin/main` in this repo); use that as
   `<base>`. If it errors, origin/HEAD isn't set in this clone: use
   `origin/main` when `git rev-parse --verify --quiet origin/main` prints a
   hash, otherwise local `main`. If the user names a different base (for
   example, a PR stacked on another branch), use theirs. Prefer the `origin/`
   ref to local `main`: GitHub compares the PR against the remote branch, and
   local `main` may be behind it.

2. **Gather the change set.** Run these together:
   - `git log <base>..HEAD --oneline`: commits on the branch
   - `git diff <base>...HEAD --stat`: files touched, with line counts
   - `git status --short`: uncommitted and untracked files, for step 6

   If `git log` prints nothing, the branch has no commits ahead of `<base>`
   (or you're on `<base>` itself). Tell the user, and offer to describe the
   uncommitted working-tree changes instead; don't write a description of an
   empty branch. If they accept, use the working tree in place of the branch
   diff for the remaining steps:
   - `git diff HEAD --stat` and `git diff HEAD -- <path>`: tracked files with
     changes, staged or not
   - `git ls-files --others --exclude-standard`: untracked files, one per
     line; read them directly, since they have no diff

   In that mode the working tree is the change, so skip step 6, and remind
   the user at the end that the changes must be committed and pushed before
   the PR can be opened.

3. **Read the meaningful diffs.** Run `git diff <base>...HEAD -- <path>` on
   the substantive files. Skim, don't list:
   - `uv.lock`: summarize as "dependency resolution updates"; don't enumerate
     packages from it.
   - Dependency changes in `pyproject.toml` (`[project]` `dependencies` and
     `[dependency-groups]`): name each added, removed or re-pinned package and
     give a one-line reason (e.g. "bump `fastapi` to patch a known
     vulnerability"). Take the reason from the commits or the code that uses
     the package; if neither shows it, don't guess: leave `TODO: reason` for
     the developer.
   - Generated files: say that they changed; don't describe them line by line.

4. **Call out changes that affect running the service.** Whoever deploys
   needs these, even when they're small. List them with:

   ```
   git diff <base>...HEAD --name-status -- migrations/versions/ .env.example Dockerfile compose.yaml Makefile
   ```

   (`A` added, `M` modified, `D` deleted, `R` renamed, with a similarity score
   such as `R100`. In working-tree mode, use `git diff HEAD --name-status -- ...`
   plus the untracked list; there, an uncommitted rename shows as a `D` plus an
   untracked file.) Cover each one:
   - **Alembic migrations** in `migrations/versions/`: name the revision (its
     `revision` id and file) and what it does, e.g. "`0002`
     (`2026_11_20_0002_add_documents.py`): creates the `documents` table".
     Deployers apply it with `make db-migrate`.
   - **`.env.example`**: every new, renamed or removed variable by name, and
     what it configures, because whoever deploys must set it.
   - **`Dockerfile` and `compose.yaml`**: what changes for building or running
     the stack (base image, build stage, service, port, volume, healthcheck).
   - **`Makefile`**: new, renamed or removed targets, and existing targets
     whose behavior changed.

5. **Group changes by theme, not by commit.** Cluster related changes into
   labelled groups (e.g. new feature, rename or refactor, safety guard,
   running the service, tests, dependencies). One commit can split across
   groups, and several commits can merge into one. Give the step 4 items their
   own group so they're easy to find.

6. **Check the working tree.** If `git status --short` from step 2 listed
   anything, those files aren't part of the branch. Note that under Notes for
   Reviewers (name them, or summarize if there are many), unless the user says
   otherwise.

7. **Fill the template.** Read `.github/pull_request_template.md` and fill its
   sections in order, keeping its headings exactly as written. If the file is
   missing, use these four headings in this order: `## Summary`,
   `## What Changed?`, `## Testing`, `## Notes for Reviewers`. If the template
   has a section the rules below don't cover, fill it from that section's
   HTML-comment hint. Rules per section:
   - **Summary**: 2-4 plain sentences on what the branch does and why, for a
     reader who hasn't seen the diff. Take the why from the commits, the user
     or the code; don't invent motivation. Fill in the `Closes` line when an
     issue number is known (from the user, the branch name or a commit
     message); otherwise remove the line and ask about it at the end.
   - **What Changed?**: the groups from step 5, each a bold label followed by
     bullets. One change per bullet, naming files, classes and functions in
     backticks.
   - **Testing**: only what was actually run in this session, with its result
     (e.g. "`make test`: 31 passed"). Never claim tests passed without running
     them; a new test file in the diff is not evidence that it passes. If
     nothing was run, give the command that verifies the change (usually
     `make test`, or `make test ARGS="tests/test_health.py"` for a subset) as a
     step for the reviewer, or leave a `TODO:` line for the developer.
   - **Notes for Reviewers**: this section belongs to the developer. Pre-fill
     only factual items:
     - inconsistencies that survive the branch, such as an old class name
       left in place
     - the working-tree files from step 6
     - follow-ups the diff makes visible, such as an added `TODO` or `FIXME`
       or a skipped test. `git diff <base>...HEAD | grep -E '^\+.*(TODO|FIXME)'`
       finds added markers; no output means there are none.

     Leave everything else (trade-offs, where to look closely) for the
     developer. If there's nothing to pre-fill, keep the heading with an empty
     body.

8. **Write the output.** Strip the template's HTML comments (`<!-- ... -->`).
   Emit the description as a single raw Markdown code block fenced with
   ```` ```markdown ````, with nothing else inside the fence, so it pastes into
   GitHub cleanly. If the description itself contains a fenced code block, use
   a four-backtick outer fence. Write it to a file only if the user asks.

9. **Close.** Ask whether the user wants tone or length adjustments. If you
   removed the `Closes` line, ask whether there's an issue to link. If Testing
   has no results, offer to run `make test`.

## Conventions

- Keep it simple: favor scannable bullets over prose walls. Summary is the
  only prose section.
- Reference code with backticked file, class and function names; don't paste
  large diffs.
- Describe only what the commits and diff show. Mark anything you can't
  determine with `TODO:` instead of guessing. Write placeholders as plain
  text, not in angle brackets, which GitHub hides as HTML.
- Follow the org writing style: no em-dashes or en-dashes as separators. Use
  a spaced hyphen, a semicolon, a colon or a new sentence.
