# misc-skills

A personal collection of small, self-contained skills for coding agents (pi, Claude Code, etc.).

## What is a skill?

A skill is a directory containing a `SKILL.md` — a short instruction manual an AI agent loads when a task matches its description. Skills may include helper scripts alongside the markdown.

## Structure

```
skills/
├── _template/        # Copy this to create a new skill
│   └── SKILL.md
└── <skill-name>/     # One directory per skill (kebab-case)
    ├── SKILL.md      # Required entrypoint (YAML frontmatter + instructions)
    └── scripts/      # Optional helper scripts
```

Every `SKILL.md` starts with:

```yaml
---
name: skill-name
description: One-line description of when to use this skill.
---
```

## Usage

Copy a skill directory into your agent's skill location, e.g. for pi:

```bash
cp -r skills/<skill-name> ~/.pi/agent/skills/
```

Or symlink it to pick up updates automatically:

```bash
ln -s "$(pwd)/skills/<skill-name>" ~/.pi/agent/skills/<skill-name>
```

## Creating a new skill

1. `cp -r skills/_template skills/<skill-name>`
2. Edit `skills/<skill-name>/SKILL.md` (frontmatter + instructions).
3. Commit: `feat(<skill-name>): add <skill-name> skill`

## Conventions

*   All content (docs, scripts, comments, commits) is in **English**.
*   Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <summary>`.
*   Scripts are small and stdlib-first; any extra dependency must be documented in the skill.
*   No secrets, no machine-specific absolute paths.

## License

[MIT](LICENSE)
