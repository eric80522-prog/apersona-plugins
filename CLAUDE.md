# Working rules

Rules for every agent and every contributor in this repo. `AGENTS.md` is a symlink to this file.

## What belongs in this file
- Only rules that stay true across PRs. No codebase facts: no file lists, commands, counts or dates; the code, the
  manifests and the checks that enforce them are their source of truth.
- No personal preferences: they go in your own gitignored `CLAUDE.local.md`.

## What this repo is
- Users install the plugin and the connector from this repo's main. Merging into main is a release: it reaches every
  user on their next update.
- The plugin talks to the Apersona service, whose server lives in a private backend repo. That repo mounts this one as
  a submodule and runs the end-to-end plugin tests against it.

## Working
- The user's explicit instruction in the session wins.
- A change the backend also has to make is paired work: one branch name in both repos and a pull request in each. The
  backend PR merges first; this one merges after its own checks and the paired backend PR are green.
- A plugin manager updates by version: a change to what users install raises the version in the manifest of each
  package it changes, in the same PR; CI checks it.
- Everything here is public. Nothing may name the private repo, its paths or its people; a test checks the plugin
  folder for it.
- The scripts run on the user's own machine and Python, the oldest supported version included, with the standard
  library only. Never add a dependency.

## Tests
- Tests here need nothing but this repo; a test that needs the server belongs in the backend.
- A test earns its CI time: a regression for a bug, a behavior users see, or a red line (credentials, what leaves the
  machine). Do not pin text or constants no user sees.
- A check that did not run has failed. Never disable or bypass a guard that blocks you; fix what it reports.

## Changes and commits
- Each commit is one reviewable result; delete what the change made unused in the same commit.
- Title `type(scope): result`, types `feat|fix|refactor|chore|docs|test`; reasons go in the body.
- Never force-push. Never touch another person's branch or uncommitted changes.
- Report: what was done, what was verified, what was not. Last line is always `Deleted/merged: …` (or `none`).
