#!/usr/bin/env python3
"""Configure Codex third-party model providers while preserving official ChatGPT login."""

from __future__ import annotations

import argparse
import copy
import difflib
import getpass
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

CATALOG_FILENAME = "codex-third-party-provider-catalog.json"
MODELS_DEV_API_URL = "https://models.dev/api.json"
MODELS_DEV_CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_CONTEXT_WINDOW = 400_000
GPT_DEFAULT_CONTEXT_WINDOW = 272_000
GPT_NUMERIC_VERSION = re.compile(r"^(gpt-\d+\.\d+)")

CODEX_PROMPTS_REPO = "asgeirtj/system_prompts_leaks"
CODEX_PROMPTS_BRANCH = "main"
CODEX_PROMPTS_ROOT = "OpenAI/Codex"

PROMPT_RELATIVE_PATHS: tuple[str, ...] = (
    "gpt-5.3-codex-spark.md",
    "gpt-5.4-mini.md",
    "gpt-5.4.md",
    "gpt-5.5.md",
    "gpt-5.6.md",
    "gpt-6-astra.md",
    "old/gpt-5-codex-mini.md",
    "old/gpt-5-codex.md",
    "old/gpt-5.1-codex-max.md",
    "old/gpt-5.1-codex-mini.md",
    "old/gpt-5.1-codex.md",
    "old/gpt-5.1.md",
    "old/gpt-5.2-codex.md",
    "old/gpt-5.2.md",
    "old/gpt-5.3-codex.md",
    "old/gpt-5.md",
)

PREFERRED_PROVIDERS_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "glm": ("zai",),
    "gpt": ("openai",),
}

REASONING_LEVEL_DESCRIPTIONS: list[tuple[str, str]] = [
    ("none", "Disable Thinking"),
    ("minimal", "Minimal reasoning"),
    ("low", "Fast responses with lighter reasoning"),
    ("medium", "Balances speed and reasoning depth for everyday tasks"),
    ("high", "Greater reasoning depth for complex problems"),
    ("xhigh", "Extra high reasoning depth for complex problems"),
    ("max", "Maximum reasoning depth for the hardest problems"),
    ("ultra", "Ultra reasoning depth"),
]

PROVIDER_ID = "custom"
MANIFEST_DIRNAME = ".codex-third-party-provider"
MANIFEST_FILENAME = "manifest.json"
CONFIG_FILENAME = "config.toml"

TEMPLATE: dict[str, Any] = {
    "slug": "native-responses-template",
    "display_name": "native-responses-template",
    "description": "native-responses-template",
    "base_instructions": (
        "You are Codex, a coding agent. You and the user share the same workspace "
        "and collaborate to achieve the user's goals."
    ),
    "default_reasoning_level": "high",
    "supported_reasoning_levels": [
        {"effort": "none", "description": "Disable Thinking"},
        {"effort": "high", "description": "Enabled Thinking"},
    ],
    "shell_type": "shell_command",
    "visibility": "list",
    "supported_in_api": True,
    "priority": 0,
    "supports_reasoning_summaries": True,
    "default_reasoning_summary": "none",
    "support_verbosity": False,
    "truncation_policy": {"mode": "bytes", "limit": 10000},
    "supports_parallel_tool_calls": False,
    "supports_image_detail_original": False,
    "context_window": 262144,
    "max_context_window": 262144,
    "effective_context_window_percent": 95,
    "experimental_supported_tools": [],
    "input_modalities": ["text", "image"],
    "supports_search_tool": False,
}


def normalize_model_id(model_id: str) -> str:
    after_slash = model_id[model_id.rfind("/") + 1 :]
    before_colon = after_slash.split(":", 1)[0]
    normalized = before_colon.strip().replace("@", "-").lower()
    if normalized.endswith("[1m]"):
        normalized = normalized[: -len("[1m]")].strip()
    return normalized


@dataclass
class ModelEntry:
    upstream_id: str
    slug: str
    display_name: str
    context_window: int = DEFAULT_CONTEXT_WINDOW
    reasoning_levels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "upstreamId": self.upstream_id,
            "slug": self.slug,
            "displayName": self.display_name,
            "contextWindow": self.context_window,
        }
        if self.reasoning_levels:
            data["reasoningLevels"] = self.reasoning_levels
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelEntry:
        slug = str(data.get("slug") or "")
        raw_context = data.get("contextWindow") or data.get("context_window")
        return cls(
            upstream_id=str(data.get("upstreamId") or data.get("upstream_id") or ""),
            slug=slug,
            display_name=str(data.get("displayName") or data.get("display_name") or ""),
            context_window=(
                int(raw_context) if raw_context else default_context_window(slug)
            ),
            reasoning_levels=list(data.get("reasoningLevels") or data.get("reasoning_levels") or []),
        )


