# coding-pal

English | [简体中文](README.zh-CN.md)

A development workflow plugin for Claude Code and Codex. People who don't write code can get a project built just by talking, confirming and accepting results, while the agent follows **requirements → UI → plan → build → acceptance**. Small changes skip the full flow, and the mechanical steps are handled in seconds by a bundled tool called pal.

The skill and its messages are written in Chinese.

## What you need

- Claude Code or Codex.
- git: your project must be a git repository.
- Python 3.9 or later, for the bundled tool pal. On macOS it comes with Apple's Command Line Tools; if it's missing, the agent will ask you to install it.

## Install

coding-pal is published through the plugin catalog [pal-plugins](https://github.com/KeelingYU/pal-plugins); you don't need to download this repository.

Claude Code:

```bash
claude plugin marketplace add KeelingYU/pal-plugins && claude plugin install coding-pal@pal-plugins
```

Codex:

```bash
codex plugin marketplace add KeelingYU/pal-plugins && codex plugin add coding-pal@pal-plugins
```

Installing only downloads the plugin; it doesn't write anything into your projects.

## Get started in 5 minutes

1. Start a new session after installing (restart Claude Code) and open your project folder.
2. Say "use coding-pal to build …" and describe what you want in a sentence or two.
3. The first time, the agent creates `~/.coding-pal` on your computer (home of the overview board), sets up the project, and asks you two things: whether this project publishes versions, and whether to turn off agent memory (recommended).
4. The agent sizes the task and tells you why:
   - **Small change** (fits in a sentence or two, visible right away): it makes the change, shows you the result, and merges once you say it's fine.
   - **Requirement**: it asks a few questions (each with a recommended answer), restates the whole thing for you to confirm, then delivers a draft requirement. If there's a UI, you get a clickable demo page first. Next comes a one-page plan summary; it starts building only after you reply "continue". When done, an independent checker verifies every acceptance criterion and you get an acceptance page.
5. Say "accepted" and it merges automatically. For projects that publish versions, nothing is released until you say "release".

Any time later, say "continue" in the project and the agent lists what's waiting for you and what's in progress, then picks up where it left off. Each project's board is at `.pal/board/index.html`; open it in a browser.

## How it works

| Stage | The agent | You |
|---|---|---|
| Requirements | Asks 3–5 questions per round, restates, writes the requirement | Answer, confirm the requirement |
| UI (only with a UI) | Builds a clickable static demo without touching project code | Confirm look and interaction |
| Plan | Writes the implementation plan; high-risk plans get an independent review | Read the first page, reply "continue" |
| Build | Sends implementer subagents to follow the plan, checks each group | Nothing, unless a business decision comes up |
| Acceptance | Sends an independent checker, generates the acceptance page | Try the listed items, say "accepted" or give feedback |
| Merge | Merges into the main line automatically, one task at a time | Nothing |
| Release (only if the project publishes) | Picks the version, writes release notes, tags and pushes | Say "release" |

- Each task works in its own working copy, so several tasks can run at once without interfering.
- The agent only stops to wait for you on two kinds of problems: building the wrong thing, or causing damage that's hard to undo (such as deleting data or sending messages outside).
- Three ground rules: never lose unmerged work; never claim done without verifying; never push unless you said "release".
- Four long-lived documents stay in the project: `docs/PRODUCT.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` and `CHANGELOG.md`. Task files and the board stay on your computer only (`.pal/`, not committed).

## Updates

You only get published versions.

- Claude Code updates only when you ask: run `claude plugin marketplace update pal-plugins && claude plugin update coding-pal@pal-plugins`, then restart.
- Codex updates to the latest published version whenever you start a new session; to update right away, run `codex plugin marketplace upgrade pal-plugins`.

Version 1.0 is not compatible with the 0.x project file layout and has no migration tool: set up old projects again as new ones. See [CHANGELOG.md](CHANGELOG.md) for the old-to-new version table.

## Development

This repository is developed with coding-pal itself. Development rules, tests and release steps are in [AGENTS.md](AGENTS.md); structure and mechanisms in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (both in Chinese). `scripts/` and `tests/` are author tools and are not shipped with the plugin.

## License

[MIT](LICENSE)
