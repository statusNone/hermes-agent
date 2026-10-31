"""Typed selection veto for required engines; provider budgets belong to Hermes."""
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
    """Keep cancellation terminal on retries without imposing a wire envelope."""
    if not getattr(agent, "_context_engine_required", False):
        return
    if getattr(agent, "_interrupt_requested", False):
        raise ContextAdmissionError("Context admission refused: cancelled before dispatch.")
