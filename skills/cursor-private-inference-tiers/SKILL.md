---
name: cursor-private-inference-tiers
description: Patch (or restore) Cursor Private Inference.app so every local-provider model gets a reasoning-effort (thinking level) control and 4 local context-window tiers (256K/272K/400K/1M). Version-independent - locate the patch sites in the installed bundles, generate a one-off patch script, apply. Use after upgrading the app, when effort/context controls disappear, or when the user asks to patch/restore thinking levels or context windows.
---

# Cursor Private Inference — Universal Effort & Context Tiers Patch

Local modification of `Cursor Private Inference.app`'s agent runtime bundles so that
**every** model exposed by a local provider gets:

1. A **Reasoning (thinking level)** enum: `low / medium / high / xhigh / max`
   (param `reasoning_effort`, default `medium`), and
2. A **Context** enum with 4 locally-defined tiers: **256K / 272K / 400K / 1M**
   (decimal values `256000/272000/400000/1000000`, default 256K), independent of
   what the gateway's `/models` response advertises.

This is an unofficial patch of installed app resources, not a supported configuration.

This skill records only *what* to change and *how to find it*. The bundles are
minified and identifiers change between releases (and between the two bundles),
so never reuse literal strings from a previous run: re-locate every site against
the installed app, then generate a throwaway patch script for that exact build.

Latest app build (macOS Apple Silicon):
<https://api2.cursor.sh/updates/download/golden/darwin-arm64/cursor-local/latest>

## When to Use

- After upgrading `Cursor Private Inference.app`, when the Reasoning/Context
  controls vanish from the model picker.
- When a model outside the built-in family table (e.g. `kimi-k3`, `qwen3`,
  `deepseek-*`) shows no thinking-level control.
- When the user asks to check patch status, re-apply, or restore.

## Target Files

The same logic is bundled twice; patch both in lockstep:

- `Contents/Resources/app/extensions/cursor-agent-exec/dist/main.js`
- `Contents/Resources/app/extensions/cursor-local-agent-runtime/dist/main.js`

If either path disappears in a future release, find the bundles that contain the
anchors below: `rg -l 'name:"Context"' "$APP"/Contents/Resources/app/extensions/*/dist/*.js`.

## Patch Sites

Each site lists a stable anchor (string literals survive minification), the
shape of the original code, and the intended change. Code shapes are examples;
single-letter names (`E`, `t`, `u`, `A.UNk`, `M`, `J`, ...) differ per bundle and
per release — always copy the names from the located snippet.

### 1. Family lookup (read only, do not patch)

Anchor: `"string"==typeof e?e:e.id);for(const e of`

```js
function E(e){const t=x("string"==typeof e?e:e.id);for(const e of k)if(e.matches(t))return{id:e.id,effort:e.effort,...
```

Consults the named family table (`claude-*`, `grok-*`, `gpt-5.x-*`, `gemini-*`).
Hits keep their own param/levels (e.g. `output_config.effort` for Claude). Entries
without an `effort` field are deliberate no-effort exceptions (`claude-sonnet-4/4.5`,
`claude-haiku-4.5`, `grok-composer*`, `gemini-*flash-lite`). Record this function's
name per bundle: site 2 must call it.

### 2. Universal effort fallback

Anchor: `/^gpt-5` regex test inside the fallback resolver, right after an inline
id normalizer (lowercase, strip `provider/` prefix and `@suffix`).

```js
function v(e){const t=function(e){...normalize...}(e);if(/^gpt-5(?:\.\d+)?$/u.test(t))return w}
```

Replace the `if(...)return w` with:

```js
if(t&&void 0===<FAMILY_LOOKUP>(e))return{param:"reasoning_effort",values:["low","medium","high","xhigh","max"],defaultValue:"medium"}
```

The `void 0===<FAMILY_LOOKUP>(e)` guard is essential: callers fall through to
this resolver whenever a family entry has no `effort`, so without the guard the
no-effort exceptions would pick up the universal enum too. The "no effort" badge
function already checks both resolvers and needs no change.

### 3. Context param definition

Anchor: `contextLength:t,longContextThresholdTokens`

```js
function(e){const{contextLength:t,longContextThresholdTokens:r}=e;if(!(...))return{defaultTokens:r,longTokens:t}}(e)
```

