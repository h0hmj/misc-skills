# PROJECT KNOWLEDGE BASE

**Generated:** 2026-09-02

## OVERVIEW
Project: **misc-skills**
A personal collection of small, self-contained skills (agent instructions) for coding agents such as pi / Claude Code. Each skill lives in its own directory with a `SKILL.md` entrypoint.

Stack: Markdown (skill definitions), optional Python / Bash helper scripts. No build system. MIT licensed.

## STRUCTURE
```
misc-skills/
├── AGENTS.md        # This file — context for AI agents
├── README.md        # Human-facing docs (English)
├── LICENSE          # MIT
└── skills/
    └── _template/   # Copy this when creating a new skill
        └── SKILL.md
```
*   `skills/<skill-name>/`: One directory per skill, named in `kebab-case`.
*   `skills/<skill-name>/SKILL.md`: Required entrypoint with YAML frontmatter (`name`, `description`).
*   `skills/<skill-name>/scripts/`: Optional helper scripts, kept next to the skill that uses them.

## COMMANDS
No install/build/test pipeline. Useful checks:
| Action | Command |
|--------|---------|
| Validate skill frontmatter | `grep -l '^---' skills/*/SKILL.md` (each must have YAML frontmatter) |
| List skills | `ls skills/` |
| Commit | `git commit` (format below) |

## CODING STANDARDS
*   **Language**: All docs, scripts, comments, and commit messages in **English**.
*   **Skill format**: Each `SKILL.md` starts with YAML frontmatter:
    ```yaml
    ---
    name: skill-name
    description: One-line description of when to use this skill.
    ---
    ```
*   **Naming**: Skill directories in `kebab-case`; scripts in `snake_case.py` or `kebab-case.sh`.
*   **Scripts**: Keep them small and dependency-light. Python scripts should be stdlib-only unless a dependency is unavoidable (then document it in the skill).
*   **Docs**: Relative paths inside a skill resolve against the skill's own directory.

## COMMIT MESSAGES
Follow **Conventional Commits** strictly:
```
<type>(<scope>): <short summary in imperative mood, lowercase, no period>
```
*   Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `style`
*   Scope: the skill name (`skills/<scope>/`) or repo-level area (`repo`, `docs`)
*   Examples:
    *   `feat(commit-review): add PR description checklist`
    *   `docs: expand README usage section`
    *   `chore(repo): update .gitignore`
*   Body (optional): explain *why*, wrap at ~72 chars. Reference issues as `#123`.

## WHERE TO LOOK
*   **Skills**: `skills/`
*   **Template for new skills**: `skills/_template/SKILL.md`
*   **Docs**: `README.md`

## NOTES
*   This repo is intentionally minimal — no CI, no package manager, no lockfile.
*   A skill is "published" simply by being committed here; keep each skill self-contained so it can be copied into any agent's skill directory as-is.
*   Never commit secrets, API keys, or machine-specific absolute paths in skills.
