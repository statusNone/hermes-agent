"""Cache-only native-output accounting for the required text request envelope.

No catalog guesses, live probes, output-field injection, or provider expansion.
"""
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class NativeOutputAllowance:
    provider: str
    base_url: str
    model: str
    context_length: int
    output_tokens: int


def resolve_native_output(provider, api_mode, base_url, model, api_key=""):
    """Resolve an exact endpoint/model from supported fresh Hermes metadata.

    Local endpoints have only credential-scoped in-process metadata; Hermes
    deliberately does not persist their catalogs. Missing metadata is a refusal,
    never permission to use a default, a name-matched catalog, or the input window.
    The returned immutable allowance is pinned for this launch, like its route.
    """
    from agent import model_metadata as mm
    from agent.context_admission import ContextAdmissionError

    if provider not in {"openai", "custom"} or api_mode != "chat_completions":
        raise ContextAdmissionError("Native output requires the existing text Chat Completions route.")
    normalized = mm._normalize_base_url(base_url)
    key = mm._endpoint_memo_key(normalized, api_key)
    age = time.time() - mm._endpoint_model_metadata_cache_time.get(key, 0)
    models = mm._endpoint_model_metadata_cache.get(key) if 0 <= age < mm._ENDPOINT_MODEL_CACHE_TTL else None
    if models is None and not mm.is_local_endpoint(normalized):
        models = mm._endpoint_disk_cache_get(normalized)
    entry = models.get(model) if isinstance(models, dict) else None
    if isinstance(entry, dict) and entry.get("id") == model:
        output = entry.get("max_completion_tokens")
        context = entry.get("context_length")
        if type(output) is int and type(context) is int and 0 < output < context:
            return NativeOutputAllowance(provider, normalized, model, context, output)
    raise ContextAdmissionError(
        "Native output admission requires fresh exact-endpoint/model metadata with "
        "context_length and a positive native max_completion_tokens ceiling. "
        "No trustworthy cached ceiling is available; obtain supported model metadata "
        "before launch. No metadata probe, guessed reserve, or answer request was made."
    )


def native_output_for_request(agent, payload):
    """Validate the pinned allowance against the final physical request route."""
    from agent.context_admission import ContextAdmissionError
    from agent.model_metadata import _normalize_base_url

    allowance = getattr(agent, "_context_native_output", None)
    if not isinstance(allowance, NativeOutputAllowance):
        raise ContextAdmissionError("Critical context requires an explicit output limit or resolved native output metadata.")
    if (agent.provider, _normalize_base_url(agent.base_url), payload.get("model")) != (
            allowance.provider, allowance.base_url, allowance.model):
        raise ContextAdmissionError("Native output metadata does not match the final request route/model.")
    if "max_tokens" in payload or "max_completion_tokens" in payload:
        raise ContextAdmissionError("Native output mode refuses an application-imposed output field.")
    return allowance