Replace the whole IIFE with an unconditional tier object (keep `defaultTokens`
equal to the first tier so the default-variant logic still works):

```js
function(e){return{defaultTokens:256e3,tiers:[256e3,272e3,400e3,1e6]}}(e)
```

### 4. Context enum in the picker

Anchor: `name:"Context"`

```js
...values:[{value:M(i.defaultTokens),displayName:J(i.defaultTokens)},{value:M(i.longTokens),displayName:J(i.longTokens),increasesModelCost:!0}]...
```

Generate the values from the tier list, keeping the local value/label helpers:

```js
values:i.tiers.map(e=>({value:M(e),displayName:J(e),increasesModelCost:e>i.defaultTokens}))
```

Optionally reword the tooltip ("The larger window is billed..." →
"Larger windows may be billed...").

### 5. Variant generation (effort × context)

Anchor: `[t.defaultTokens,t.longTokens]`

- `a=void 0!==t?[t.defaultTokens,t.longTokens]:[void 0]` → `a=void 0!==t?t.tiers:[void 0]`
- Display-suffix condition `n===t?.longTokens` → `n!==t?.defaultTokens`, so every
  non-default tier gets a label (e.g. `kimi-k3 High 400K`).
- Leave the default-variant flag (`... n!==t?.defaultTokens`) alone; Medium + 256K
  stays the default.

### 6. Request-path clamp

Anchor: `Math.min(` on the selected context value vs. the advertised `context_length`.

```js
d=void 0!==u?Math.min(u,l??u):l   →   d=void 0!==u?u:l
```

The selected tier is used as-is even if the gateway reports a smaller window.

### Tier values are decimal

The display formatter divides by 1000, not 1024, so tiers must be decimal
(`272000` → "272K"); binary `278528` would render as "279K".

## Procedure

1. **Locate.** For each bundle, run `rg -o '.{0,200}<anchor>.{0,300}'` per site,
   read the snippets, and confirm the shapes still match the intent above. If a
   site's logic has changed meaningfully, stop and report instead of guessing.
2. **Build exact pairs.** For each bundle and site, write the exact `old`
   substring (from the installed file) and its `new` counterpart using that
   bundle's identifiers. Show the user the pairs before writing anything.
3. **Generate a throwaway script** (in a temp directory, not committed here;
   stdlib only, e.g. Node `fs`/`child_process`) that, per bundle:
   - asserts every `old` occurs exactly once and its `new` zero times
     (all `new` once and `old` zero = already patched; any mix = abort);
   - copies the pristine file to `main.js.pristine` beside it when the file is
     in the pristine state (overwrite a stale backup left by an older version);
   - writes the patched content, runs `node --check` on it, and restores the
     original on any failure, including failures in the other bundle.
4. **Apply**, then grep for each `new` string to confirm it landed once.
5. Tell the user to **fully quit and restart** Cursor Private Inference and
   click "Refresh model list" in the picker.

### Restore

Copy each `main.js.pristine` back over `main.js`. Without a backup, reinstall
the app (or reverse the exact pairs if they are still at hand).

## Verification Checklist

- [ ] Every `new` string present exactly once per bundle; `node --check` passes
- [ ] App fully restarted; model list refreshed
- [ ] A non-family model (e.g. `kimi-k3`) shows Reasoning (default Medium) and
      Context (default 256K) enums
- [ ] Family models (GPT-5.x, Claude Opus, Grok) keep their original effort controls
- [ ] No-effort exceptions (`claude-haiku-4.5`, `claude-sonnet-4`,
      `grok-composer-*`, `gemini-*flash-lite`) still have no Reasoning control
- [ ] Selecting `1M` survives the request path (not clamped to the advertised
      `context_length`)

## Notes and Limitations

- Only the bundle files are modified (plus `.pristine` backups). No re-signing
  is done; the patch changes signed bundle contents.
- Whether the gateway accepts `reasoning_effort:"max"` or a 1M context is up to
  the provider; the patch only restores the local controls and request mapping.
  If a request fails, drop to `xhigh` / a lower tier.
- The effort/context UI additionally requires `extendedCapabilitiesDetected`
  (at least one `/models` entry with a valid `api_types` field). If no enum
  shows at all, check the gateway response first.
- An app update silently overwrites the patch; re-run the procedure after updates.
- Do not put API keys in the skill or task directories.
