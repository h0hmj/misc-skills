---
name: codex-third-party-provider
description: Configure Codex third-party or custom model providers in ~/.codex while preserving official ChatGPT login. Use when the user asks to add a Codex custom provider, set base_url and API key, edit config.toml experimental_bearer_token, keep auth.json intact, list vendor models, or add/remove catalog models via models.dev.
---

# Configure Codex third-party providers

Write a custom provider into Codex live config without replacing official ChatGPT login. Prefer the bundled CLI (Python 3.10+, stdlib only).

## When to Use

- The user wants a Codex third-party / custom provider (`base_url` + API key).
- They want to keep official ChatGPT login (`auth.json`) while routing models through that provider.
- They ask to list vendor models, add/remove catalog entries, or point Codex at `codex-third-party-provider-catalog.json`.

Do not use to log the user out of ChatGPT or edit `models_cache.json`.

## Safety

Follow these rules even if doing a manual edit:

- Never create, rewrite, or delete `auth.json`. Official login stays as-is.
- Never write `models_cache.json` (Codex official cache; it will be overwritten).
- Before writing `config.toml`, copy it to `config.toml.bak.<YYYYMMDDHHMMSS>`.
- Redact API keys in all output (`sk-...xxxx` / first 3 + last 4 chars).
- After a real write, tell the user to **restart Codex** so `/model` refreshes. Skip that prompt on dry-run.

## File map

Codex home is `$CODEX_HOME` if set, otherwise `~/.codex`. Pass `--codex-dir` to override.

```
<codex-home>/
  auth.json                         # never touch
  config.toml                       # provider routing (merge only)
  models_cache.json                 # never touch
  codex-third-party-provider-catalog.json    # projected Codex catalog
  .codex-third-party-provider/
    manifest.json                   # CLI source of truth
    models-dev-cache.json           # models.dev cache (24h)
    codex-prompts-cache.json        # leaked Codex GPT prompts cache (fetch if missing)
```

## How It Works

Resolve this skill directory (the folder that contains this `SKILL.md`). Run the CLI with Python 3.10+:

```bash
python3 scripts/codex_third_party_provider.py <subcommand>
```

From a repo root that contains this skill:

```bash
python3 skills/codex-third-party-provider/scripts/codex_third_party_provider.py <subcommand>
```

No extra packages. For `setup` in a non-TTY agent session, always pass `--base-url` and `--api-key` (or set `OPENAI_API_KEY`). Interactive prompts only work on a TTY.

### Commands

| Command | Description |
|---|---|
| `setup` | Write provider `base_url` + API key into `config.toml` and `manifest.json`. Never touches `auth.json`. |
| `list` | Fetch vendor models via OpenAI-compatible `GET /v1/models`. |
| `add MODEL ...` | Add one or more models to the catalog. Named duplicates without `--context-window` error. |
| `add --all` | Add every id from vendor `GET /v1/models`. Already-cataloged ids are skipped. |
| `add --context-window` | Override context window; re-run on existing slugs to refresh metadata and catalog. |
| `add --dry-run` | Preview unified diff for `manifest.json`, catalog, and `config.toml`. No writes (including caches). API keys are redacted. |
| `rm MODEL ...` | Remove one or more models. Missing refs error and write nothing. |
| `rm --all` | Remove every catalog model. Errors if the catalog is empty. |
| `catalog` | Show models already added. |

### Workflow

1. **`setup`** — write provider credentials into `config.toml` + `manifest.json`. Never touches `auth.json`.
   ```bash
   python3 scripts/codex_third_party_provider.py setup \
     --base-url https://api.example.com \
     --api-key sk-...
   ```
2. **`list`** — OpenAI-compatible `GET /v1/models` (tries `/v1/models` and `/models`).
3. **`add --dry-run`** — unified diff of `manifest.json`, catalog, and `config.toml`. No writes (including caches). Keys are redacted. Accepts `MODEL ...` or `--all`.
4. **`add`** — persist models (`MODEL ...` or `--all`). Optional `--context-window TOKENS` (re-run on existing slugs to refresh metadata and catalog).
5. Tell the user to restart Codex, then use `/model`.