@dataclass(frozen=True)
class VendorModel:
    id: str
    lookup_key: str
    owned_by: str | None = None


@dataclass
class ModelsDevMatch:
    provider_id: str
    model_id: str
    display_name: str
    context_window: int | None
    reasoning_levels: list[str]


def is_gpt_family(slug: str) -> bool:
    return normalize_model_id(slug).startswith("gpt")


def default_context_window(slug: str) -> int:
    if is_gpt_family(slug):
        return GPT_DEFAULT_CONTEXT_WINDOW
    return DEFAULT_CONTEXT_WINDOW


def _http_get_text(url: str, headers: dict[str, str] | None = None) -> str:
    req = Request(url, headers=headers or {"User-Agent": "codex-third-party-provider"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    return json.loads(_http_get_text(url, headers))


def _canonical_efforts(levels: list[str]) -> list[str]:
    level_set = {level.strip().lower() for level in levels}
    return [effort for effort, _ in REASONING_LEVEL_DESCRIPTIONS if effort in level_set]


def supported_reasoning_levels(levels: list[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for effort in _canonical_efforts(levels):
        description = next(desc for cand, desc in REASONING_LEVEL_DESCRIPTIONS if cand == effort)
        entries.append({"effort": effort, "description": description})
    return entries


def _extract_reasoning_levels(model: dict[str, Any]) -> list[str]:
    options = model.get("reasoning_options")
    if not isinstance(options, list):
        return []
    levels: list[str] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        effort = option.get("effort")
        if isinstance(effort, str) and effort.strip():
            levels.append(effort.strip().lower())
            continue
        values = option.get("values")
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value.strip():
                    levels.append(value.strip().lower())
            continue
        option_type = option.get("type")
        if option_type == "toggle":
            levels.extend(["none", "high"])
    return _canonical_efforts(levels)


def _load_models_dev_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    fetched_at = payload.get("fetchedAt")
    data = payload.get("data")
    if not isinstance(fetched_at, (int, float)) or not isinstance(data, dict):
        return None
    if time.time() - float(fetched_at) > MODELS_DEV_CACHE_TTL_SECONDS:
        return None
    return data


def _save_models_dev_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetchedAt": int(time.time()), "data": data}
    path.write_text(json.dumps(payload), encoding="utf-8")


def fetch_models_dev(cache_path: Path, *, persist: bool = True) -> dict[str, Any]:
    cached = _load_models_dev_cache(cache_path)
    if cached is not None:
        return cached
    data = _http_get_json(MODELS_DEV_API_URL)
    if not isinstance(data, dict):
        raise ValueError("models.dev response must be a JSON object")
    if persist:
        _save_models_dev_cache(cache_path, data)
    return data


def _model_family(model_part: str) -> str | None:
    normalized = normalize_model_id(model_part)
    if normalized.startswith("glm"):
        return "glm"
    if normalized.startswith("gpt"):
        return "gpt"
    return None


def _provider_preference_rank(provider_id: str, model_part: str) -> int:
    family = _model_family(model_part)
    if family is None:
        return 0
    preferred = PREFERRED_PROVIDERS_BY_FAMILY.get(family, ())
    provider = provider_id.strip().lower()
    for index, candidate in enumerate(preferred):
        if provider == candidate.lower():
            return index
    return len(preferred)


def lookup_model(data: dict[str, Any], model_part: str) -> ModelsDevMatch | None:
    part = model_part.strip()
    if not part:
        return None

    normalized = normalize_model_id(part)
    best_rank: tuple[int, int] = (-1, 0)
    best_match: ModelsDevMatch | None = None

    for provider_id, provider in data.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for model_key, model in models.items():
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or model_key)
            if model_key == part or model_id == part:
                score = 100
            elif normalize_model_id(model_key) == normalized or normalize_model_id(model_id) == normalized:
                score = 10
            else:
                continue
            preference_rank = _provider_preference_rank(str(provider_id), part)
            match_rank = (score, -preference_rank)
            if match_rank > best_rank:
                best_rank = match_rank
                best_match = _build_match(str(provider_id), str(model_key), model)

    return best_match


def _build_match(provider_id: str, model_key: str, model: dict[str, Any]) -> ModelsDevMatch:
    limit = model.get("limit")
    context_window: int | None = None
    if isinstance(limit, dict):
        raw = limit.get("context")
        if isinstance(raw, int) and raw > 0:
            context_window = raw
    display_name = str(model.get("name") or model_key)
    reasoning_levels = _extract_reasoning_levels(model)
    return ModelsDevMatch(
        provider_id=provider_id,
        model_id=model_key,
        display_name=display_name,
        context_window=context_window,
        reasoning_levels=reasoning_levels,
    )


def _raw_prompt_url(relative_path: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{CODEX_PROMPTS_REPO}/"
        f"{CODEX_PROMPTS_BRANCH}/{CODEX_PROMPTS_ROOT}/{relative_path}"
    )


def _prompt_stem(relative_path: str) -> str:
    return Path(relative_path).name.removesuffix(".md")


def _load_prompts_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    prompts = payload.get("prompts")
    if not isinstance(prompts, dict):
        return {}
    index: dict[str, str] = {}
    for key, value in prompts.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            index[key.strip()] = value
    return index


def _save_prompts_cache(path: Path, prompts: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetchedAt": int(time.time()), "prompts": prompts}
    path.write_text(json.dumps(payload), encoding="utf-8")


def fetch_codex_prompts_index(cache_path: Path, *, persist: bool = True) -> dict[str, str]:
    if cache_path.exists():
        return _load_prompts_cache(cache_path)

    prompts: dict[str, str] = {}
    for relative_path in PROMPT_RELATIVE_PATHS:
        slug = _prompt_stem(relative_path)
        prompts[slug] = _http_get_text(_raw_prompt_url(relative_path)).strip()

    if persist:
        _save_prompts_cache(cache_path, prompts)
    return prompts


def resolve_prompt_slug(normalized: str, prompts: dict[str, str]) -> str | None:
    if normalized in prompts:
        return normalized
    match = GPT_NUMERIC_VERSION.match(normalized)
    if match is not None:
        version_slug = match.group(1)
        if version_slug in prompts:
            return version_slug
    return None


def lookup_official_model_messages(
    slug: str,
    prompts: dict[str, str],
) -> dict[str, Any] | None:
    if not is_gpt_family(slug):
        return None
    normalized = normalize_model_id(slug)
    resolved = resolve_prompt_slug(normalized, prompts)
    if resolved is None:
        return None
    template = prompts.get(resolved)
    if not template:
        return None
    return {"instructions_template": template}


def _apply_reasoning_override(
    entry: dict[str, Any],
    template_default: str | None,
    reasoning_levels: list[str],
) -> None:
    if not reasoning_levels:
        return
    levels = supported_reasoning_levels(reasoning_levels)
    if not levels:
        return
    entry["supported_reasoning_levels"] = levels
    canonical = [item["effort"] for item in levels]
    default = template_default if template_default in canonical else canonical[-1]
    entry["default_reasoning_level"] = default


def build_catalog_entry(
    template: dict[str, Any],
    model: ModelEntry,
    priority: int,
    *,
    codex_prompts: dict[str, str] | None = None,
) -> dict[str, Any]:
    entry = copy.deepcopy(template)
    display_name = model.display_name or model.slug
    context_window = model.context_window or default_context_window(model.slug)

    entry["slug"] = model.slug
    entry["display_name"] = display_name
    entry["description"] = display_name
    entry["context_window"] = context_window
    entry["max_context_window"] = context_window
    entry["priority"] = 1000 + priority
    entry["additional_speed_tiers"] = []
    entry["service_tiers"] = []
    entry["availability_nux"] = None
    entry["upgrade"] = None
    entry["input_modalities"] = ["text", "image"]
    entry["shell_type"] = "shell_command"

    model_messages = None
    if codex_prompts is not None:
        model_messages = lookup_official_model_messages(model.slug, codex_prompts)

    if model_messages is not None:
        entry["model_messages"] = model_messages
        entry.pop("base_instructions", None)

    template_default = template.get("default_reasoning_level")
    if isinstance(template_default, str):
        _apply_reasoning_override(entry, template_default, model.reasoning_levels)

    return entry


def build_catalog(
    models: list[ModelEntry],
    *,
    codex_prompts_cache_path: Path | None = None,
    persist_caches: bool = True,
) -> dict[str, Any]:
    codex_prompts = (
        fetch_codex_prompts_index(codex_prompts_cache_path, persist=persist_caches)
        if codex_prompts_cache_path is not None
        else {}
    )
    entries = [
        build_catalog_entry(
            TEMPLATE,
            model,
            index,
            codex_prompts=codex_prompts,
        )
        for index, model in enumerate(models)
    ]
    return {"models": entries}


def catalog_to_text(catalog: dict[str, Any]) -> str:
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def _normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def _model_urls(base_url: str) -> list[str]:
    normalized = _normalize_base_url(base_url)
    candidates: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if url not in seen:
            seen.add(url)
            candidates.append(url)

    if normalized.endswith("/models"):
        add(normalized)
    elif normalized.endswith("/v1"):
        add(f"{normalized}/models")
    else:
        add(f"{normalized}/v1/models")
        add(f"{normalized}/models")
        parsed = urlparse(normalized)
        if parsed.path and parsed.path not in ("", "/"):
            trimmed = normalized.rsplit("/", 1)[0]
            add(f"{trimmed}/v1/models")
            add(f"{trimmed}/models")
        add(urljoin(f"{normalized}/", "v1/models"))

    return candidates


def fetch_vendor_models(base_url: str, api_key: str) -> list[VendorModel]:
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "User-Agent": "codex-third-party-provider",
    }
    errors: list[str] = []

    for url in _model_urls(base_url):
        try:
            payload = _http_get_json(url, headers)
        except HTTPError as exc:
            errors.append(f"{url}: HTTP {exc.code}")
            continue
        except (URLError, TimeoutError, ValueError) as exc:
            errors.append(f"{url}: {exc}")
            continue

        models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            errors.append(f"{url}: unexpected response shape")
            continue

        result: list[VendorModel] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            owned_by = item.get("owned_by")
            result.append(
                VendorModel(
                    id=model_id.strip(),
                    lookup_key=normalize_model_id(model_id),
                    owned_by=str(owned_by) if owned_by is not None else None,
                )
            )
        result.sort(key=lambda model: model.id.lower())
        return result

    detail = "; ".join(errors) if errors else "no endpoints tried"
    raise RuntimeError(f"Failed to fetch models from {base_url}: {detail}")


@dataclass(frozen=True)
class CodexPaths:
    codex_dir: Path
    config_path: Path
    catalog_path: Path
    manifest_dir: Path
    manifest_path: Path
    models_dev_cache_path: Path
    codex_prompts_cache_path: Path

    @classmethod
    def resolve(cls, codex_dir: Path | None = None) -> CodexPaths:
        base = codex_dir or default_codex_dir()
        manifest_dir = base / MANIFEST_DIRNAME
        return cls(
            codex_dir=base,
            config_path=base / CONFIG_FILENAME,
            catalog_path=base / CATALOG_FILENAME,
            manifest_dir=manifest_dir,
            manifest_path=manifest_dir / MANIFEST_FILENAME,
            models_dev_cache_path=manifest_dir / "models-dev-cache.json",
            codex_prompts_cache_path=manifest_dir / "codex-prompts-cache.json",
        )


def default_codex_dir() -> Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def resolve_upstream_id(model_ref: str, vendor_ids: list[str] | None = None) -> str:
    ref = model_ref.strip()
    if not ref:
        return ref
    if "/" in ref:
        return ref
    if vendor_ids:
        for vendor_id in vendor_ids:
            if vendor_id == ref or vendor_id.endswith(f"/{ref}"):
                return vendor_id
            if normalize_model_id(vendor_id) == normalize_model_id(ref):
                return vendor_id
    return ref


@dataclass
class Manifest:
    base_url: str = ""
    api_key: str = ""
    models: list[ModelEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseUrl": self.base_url,
            "apiKey": self.api_key,
            "models": [model.to_dict() for model in self.models],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        models_raw = data.get("models") or []
        models = [ModelEntry.from_dict(item) for item in models_raw if isinstance(item, dict)]
        return cls(
            base_url=str(data.get("baseUrl") or data.get("base_url") or ""),
            api_key=str(data.get("apiKey") or data.get("api_key") or ""),
            models=models,
        )


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_manifest(path: Path) -> Manifest:
    text = read_text(path).strip()
    if not text:
        return Manifest()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid manifest at {path}")
    return Manifest.from_dict(data)


def save_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_model(manifest: Manifest, model_ref: str) -> ModelEntry | None:
    ref = model_ref.strip()
    norm = ref.lower()
    for model in manifest.models:
        if model.slug == ref or model.upstream_id == ref:
            return model
        if model.slug.lower() == norm or model.upstream_id.lower() == norm:
            return model
    return None


def _toml_scalar(value: str | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _split_root_and_rest(text: str) -> tuple[str, str]:
    match = re.search(r"(?m)^\[", text)
    if not match:
        return text, ""
    return text[: match.start()], text[match.start() :]


def _upsert_assignment(block: str, key: str, rendered: str | None) -> str:
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=[ \t]*.*?\r?$")
    if rendered is None:
        updated = pattern.sub("", block)
        return re.sub(r"\n{3,}", "\n\n", updated)
    if pattern.search(block):
        return pattern.sub(f"{key} = {rendered}", block, count=1)
    prefix = block.rstrip()
    sep = "\n" if prefix else ""
    suffix = "\n" if not block.endswith("\n") else ""
    return f"{prefix}{sep}{key} = {rendered}\n{suffix}"


def _find_section(text: str, header: str) -> tuple[int, int] | None:
    pattern = re.compile(rf"(?m)^\[{re.escape(header)}\][ \t]*\r?\n")
    match = pattern.search(text)
    if not match:
        return None
    rest = text[match.end() :]
    nxt = re.search(r"(?m)^\[", rest)
    end = match.end() + (nxt.start() if nxt else len(rest))
    return match.start(), end


def _strip_inline_custom(text: str) -> str:
    loc = _find_section(text, "model_providers")
    if loc is None:
        return text
    start, end = loc
    section = text[start:end]
    stripped = re.sub(
        r"(?m)^[ \t]*custom[ \t]*=[ \t]*\{.*\}[ \t]*\r?\n?",
        "",
        section,
        count=1,
    )
    return text[:start] + stripped + text[end:]


def merge_provider_config(
    text: str,
    *,
    base_url: str,
    api_key: str,
    set_catalog_pointer: bool,
) -> str:
    root, rest = _split_root_and_rest(text)
    root = _upsert_assignment(root, "model_provider", _toml_scalar(PROVIDER_ID))
    if set_catalog_pointer:
        root = _upsert_assignment(root, "model_catalog_json", _toml_scalar(CATALOG_FILENAME))
    else:
        root = re.sub(
            rf'(?m)^[ \t]*model_catalog_json[ \t]*=[ \t]*["\']{re.escape(CATALOG_FILENAME)}["\'][ \t]*\r?$',
            "",
            root,
        )

    rest = _strip_inline_custom(rest)
    fields = {
        "name": _toml_scalar("third-party"),
        "base_url": _toml_scalar(base_url.rstrip("/")),
        "wire_api": _toml_scalar("responses"),
        "experimental_bearer_token": _toml_scalar(api_key),
        "requires_openai_auth": _toml_scalar(True),
    }
    loc = _find_section(rest, "model_providers.custom")
    if loc is None:
        block = "[model_providers.custom]\n" + "".join(
            f"{key} = {value}\n" for key, value in fields.items()
        )
        rest = rest.rstrip() + ("\n\n" if rest.strip() else "") + block
        if not rest.endswith("\n"):
            rest += "\n"
    else:
        start, end = loc
        section = rest[start:end]
        for key, value in fields.items():
            section = _upsert_assignment(section, key, value)
        rest = rest[:start] + section + rest[end:]

    merged = root.rstrip() + "\n\n" + rest.lstrip() if rest.strip() else root
    if not merged.endswith("\n"):
        merged += "\n"
    return merged


def backup_config(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def resolve_credentials(
    manifest: Manifest,
    *,
    base_url: str | None,
    api_key: str | None,
) -> tuple[str, str]:
    resolved_base = (base_url or manifest.base_url or "").strip()
    resolved_key = (api_key or manifest.api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not resolved_base:
        raise ValueError("Missing --base-url; run setup first or pass --base-url")
    if not resolved_key:
        raise ValueError("Missing --api-key; run setup, pass --api-key, or set OPENAI_API_KEY")
    return resolved_base, resolved_key


def setup_provider(
    paths: CodexPaths,
    *,
    base_url: str,
    api_key: str,
) -> None:
    base_url = base_url.strip()
    api_key = api_key.strip()
    if not base_url:
        raise ValueError("--base-url is required")
    if not api_key:
        raise ValueError("--api-key is required")

    manifest = load_manifest(paths.manifest_path)
    manifest.base_url = base_url
    manifest.api_key = api_key

    config_before = read_text(paths.config_path)
    config_after = merge_provider_config(
        config_before,
        base_url=base_url,
        api_key=api_key,
        set_catalog_pointer=bool(manifest.models),
    )

    if config_before != config_after:
        backup_config(paths.config_path)
        paths.config_path.parent.mkdir(parents=True, exist_ok=True)
        paths.config_path.write_text(config_after, encoding="utf-8")

    save_manifest(paths.manifest_path, manifest)
    print("Provider configured. auth.json was not modified.")


def list_vendor_models(
    paths: CodexPaths,
    *,
    base_url: str | None,
    api_key: str | None,
) -> None:
    manifest = load_manifest(paths.manifest_path)
    resolved_base, resolved_key = resolve_credentials(manifest, base_url=base_url, api_key=api_key)
    models = fetch_vendor_models(resolved_base, resolved_key)
    print(f"{'ID':<40} {'LOOKUP':<30} OWNED_BY")
    print("-" * 90)
    for model in models:
        owned_by = model.owned_by or "-"
        print(f"{model.id:<40} {model.lookup_key:<30} {owned_by}")


def show_catalog(paths: CodexPaths) -> None:
    manifest = load_manifest(paths.manifest_path)
    if not manifest.models:
        print("Catalog is empty.")
        return
    print(f"{'SLUG':<24} {'UPSTREAM':<32} DISPLAY")
    print("-" * 90)
    for model in manifest.models:
        print(f"{model.slug:<24} {model.upstream_id:<32} {model.display_name}")


def models_dev_model_part(add_input: str) -> str:
    ref = add_input.strip()
    if "/" in ref:
        return ref.split("/", 1)[1].strip()
    return ref


def _require_model_selection(command: str, *, all_models: bool, models: list[str]) -> None:
    if all_models and models:
        raise ValueError(f"{command}: --all cannot be combined with MODEL")
    if not all_models and not models:
        raise ValueError(f"{command}: pass MODEL ... or --all")


def _build_model_entry(
    add_input: str,
    upstream_id: str,
    paths: CodexPaths,
    *,
    context_window: int | None = None,
    persist_caches: bool = True,
    models_dev: dict[str, Any] | None = None,
) -> ModelEntry:
    model_part = models_dev_model_part(add_input)
    if models_dev is None:
        models_dev = fetch_models_dev(paths.models_dev_cache_path, persist=persist_caches)
    match = lookup_model(models_dev, model_part)
    display_name = add_input.strip()
    resolved_context = context_window
    if resolved_context is None:
        resolved_context = (
            match.context_window
            if match and match.context_window
            else default_context_window(add_input)
        )
    reasoning_levels = match.reasoning_levels if match else []
    return ModelEntry(
        upstream_id=upstream_id,
        slug=add_input.strip(),
        display_name=display_name,
        context_window=resolved_context,
        reasoning_levels=reasoning_levels,
    )


@dataclass(frozen=True)
class AddPlan:
    slugs: tuple[str, ...]
    manifest_before: str
    manifest_after: str
    catalog_before: str
    catalog_after: str
    config_before: str
    config_after: str
    duplicate: bool


def plan_add_models(
    paths: CodexPaths,
    model_refs: list[str],
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    context_window: int | None = None,
    persist_caches: bool = True,
    skip_existing: bool = False,
    require_vendor: bool = False,
) -> AddPlan:
    if context_window is not None and context_window <= 0:
        raise ValueError("--context-window must be a positive integer")

    manifest = load_manifest(paths.manifest_path)
    resolved_base, resolved_key = resolve_credentials(manifest, base_url=base_url, api_key=api_key)

    if require_vendor:
        model_refs = [model.id for model in fetch_vendor_models(resolved_base, resolved_key)]
        vendor_ids = model_refs
    else:
        try:
            vendor_ids = [model.id for model in fetch_vendor_models(resolved_base, resolved_key)]
        except RuntimeError:
            vendor_ids = None

    models_dev = fetch_models_dev(paths.models_dev_cache_path, persist=persist_caches)
    planned_manifest = copy.deepcopy(manifest)
    planned_manifest.base_url = resolved_base
    planned_manifest.api_key = resolved_key
    changed_slugs: list[str] = []

    for raw in model_refs:
        add_input = raw.strip()
        if not add_input:
            raise ValueError("Model id is empty")
        existing = find_model(planned_manifest, add_input)
        if existing is not None and context_window is None:
            if skip_existing:
                continue
            raise ValueError(f"Model {add_input} already exists in catalog")
        upstream_id = resolve_upstream_id(add_input, vendor_ids)
        entry = _build_model_entry(
            add_input,
            upstream_id,
            paths,
            context_window=context_window,
            persist_caches=persist_caches,
            models_dev=models_dev,
        )
        if existing is not None:
            planned_manifest.models = [
                entry if model.slug == existing.slug else model
                for model in planned_manifest.models
            ]
        else:
            planned_manifest.models.append(entry)
        changed_slugs.append(add_input)

    manifest_before = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n"
    if not paths.manifest_path.exists():
        manifest_before = ""
    catalog_before = read_text(paths.catalog_path)
    config_before = read_text(paths.config_path)

    if not changed_slugs:
        return AddPlan(
            slugs=(),
            manifest_before=manifest_before,
            manifest_after=manifest_before,
            catalog_before=catalog_before,
            catalog_after=catalog_before,
            config_before=config_before,
            config_after=config_before,
            duplicate=True,
        )

    manifest_after = json.dumps(planned_manifest.to_dict(), indent=2, ensure_ascii=False) + "\n"
    if planned_manifest.models:
        catalog_after = catalog_to_text(
            build_catalog(
                planned_manifest.models,
                codex_prompts_cache_path=paths.codex_prompts_cache_path,
                persist_caches=persist_caches,
            )
        )
        config_after = merge_provider_config(
            config_before,
            base_url=resolved_base,
            api_key=resolved_key,
            set_catalog_pointer=True,
        )
    else:
        catalog_after = ""
        config_after = merge_provider_config(
            config_before,
            base_url=resolved_base,
            api_key=resolved_key,
            set_catalog_pointer=False,
        )

    return AddPlan(
        slugs=tuple(changed_slugs),
        manifest_before=manifest_before,
        manifest_after=manifest_after,
        catalog_before=catalog_before,
        catalog_after=catalog_after,
        config_before=config_before,
        config_after=config_after,
        duplicate=False,
    )


def execute_add(paths: CodexPaths, plan: AddPlan) -> None:
    if plan.duplicate:
        print("No models to add.")
        return

    paths.manifest_dir.mkdir(parents=True, exist_ok=True)
    paths.manifest_path.write_text(plan.manifest_after, encoding="utf-8")

    if plan.catalog_after.strip():
        paths.catalog_path.write_text(plan.catalog_after, encoding="utf-8")
    elif paths.catalog_path.exists():
        paths.catalog_path.unlink()

    if plan.config_before != plan.config_after:
        backup_config(paths.config_path)
        paths.config_path.parent.mkdir(parents=True, exist_ok=True)
        paths.config_path.write_text(plan.config_after, encoding="utf-8")

    print(f"Added {len(plan.slugs)} model(s).")
    print("Restart Codex to refresh /model.")


def remove_models(paths: CodexPaths, *, all_models: bool, models: list[str]) -> None:
    manifest = load_manifest(paths.manifest_path)
    if all_models:
        if not manifest.models:
            raise ValueError("Catalog is empty")
        removed = [model.slug for model in manifest.models]
        manifest.models = []
    else:
        seen: set[str] = set()
        removed: list[str] = []
        for ref in models:
            target = find_model(manifest, ref)
            if target is None:
                raise ValueError(f"Model not found: {ref}")
            if target.slug not in seen:
                removed.append(target.slug)
                seen.add(target.slug)
        manifest.models = [model for model in manifest.models if model.slug not in seen]

    save_manifest(paths.manifest_path, manifest)

    config_before = read_text(paths.config_path)
    if manifest.models:
        catalog_text = catalog_to_text(
            build_catalog(manifest.models, codex_prompts_cache_path=paths.codex_prompts_cache_path)
        )
        paths.catalog_path.write_text(catalog_text, encoding="utf-8")
        config_after = merge_provider_config(
            config_before,
            base_url=manifest.base_url,
            api_key=manifest.api_key,
            set_catalog_pointer=True,
        )
    else:
        if paths.catalog_path.exists():
            paths.catalog_path.unlink()
        config_after = merge_provider_config(
            config_before,
            base_url=manifest.base_url,
            api_key=manifest.api_key,
            set_catalog_pointer=False,
        )

    if config_before != config_after:
        backup_config(paths.config_path)
        paths.config_path.write_text(config_after, encoding="utf-8")

    print(f"Removed {len(removed)} model(s).")
    if manifest.models:
        print("Restart Codex to refresh /model.")


@dataclass(frozen=True)
class FileChange:
    label: str
    before: str
    after: str


@dataclass(frozen=True)
class DiffPlan:
    changes: tuple[FileChange, ...]
    summary: str
    duplicate: bool = False


def mask_secret(value: str) -> str:
    trimmed = value.strip()
    if len(trimmed) <= 8:
        return "***"
    return f"{trimmed[:3]}...{trimmed[-4:]}"


def redact_secrets(text: str) -> str:
    def repl_api_key(match: re.Match[str]) -> str:
        value = match.group(1)
        return f'"apiKey": "{mask_secret(value)}"'

    text = re.sub(r'"apiKey"\s*:\s*"([^"]*)"', repl_api_key, text)
    text = re.sub(
        r'(experimental_bearer_token\s*=\s*")([^"]*)(")',
        lambda m: f'{m.group(1)}{mask_secret(m.group(2))}{m.group(3)}',
        text,
    )
    return text


def render_diff(plan: DiffPlan, *, use_color: bool | None = None) -> str:
    if use_color is None:
        use_color = sys.stdout.isatty()

    chunks: list[str] = []
    changed = 0
    for change in plan.changes:
        before = change.before
        after = change.after
        if before == after:
            continue
        changed += 1
        chunks.append(f"=== {change.label} ===")
        diff_lines = difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"{change.label} (current)",
            tofile=f"{change.label} (planned)",
            lineterm="",
        )
        for line in diff_lines:
            if use_color:
                if line.startswith("+") and not line.startswith("+++"):
                    line = f"\033[32m{line}\033[0m"
                elif line.startswith("-") and not line.startswith("---"):
                    line = f"\033[31m{line}\033[0m"
                elif line.startswith("@@"):
                    line = f"\033[36m{line}\033[0m"
            chunks.append(redact_secrets(line))
        chunks.append("")

    if plan.duplicate:
        chunks.append("No changes: nothing to add.")
    elif changed == 0:
        chunks.append("No file changes.")
    else:
        chunks.append(plan.summary)
    return "\n".join(chunks).rstrip() + "\n"


def add_plan_to_diff(plan: AddPlan) -> DiffPlan:
    if plan.duplicate:
        summary = "No changes: nothing to add."
        return DiffPlan(changes=(), summary=summary, duplicate=True)

    changes = (
        FileChange("manifest.json", plan.manifest_before, plan.manifest_after),
        FileChange(CATALOG_FILENAME, plan.catalog_before, plan.catalog_after),
        FileChange("config.toml", plan.config_before, plan.config_after),
    )
    changed_count = sum(1 for change in changes if change.before != change.after)
    summary = f"Would add {len(plan.slugs)} model(s); {changed_count} file(s) changed"
    return DiffPlan(changes=changes, summary=summary)


def _prompt_line(label: str, *, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        print("Value is required.", file=sys.stderr)


def _prompt_secret(label: str) -> str:
    while True:
        value = getpass.getpass(f"{label}: ").strip()
        if value:
            return value
        print("Value is required.", file=sys.stderr)


def resolve_setup_credentials(
    base_url: str | None,
    api_key: str | None,
    *,
    default_base_url: str | None = None,
) -> tuple[str, str]:
    resolved_base = (base_url or default_base_url or "").strip()
    resolved_key = (api_key or os.environ.get("OPENAI_API_KEY") or "").strip()

    if not resolved_base:
        if not sys.stdin.isatty():
            raise ValueError("Missing --base-url (non-interactive session)")
        resolved_base = _prompt_line("Base URL", default=default_base_url)

    if not resolved_key:
        if not sys.stdin.isatty():
            raise ValueError("Missing --api-key; pass flag or set OPENAI_API_KEY")
        resolved_key = _prompt_secret("API key")

    return resolved_base, resolved_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-third-party-provider",
        description="Configure Codex third-party models while preserving official login.",
    )
    parser.add_argument(
        "--codex-dir",
        type=Path,
        default=None,
        help="Codex config directory (default: $CODEX_HOME or ~/.codex)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Configure provider base URL and API key")
    setup.add_argument(
        "--base-url",
        default=None,
        help="Provider base URL; prompts interactively when omitted",
    )
    setup.add_argument(
        "--api-key",
        default=None,
        help="Provider API key; prompts interactively when omitted and OPENAI_API_KEY is unset",
    )

    list_cmd = subparsers.add_parser("list", help="List models from vendor GET /v1/models")
    list_cmd.add_argument("--base-url", default=None)
    list_cmd.add_argument("--api-key", default=None)

    add = subparsers.add_parser("add", help="Add models to the local catalog")
    add.add_argument("models", nargs="*", metavar="MODEL", help="Model id or slug")
    add.add_argument(
        "--all",
        action="store_true",
        dest="all_models",
        help="Add every id from vendor GET /v1/models (skips ids already in the catalog)",
    )
    add.add_argument("--base-url", default=None)
    add.add_argument("--api-key", default=None)
    add.add_argument(
        "--context-window",
        type=int,
        default=None,
        metavar="TOKENS",
        help="Override context window size (also updates existing model entries)",
    )
    add.add_argument("--dry-run", action="store_true")

    rm = subparsers.add_parser("rm", help="Remove models from the local catalog")
    rm.add_argument("models", nargs="*", metavar="MODEL", help="Model slug or upstream id")
    rm.add_argument(
        "--all",
        action="store_true",
        dest="all_models",
        help="Remove every model from the local catalog",
    )

    subparsers.add_parser("catalog", help="Show models already added")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = CodexPaths.resolve(args.codex_dir)

    if args.command == "setup":
        manifest = load_manifest(paths.manifest_path)
        base_url, api_key = resolve_setup_credentials(
            args.base_url,
            args.api_key,
            default_base_url=manifest.base_url or None,
        )
        setup_provider(paths, base_url=base_url, api_key=api_key)
        return 0

    if args.command == "list":
        list_vendor_models(paths, base_url=args.base_url, api_key=args.api_key)
        return 0

    if args.command == "catalog":
        show_catalog(paths)
        return 0

    if args.command == "add":
        _require_model_selection("add", all_models=args.all_models, models=args.models)
        plan = plan_add_models(
            paths,
            args.models,
            base_url=args.base_url,
            api_key=args.api_key,
            context_window=args.context_window,
            persist_caches=not args.dry_run,
            skip_existing=args.all_models,
            require_vendor=args.all_models,
        )
        if args.dry_run:
            diff = add_plan_to_diff(plan)
            sys.stdout.write(render_diff(diff))
            return 0
        execute_add(paths, plan)
        return 0

    if args.command == "rm":
        _require_model_selection("rm", all_models=args.all_models, models=args.models)
        remove_models(paths, all_models=args.all_models, models=args.models)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
