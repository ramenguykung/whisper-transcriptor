# Repository instructions

Build only the agreed local transcription app. Keep UI strings short and preserve transcript Unicode and punctuation.

## Version control

- Group commits by module, then feature, then modification type. Keep each commit coherent and include required callers and tests.
- Use Conventional Commits: `<type>(<module>): <change>`.
- Stage explicit paths or selected hunks and inspect the staged diff before committing.
- Run relevant checks before each commit. Always use `git commit -S`, including `git commit --amend -S` for amendments.
- Verify every commit with `git verify-commit HEAD`. Never fall back to unsigned commits or bypass hooks.
- Sign merge commits and rebased commits. Do not rewrite published history without user authorization.
- Require signed commits on protected branches when a remote is configured.
- Never commit private keys, local media, model files, or environment secrets.

## Architecture

The API owns SQLite. The worker accesses jobs through the internal version 1 HTTP protocol and uses a shared media volume. Heavy imports and inference run in a child process. The UI talks only to `/api` through one origin.

Follow CONTRACT.md for interfaces. Keep CPU inference available when GPU inference fails. Preserve unsaved editor drafts on conflicts and network errors.