`rm` accepts `MODEL ...` or `--all`.

After `setup`, later commands read `base_url` / API key from the manifest unless flags or `OPENAI_API_KEY` override them.

### What `setup` / `add` write into `config.toml`

Merge these fields; keep unrelated keys. `model_catalog_json` is set only when the catalog is non-empty.

```toml
model_provider = "custom"
model_catalog_json = "codex-third-party-provider-catalog.json"

[model_providers.custom]
name = "third-party"
base_url = "<user url>"
wire_api = "responses"
experimental_bearer_token = "<api-key>"
requires_openai_auth = true
```

### Naming and models.dev

For add input `A/B` (example: `copilot/gpt-5.6-luna`):

- Catalog `slug`, `display_name`, and `description` stay `A/B` **verbatim**.
- models.dev lookup uses only `B` (the part after the first `/`).
- When multiple models.dev providers match, prefer official metadata: **GLM** family → `zai`, **GPT** family → `openai`.
- Do **not** map user-facing provider aliases (`copilot` ↛ `openai`, etc.); the preference applies only to models.dev lookup.
- Prefix strip, `:suffix` drop, `@` → `-`, and lowercase are **lookup-only**, not catalog identity.
- Context window comes from models.dev `limit.context`, else family defaults (`272000` for **GPT**, `400000` otherwise), unless `--context-window` is set.
- Reasoning levels come from models.dev `reasoning_options`; if none, keep the native template defaults.
- **GPT** family catalog entries copy `instructions_template` from [system_prompts_leaks/OpenAI/Codex](https://github.com/asgeirtj/system_prompts_leaks/tree/main/OpenAI/Codex) (`gpt-*.md`, fetched once into `.codex-third-party-provider/codex-prompts-cache.json` when that file is missing). Exact slug match first, else same `gpt-X.Y` family file (e.g. `gpt-5.6-sol` → `gpt-5.6.md`). Delete the cache file to refresh. Non-GPT models keep bundled `base_instructions`.

### Metadata sources

| Field | Source |
|---|---|
| Context window | models.dev `limit.context`, else `272000` (GPT) / `400000` (other), unless `--context-window` is set |
| Reasoning levels | models.dev `reasoning_options` (GLM → `zai`, GPT → `openai` provider preference) |
| GPT `instructions_template` | [system_prompts_leaks/OpenAI/Codex](https://github.com/asgeirtj/system_prompts_leaks/tree/main/OpenAI/Codex) `gpt-*.md`, fetched once into cache if missing |
| Non-GPT instructions | Inline `TEMPLATE` dict in the script (`base_instructions`) |

## Manual fallback

Reproduce the CLI merge; do not invent extra provider fields.

1. Honor the Safety rules above.
2. Merge the `config.toml` snippet; keep comments and unrelated tables.
3. Maintain `.codex-third-party-provider/manifest.json` as SSOT (`baseUrl`, `apiKey`, `models[]` with `upstreamId`, `slug`, `displayName`, `contextWindow`, optional `reasoningLevels`).
4. Build `codex-third-party-provider-catalog.json` by deep-copying the `TEMPLATE` dict in `scripts/codex_third_party_provider.py` per model. Set `slug` / `display_name` / `description` to the add input, `context_window` / `max_context_window`, and `priority` = `1000 + index`. GPT entries set `model_messages.instructions_template` from the GitHub Codex prompt repo and drop `base_instructions`.
5. If removing the last model, delete the catalog file and remove `model_catalog_json`.

## Files

*   `scripts/codex_third_party_provider.py` — CLI entry (`python3 scripts/codex_third_party_provider.py`).

## Notes

- Official login plus `requires_openai_auth = true` is what lets Codex Desktop show custom models; the third-party key still goes out as `experimental_bearer_token`.
- `rm` of the current `model` does not pick a replacement; the user must set one if needed.
- Duplicate named `add` without `--context-window` raises. `add --all` skips ids already in the catalog.
- Requires Python 3.10+ (stdlib only).
