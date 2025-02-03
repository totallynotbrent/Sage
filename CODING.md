# Sage Coding Conventions

These rules apply to all code written for this project. They keep the codebase
modular, readable, and honest. Treat them as hard requirements unless a change
is explicitly approved as an exception. Mirrors `bread/CODING.md` — same
conventions in both repos.

1. **Modularity & file size.** Each Python file is soft-limited to **500–1000
   lines**. When a module approaches the ceiling, split it — one responsibility
   per file, small focused functions, thin wiring at the edges. Existing files
   already over the limit should be carved up opportunistically, not all at once.

2. **snake_case.** Use `snake_case` for all module names, function names, method
   names, variables, and file names. Class/type names use `PascalCase`. No
   camelCase for variables or functions. No `PascalCase` file names.

3. **No comments in code.** Do not add inline comments, docstring-as-comment
   noise, or decorative `#` banners. Prefer clear, self-documenting names and
   small functions. Module-level docstrings that explain *why* something exists
   are allowed and encouraged; line-level comments that restate *what* the code
   does are not. Delete stale comments when touching nearby code.

4. **Always update documentation.** Any change that alters behavior, adds a
   setting, changes a schema, or adds a tool must update the relevant docs in
   the same commit: this file, any `README`/`docs/`, and the settings
   (`app/config.py`) surface. Unchanged code ships with unchanged docs;
   changed code ships with changed docs.

5. **Always plan and research first.** Before implementing, write a short plan
   (goal, approach, files touched, tests, verification) — in the PR/issue or a
   plan file — and research any unfamiliar dependency or API. No code before
   the approach is decided. Verify with the unit suite and the deploy loop
   before declaring done.

6. **LF line endings everywhere.** All text files use LF (`\n`) only. Enforced
   by `.gitattributes` (`* text=auto eol=lf` + explicit `*.py text eol=lf` etc.,
   binaries marked `binary`). Do not commit CRLF. If `git diff` shows a huge
   churn, check `file <path>` / `git ls-files --eol` first and renormalize
   (`git add --renormalize .`) instead of committing CRLF.

## Process

- Run `python -m pytest tests -q` — all green before pushing.
- Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`) on a
  feature branch, pushed to GitHub at good checkpoints.
- Push a checkpoint before risky exploratory work so you can pull back to a
  known-good state if it goes sideways.
