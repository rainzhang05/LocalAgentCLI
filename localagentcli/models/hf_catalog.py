"""Dynamic Hugging Face catalog discovery for the interactive /models picker."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

HF_API_BASE_URL = "https://huggingface.co"
HF_MODELS_API_PATH = "/api/models"
HF_QUERY_LIMIT = 25
HF_MAX_MODELS_PER_FAMILY = 18

_SUPPORTED_PIPELINES = {
    "",
    "conversational",
    "image-text-to-text",
    "text-generation",
    "text2text-generation",
}


@dataclass(frozen=True)
class HubModelFamily:
    """One model family shown in the interactive Hugging Face picker."""

    backend: str
    key: str
    label: str
    description: str
    queries: tuple[str, ...]
    keywords: tuple[str, ...]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class HubModelChoice:
    """One discovered Hugging Face repo selectable in the /models picker."""

    backend: str
    family: str
    repo: str
    label: str
    install_name: str
    summary: str
    aliases: tuple[str, ...] = ()
    downloads: int = 0
    likes: int = 0


_FAMILY_SPECS: dict[str, tuple[HubModelFamily, ...]] = {
    "gguf": (
        HubModelFamily(
            "gguf",
            "gpt-oss",
            "GPT-OSS",
            "Open-weight GPT-OSS conversions in GGUF format.",
            ("gpt-oss GGUF",),
            ("gpt-oss",),
        ),
        HubModelFamily(
            "gguf",
            "qwen",
            "Qwen",
            "Qwen instruct and reasoning builds in GGUF format.",
            ("Qwen GGUF", "Qwen3 GGUF"),
            ("qwen", "qwq"),
        ),
        HubModelFamily(
            "gguf",
            "llama",
            "Llama",
            "Meta Llama community GGUF conversions across multiple sizes.",
            ("Llama GGUF", "Llama-3 GGUF"),
            ("llama",),
        ),
        HubModelFamily(
            "gguf",
            "gemma",
            "Gemma",
            "Gemma 2 and Gemma 3 quantized GGUF repos.",
            ("Gemma GGUF", "Gemma 3 GGUF"),
            ("gemma",),
        ),
        HubModelFamily(
            "gguf",
            "mistral",
            "Mistral",
            "Mistral, Ministral, and Mistral Small GGUF repos.",
            ("Mistral GGUF", "Mistral Small GGUF"),
            ("mistral", "ministral"),
        ),
        HubModelFamily(
            "gguf",
            "phi",
            "Phi",
            "Phi family GGUF repos from Microsoft and community authors.",
            ("Phi GGUF", "Phi-4 GGUF"),
            ("phi",),
        ),
        HubModelFamily(
            "gguf",
            "deepseek",
            "DeepSeek",
            "DeepSeek reasoning and coder repos in GGUF format.",
            ("DeepSeek GGUF", "DeepSeek-R1 GGUF"),
            ("deepseek",),
        ),
        HubModelFamily(
            "gguf",
            "granite",
            "Granite",
            "IBM Granite repos converted to GGUF for llama.cpp.",
            ("Granite GGUF",),
            ("granite",),
        ),
        HubModelFamily(
            "gguf",
            "smollm",
            "SmolLM",
            "Small-footprint SmolLM repos in GGUF format.",
            ("SmolLM GGUF", "SmolLM2 GGUF"),
            ("smollm",),
        ),
        HubModelFamily(
            "gguf",
            "starcoder",
            "StarCoder",
            "Code generation models from the StarCoder line in GGUF format.",
            ("StarCoder GGUF", "StarCoder2 GGUF"),
            ("starcoder",),
        ),
        HubModelFamily(
            "gguf",
            "codestral",
            "Codestral",
            "Codestral coding-focused GGUF repos.",
            ("Codestral GGUF",),
            ("codestral",),
        ),
        HubModelFamily(
            "gguf",
            "tinyllama",
            "TinyLlama",
            "TinyLlama GGUF repos for lightweight local runs.",
            ("TinyLlama GGUF",),
            ("tinyllama",),
        ),
        HubModelFamily(
            "gguf",
            "glm",
            "GLM",
            "GLM repos converted to GGUF for local inference.",
            ("GLM GGUF", "GLM-4 GGUF"),
            ("glm",),
        ),
    ),
    "mlx": (
        HubModelFamily(
            "mlx",
            "gpt-oss",
            "GPT-OSS",
            "MLX-compatible GPT-OSS repos for Apple Silicon.",
            ("gpt-oss MLX", "mlx-community gpt-oss"),
            ("gpt-oss",),
        ),
        HubModelFamily(
            "mlx",
            "qwen",
            "Qwen",
            "Qwen MLX repos from mlx-community and other Apple-focused publishers.",
            ("mlx-community qwen", "Qwen MLX"),
            ("qwen", "qwq"),
        ),
        HubModelFamily(
            "mlx",
            "llama",
            "Llama",
            "Llama MLX repos tuned for Apple Silicon.",
            ("mlx-community llama", "Llama MLX"),
            ("llama",),
        ),
        HubModelFamily(
            "mlx",
            "gemma",
            "Gemma",
            "Gemma MLX repos for Apple Silicon.",
            ("mlx-community gemma", "Gemma MLX"),
            ("gemma",),
        ),
        HubModelFamily(
            "mlx",
            "mistral",
            "Mistral",
            "Mistral MLX repos including smaller Apple-friendly variants.",
            ("mlx-community mistral", "Mistral MLX"),
            ("mistral", "ministral"),
        ),
        HubModelFamily(
            "mlx",
            "phi",
            "Phi",
            "Phi MLX repos for compact local inference on Macs.",
            ("mlx-community phi", "Phi MLX"),
            ("phi",),
        ),
        HubModelFamily(
            "mlx",
            "deepseek",
            "DeepSeek",
            "DeepSeek MLX repos, including reasoning variants.",
            ("mlx-community deepseek", "DeepSeek MLX"),
            ("deepseek",),
        ),
        HubModelFamily(
            "mlx",
            "granite",
            "Granite",
            "IBM Granite MLX conversions for Apple Silicon.",
            ("mlx-community granite", "Granite MLX"),
            ("granite",),
        ),
        HubModelFamily(
            "mlx",
            "smollm",
            "SmolLM",
            "Small Apple Silicon-friendly SmolLM MLX repos.",
            ("mlx-community smollm", "SmolLM MLX"),
            ("smollm",),
        ),
        HubModelFamily(
            "mlx",
            "glm",
            "GLM",
            "GLM MLX repos from Apple-focused model publishers.",
            ("mlx-community glm", "GLM MLX"),
            ("glm",),
        ),
    ),
    "safetensors": (
        HubModelFamily(
            "safetensors",
            "gpt-oss",
            "GPT-OSS",
            "Official and community GPT-OSS safetensors repos.",
            ("openai/gpt-oss", "gpt-oss"),
            ("gpt-oss",),
        ),
        HubModelFamily(
            "safetensors",
            "qwen",
            "Qwen",
            "Qwen instruct, coder, and reasoning safetensors repos.",
            ("Qwen/Qwen", "Qwen3"),
            ("qwen", "qwq"),
        ),
        HubModelFamily(
            "safetensors",
            "llama",
            "Llama",
            "Official Meta Llama safetensors repos.",
            ("meta-llama/Llama", "Llama-3"),
            ("llama",),
        ),
        HubModelFamily(
            "safetensors",
            "gemma",
            "Gemma",
            "Gemma safetensors repos from Google.",
            ("google/gemma", "Gemma 3"),
            ("gemma",),
        ),
        HubModelFamily(
            "safetensors",
            "mistral",
            "Mistral",
            "Mistral and Ministral safetensors repos.",
            ("mistralai/Mistral", "Mistral Small"),
            ("mistral", "ministral"),
        ),
        HubModelFamily(
            "safetensors",
            "phi",
            "Phi",
            "Phi safetensors repos from Microsoft.",
            ("microsoft/Phi", "Phi-4"),
            ("phi",),
        ),
        HubModelFamily(
            "safetensors",
            "deepseek",
            "DeepSeek",
            "DeepSeek reasoning, chat, and coder safetensors repos.",
            ("deepseek-ai/DeepSeek", "DeepSeek-R1"),
            ("deepseek",),
        ),
        HubModelFamily(
            "safetensors",
            "granite",
            "Granite",
            "IBM Granite safetensors repos.",
            ("ibm-granite/granite", "granite"),
            ("granite",),
        ),
        HubModelFamily(
            "safetensors",
            "smollm",
            "SmolLM",
            "Small SmolLM safetensors repos.",
            ("HuggingFaceTB/SmolLM", "SmolLM2"),
            ("smollm",),
        ),
        HubModelFamily(
            "safetensors",
            "starcoder",
            "StarCoder",
            "StarCoder and StarCoder2 safetensors repos.",
            ("bigcode/starcoder", "StarCoder2"),
            ("starcoder",),
        ),
        HubModelFamily(
            "safetensors",
            "codestral",
            "Codestral",
            "Codestral safetensors repos for coding workloads.",
            ("mistralai/Codestral", "codestral"),
            ("codestral",),
        ),
        HubModelFamily(
            "safetensors",
            "tinyllama",
            "TinyLlama",
            "TinyLlama safetensors repos for lightweight use.",
            ("TinyLlama/TinyLlama",),
            ("tinyllama",),
        ),
        HubModelFamily(
            "safetensors",
            "glm",
            "GLM",
            "GLM safetensors repos from THUDM and community publishers.",
            ("THUDM/GLM", "GLM-4"),
            ("glm",),
        ),
    ),
}


class HuggingFaceCatalog:
    """Discover Hugging Face repos for the interactive /models picker."""

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            base_url=HF_API_BASE_URL,
            headers=_hf_headers(),
            timeout=20,
            follow_redirects=True,
        )
        self._cache: dict[tuple[str, str], list[HubModelChoice]] = {}

    def list_families(self, backend: str) -> list[HubModelFamily]:
        """Return the available families for one backend."""
        return list(_FAMILY_SPECS.get(backend, ()))

    def list_models(self, backend: str, family: str) -> list[HubModelChoice]:
        """Discover selectable Hugging Face repos for one backend/family pair."""
        cache_key = (backend, family)
        if cache_key in self._cache:
            return list(self._cache[cache_key])

        spec = _find_family_spec(backend, family)
        choices_by_repo: dict[str, HubModelChoice] = {}
        for query in spec.queries:
            response = self._client.get(
                HF_MODELS_API_PATH,
                params={
                    "search": query,
                    "sort": "downloads",
                    "direction": "-1",
                    "limit": HF_QUERY_LIMIT,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                continue
            for record in payload:
                choice = _choice_from_record(record, spec)
                if choice is None:
                    continue
                current = choices_by_repo.get(choice.repo)
                if current is None or (choice.downloads, choice.likes) > (
                    current.downloads,
                    current.likes,
                ):
                    choices_by_repo[choice.repo] = choice

        ordered = sorted(
            choices_by_repo.values(),
            key=lambda item: (-item.downloads, -item.likes, item.label.lower()),
        )[:HF_MAX_MODELS_PER_FAMILY]
        self._cache[cache_key] = ordered
        return list(ordered)


def _find_family_spec(backend: str, family: str) -> HubModelFamily:
    for spec in _FAMILY_SPECS.get(backend, ()):
        if spec.key == family:
            return spec
    raise ValueError(f"Unknown Hugging Face family '{family}' for backend '{backend}'.")


def _choice_from_record(record: object, spec: HubModelFamily) -> HubModelChoice | None:
    if not isinstance(record, dict):
        return None

    repo = str(record.get("id") or record.get("modelId") or "").strip()
    if not repo:
        return None

    lowered_repo = repo.lower()
    tags = _lowered_tags(record.get("tags"))
    pipeline = str(record.get("pipeline_tag") or "").strip().lower()
    if pipeline not in _SUPPORTED_PIPELINES:
        return None
    if not _repo_matches_backend(lowered_repo, tags, spec.backend):
        return None
    if not _repo_matches_family(lowered_repo, tags, spec.keywords):
        return None
    if _should_skip_repo(lowered_repo, tags):
        return None

    owner, short_name = _split_repo(repo)
    downloads = _as_int(record.get("downloads"))
    likes = _as_int(record.get("likes"))
    label = f"{_humanize_model_name(short_name)} [{owner}]"
    summary_bits = []
    if downloads > 0:
        summary_bits.append(f"{downloads:,} downloads")
    if likes > 0:
        summary_bits.append(f"{likes:,} likes")
    summary_bits.append(repo)
    summary = " • ".join(summary_bits)

    return HubModelChoice(
        backend=spec.backend,
        family=spec.key,
        repo=repo,
        label=label,
        install_name=_derive_install_name(repo),
        summary=summary,
        aliases=(repo, short_name, owner, spec.key, *spec.aliases),
        downloads=downloads,
        likes=likes,
    )


def _repo_matches_backend(repo: str, tags: set[str], backend: str) -> bool:
    if backend == "gguf":
        return "gguf" in repo or "gguf" in tags
    if backend == "mlx":
        return (
            repo.startswith("mlx-community/")
            or repo.startswith("lmstudio-community/")
            or "mlx" in repo
            or "mlx" in tags
        )
    if backend == "safetensors":
        return (
            "gguf" not in repo
            and "gguf" not in tags
            and "mlx" not in tags
            and not repo.startswith("mlx-community/")
            and "-mlx" not in repo
        )
    return False


def _repo_matches_family(repo: str, tags: set[str], keywords: tuple[str, ...]) -> bool:
    return any(keyword.lower() in repo or keyword.lower() in tags for keyword in keywords)


def _should_skip_repo(repo: str, tags: set[str]) -> bool:
    blocked = ("adapter", "lora", "mergekit", "embedding", "reranker", "reward")
    return any(marker in repo or marker in tags for marker in blocked)


def _lowered_tags(raw_tags: object) -> set[str]:
    if not isinstance(raw_tags, list):
        return set()
    return {str(tag).strip().lower() for tag in raw_tags if str(tag).strip()}


def _split_repo(repo: str) -> tuple[str, str]:
    if "/" in repo:
        owner, short_name = repo.split("/", 1)
        return owner, short_name
    return "huggingface", repo


def _humanize_model_name(value: str) -> str:
    text = re.sub(r"[_\-]+", " ", value).strip()
    return re.sub(r"\s+", " ", text)


def _derive_install_name(repo: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", repo.replace("/", "-")).strip("-").lower()


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _hf_headers() -> dict[str, str]:
    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
    )
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}
