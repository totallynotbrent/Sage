# Git Model (shared across bread / sage / UmaViewer)

One convention for all three repos so work never collides. Read this before
creating a branch, and follow it even when a merge feels like it "should" be a
fast-forward.

## The mental model

Think in three separate trunks:

- **main (the hub)** — "settled" work, what you and GitHub's home view actually
  see. It only ever *receives* a finished branch; it does not get dragged
  forward by in-progress work.
- **feature / experiment branches (spokes)** — branch off `main` (or `master`
  for UmaViewer), do the work in isolation, then merge back to the default
  branch **when done**. They diverge from the default branch but not from each
  other, so they merge cleanly in any order.
- **prerelease branches (a stacking trunk)** — own line for release-candidate
  work. Every new prerelease / fix-on-prerelease branches off the *current*
  prerelease, never off `main` or an older prerelease. So the current
  prerelease is always an ancestor of the next one, and they can't overlap.

## Why not fast-forward everything?

Fast-forwarding `main` with every little fix *while other feature branches
still exist* is what causes "collision." A feature branched off an old `main`
goes stale the moment `main` moves, and merging it later is when it collides.
Fast-forward is only safe when there is exactly one line of work and nothing
else is in flight.

Rule of thumb: **do not fast-forward `main`/`master` as a habit.** Merge
finished branches instead. Fast-forward is the exception, not the default.

## The workflow

1. Branch the default branch:
   - bread / sage: `git checkout main && git pull && git checkout -b <topic>`
   - UmaViewer: `git checkout master && git pull && git checkout -b <topic>`
   - Topic names: `feature/<thing>`, `fix/<slug>`, `experiment/<thing>`,
     `prerelease/<thing>`.
2. Do the work in isolation. Commit with conventional messages
   (feat:/fix:/docs:/chore:).
3. When the branch is **done and verified** (tests green, live check ok):
   - `git checkout <default>` (always `main`, or `master` for UmaViewer)
   - `git merge --no-ff <topic>` — explicit merge commit, keeps main's history
     readable and never pretends the work was on main the whole time.
   - `git push origin <default>`
   - Delete the merged branch locally and on origin once it's in.
4. Keep `main`/`master` purely-ahead of every topic branch so the merge logic
   stays trivial.

## Same-session rule (user preference)

For **user-requested direct work** that is verified, merge to main/master the
same session — don't hold work on a branch unless the user explicitly says to
hold it. Verified means: test suite green AND live check passes. See
`CODING.md` rule set.

## Prereleases (UmaViewer and anything with pre-release exes)

- Stable/releases live on `master`.
- `prerelease/*` is its own trunk. New work that will become a prerelease
  branches off the **current** `prerelease/*`, not off `master`.
- When a prerelease is validated, merge it into `master` (`--no-ff`), tag with
  a semantic `vX.Y.Z`, and push the tag.
- Do not branch the next prerelease off an already-merged older one — branch
  off the newest active one so history stays linear-stackable.

## Failure-mode cheatsheet

- **"I fast-forwarded main, now my other feature won't merge."** That feature
  is behind; don't ff-merge it. Instead `git checkout <feature> && git merge
  <default>` to bring it up to date, then merge it with `--no-ff`.
- **"Prerelease overlaps old prerelease."** You branched off the wrong base.
  The new prerelease should have `git merge-base` equal to the current
  prerelease head; if it equals an older one, rebase onto the current
  prerelease.
- **"Merge would be huge churn / every line changes."** Check line endings
  first (`file --eol`). All three repos enforce LF; CRLF noise looks like a
  merge conflict but is not.