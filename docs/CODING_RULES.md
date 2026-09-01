# Sage — Coding Rules

These conventions apply to all code written for Sage. They are the working
rules for the project; when an existing file does not yet follow them, apply
the rules to new and edited code rather than rewriting unrelated code.

## 1. Keep Python files small and modular

Each Python file has a soft line budget of **500–1000 lines**. When a file
approaches the top of that range, split the cohesive concern out into its own
module (a new file or package) instead of letting one file grow. Prefer small,
single-purpose modules over long files: named components, clear responsibilities,
and clean import boundaries count for more than file count.

## 2. Use snake_case

Use `snake_case` for Python identifiers (functions, variables, methods) and for
module file names. Reserve `PascalCase` for classes. Follow PEP 8 naming where
it does not conflict, and stay consistent with the surrounding module.

## 3. No comments in code

Do not add comments to code. Intent must be readable from names, structure, and
the code itself. Where a design decision needs explanation, document it in the
docs (see rule 4) rather than as an inline comment. This applies to new comments;
pre-existing comments are left alone unless the surrounding code is edited.

## 4. Always update documentation

Any change that alters behavior, layouts/UI, API contracts, configuration, or
deployment must be reflected in the corresponding documentation in `docs/`
before the change is committed. Keep the docs index and per-feature docs in sync
with the actual implementation.

## 5. Always plan, and research when needed

Plan before implementing. For non-trivial or unfamiliar work, research first
(how an API behaves, what a dependency version changed, the shape of existing
code) so the plan is grounded. Write a plan for multi-step tasks; only start
editing once the approach is clear.