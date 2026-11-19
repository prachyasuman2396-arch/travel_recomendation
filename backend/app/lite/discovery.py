"""Find out which Gemini / Groq models a key can actually use — so a pasted key just works.

Model names change every few months and differ per account/region. Instead of trusting a hard-coded
ID, we list the models the key can see and pick the first one from a preference list (newest first),
falling back to the best-looking match. One cheap GET per provider; results are cached by the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from app.core.errors import ExternalServiceError
from app.core.http import send

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GROQ_BASE = "https://api.groq.com/openai/v1"

# Newest first. Anything not listed is still considered via the heuristic below.
GEMINI_TEXT_PREFERENCE = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                          "gemini-3-flash", "gemini-2.5-flash")
GEMINI_EMBED_PREFERENCE = ("gemini-embedding-001", "gemini-embedding-2", "text-embedding-004")
# Groq: gpt-oss models support strict JSON Schema ("Structured Outputs"); others use JSON-object mode.
GROQ_BIG_PREFERENCE = ("openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct", "llama-3.3-70b-versatile",
                       "meta-llama/llama-4-maverick-17b-128e-instruct")
GROQ_FAST_PREFERENCE = ("openai/gpt-oss-20b", "llama-3.1-8b-instant", "meta-llama/llama-4-scout-17b-16e-instruct",
                        "openai/gpt-oss-120b")
GROQ_STRICT_SCHEMA = ()  # disabled — use json_object mode to avoid strict schema validation errors
_EXCLUDE = re.compile(r"tts|live|image|audio|transcribe|vision|embedding|aqa|guard|whisper|omni|robotics|"
                      r"computer-use|native|preview-tts|translate|lyria|veo|imagen|compound|playai|orpheus",
                      re.IGNORECASE)


@dataclass
class ProviderStatus:
    provider: str
    ok: bool
    message: str
    text_model: str | None = None  # Gemini: main model; Groq: big model
    fast_model: str | None = None  # Groq only
    embed_model: str | None = None  # Gemini only
    available: list[str] = field(default_factory=list)

    def structured_mode(self, model: str | None) -> str:
        return "plain_text"  # all models use plain-text JSON mode (json_object/json_schema modes are unreliable)


def _pick(available: list[str], preference: tuple[str, ...]) -> str | None:
    for name in preference:
        if name in available:
            return name
    return None


def _version_key(name: str) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", name)[:3]]
    return tuple(nums) + (0,) * (3 - len(nums))


def _key_problem(exc: ExternalServiceError, provider: str) -> str:
    if exc.status in (400, 401, 403):
        return f"{provider} rejected this API key (HTTP {exc.status}). Check that it was copied completely."
    if exc.status == 429:
        return f"{provider} is rate-limiting this key right now; it will be retried on use."
    return f"Could not reach {provider}: {exc}"


async def discover_gemini(api_key: str, http: httpx.AsyncClient) -> ProviderStatus:
    names: list[str] = []
    embed: list[str] = []
    token: str | None = None
    try:
        for _ in range(5):  # paginated list
            params = {"pageSize": 1000, **({"pageToken": token} if token else {})}
            request = http.build_request("GET", f"{GEMINI_BASE}/models", params=params,
                                         headers={"x-goog-api-key": api_key})
            data = (await send(http, request, "gemini")).json()
            for m in data.get("models", []):
                name = str(m.get("name", "")).removeprefix("models/")
                methods = m.get("supportedGenerationMethods") or []
                if "generateContent" in methods:
                    names.append(name)
                if "embedContent" in methods or "batchEmbedContents" in methods:
                    embed.append(name)
            token = data.get("nextPageToken")
            if not token:
                break
    except ExternalServiceError as exc:
        if exc.status == 429:  # key is probably fine; fall back to defaults
            return ProviderStatus("gemini", True, _key_problem(exc, "Gemini"), GEMINI_TEXT_PREFERENCE[0],
                                  embed_model=GEMINI_EMBED_PREFERENCE[0])
        return ProviderStatus("gemini", False, _key_problem(exc, "Gemini"))
    text = _pick(names, GEMINI_TEXT_PREFERENCE)
    if text is None:
        flash = sorted((n for n in names if "flash" in n and "lite" not in n and not _EXCLUDE.search(n)),
                       key=lambda n: ("preview" in n or "exp" in n, tuple(-x for x in _version_key(n))))
        text = flash[0] if flash else next((n for n in names if not _EXCLUDE.search(n)), None)
    emb = _pick(embed, GEMINI_EMBED_PREFERENCE) or (embed[0] if embed else None)
    if text is None:
        return ProviderStatus("gemini", False, "This Gemini key has no text model available.", available=names)
    return ProviderStatus("gemini", True, f"Gemini ready ({text})", text_model=text, embed_model=emb,
                          available=names)


async def discover_groq(api_key: str, http: httpx.AsyncClient) -> ProviderStatus:
    try:
        request = http.build_request("GET", f"{GROQ_BASE}/models", headers={"Authorization": f"Bearer {api_key}"})
        data = (await send(http, request, "groq")).json()
    except ExternalServiceError as exc:
        if exc.status == 429:
            return ProviderStatus("groq", True, _key_problem(exc, "Groq"), GROQ_BIG_PREFERENCE[0],
                                  fast_model=GROQ_FAST_PREFERENCE[0])
        return ProviderStatus("groq", False, _key_problem(exc, "Groq"))
    names = [str(m.get("id")) for m in data.get("data", []) if m.get("active", True) and m.get("id")]
    chat = [n for n in names if not _EXCLUDE.search(n)]
    big = _pick(chat, GROQ_BIG_PREFERENCE) or (chat[0] if chat else None)
    fast = _pick(chat, GROQ_FAST_PREFERENCE) or big
    if big is None:
        return ProviderStatus("groq", False, "This Groq key has no chat model available.", available=names)
    return ProviderStatus("groq", True, f"Groq ready ({fast} / {big})", text_model=big, fast_model=fast,
                          available=names)
