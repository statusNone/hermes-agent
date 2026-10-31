"""Opt-in terminal admission for required context engines.

Legacy selection stays fail-open. The bounded critical envelope is text-only
OpenAI Chat Completions; unsupported routes fail closed rather than guessing.
"""
import json
from contextlib import nullcontext


class ContextAdmissionError(RuntimeError):
    """A local refusal, never eligible for provider retry or fallback."""


def select_required(agent, messages, conversation, incoming):
    from hermes_cli.plugin_choices import bind_asker, clarify_asker
    engine = agent.context_compressor
    callback = getattr(agent, "clarify_callback", None)
    binding = bind_asker(clarify_asker(callback)) if callback else nullcontext()
    try:
        with binding:
            selected = engine.select_context(
                messages, conversation_messages=conversation,
                incoming_message=incoming, budget_tokens=engine.context_length,
            )
        if not isinstance(selected, list) or not selected or not all(isinstance(m, dict) for m in selected):
            raise ValueError("required engine returned no valid selection")
        if getattr(agent, "_interrupt_requested", False):
            raise ValueError("selection cancelled")
        return selected
    except Exception as exc:
        raise ContextAdmissionError(
            "Context admission refused; no answer request sent. Wait for indexing, "
            "reduce the current input, or repair the required engine before retrying. " + str(exc)
        ) from exc


def admit_request(agent, payload):
    """Check the resolved request after middleware, on every physical dispatch path.

    UTF-8 JSON bytes upper-bound controlled text tokens, with a framing margin
    and the actual output limit. This is not arbitrary-provider certification.
    """
    if not getattr(agent, "_context_engine_required", False):
        return
    if getattr(agent, "_interrupt_requested", False):
        raise ContextAdmissionError("Context admission refused: cancelled before dispatch.")
    if agent.provider not in {"openai", "custom"} or agent.api_mode != "chat_completions":
        raise ContextAdmissionError(f"Critical context supports only OpenAI text Chat Completions, got {agent.provider}/{agent.api_mode}.")
    payload = {k: v for k, v in payload.items() if k != 'timeout'}  # SDK transport option, not JSON
    if 'reasoning_effort' in payload and payload['reasoning_effort'] != 'none':
        raise ContextAdmissionError('Critical context does not support enabled reasoning')
    allowed = {"model", "messages", "max_tokens", "max_completion_tokens", "temperature", "top_p", "stream", "stream_options", "reasoning_effort"}
    if set(payload) - allowed:
        raise ContextAdmissionError("Critical context: unsupported request fields " + repr(sorted(set(payload) - allowed)) + "; no send.")
    messages = payload.get("messages", [])
    if not messages or any(m.get("role") not in {"system", "user", "assistant"}
            or not isinstance(m.get("content"), str) or set(m) - {"role", "content"} for m in messages):
        raise ContextAdmissionError("Critical context: only plain text messages are supported.")
    output = payload.get("max_completion_tokens", payload.get("max_tokens"))
    if type(output) is not int or output <= 0:
        raise ContextAdmissionError("Critical context requires an explicit positive output limit.")
    engine = agent.context_compressor
    ceiling = min(engine.context_length, getattr(engine, "admission_ceiling", engine.context_length))
    measured = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    # Explicit per-message framing plus fixed SDK envelope allowance. No tools,
    # media, reasoning, extra bodies or alternate adapters enter this envelope.
    overhead = 1024 + 32 * len(messages)
    if measured + output + overhead > ceiling:
        raise ContextAdmissionError("Context admission refused: serialized request plus output exceeds hard ceiling.")
    agent._context_admission_receipt = dict(request_bytes=measured, output=output,
                                            overhead=overhead, ceiling=ceiling)
