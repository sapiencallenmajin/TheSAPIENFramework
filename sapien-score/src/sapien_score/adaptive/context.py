"""Build attacker context from conversation history for adaptive mode."""


def summarize_early_turns(messages: list[dict[str, str]]) -> str:
    """Compress early conversation messages to save context window.

    Keeps the first and last message of each user/assistant exchange,
    replacing the middle with a marker.

    Parameters
    ----------
    messages : list[dict]
        The early portion of conversation history to summarize.

    Returns
    -------
    str
        A compressed textual summary of the early conversation.
    """
    if not messages:
        return ""

    lines = []
    lines.append(_format_message(messages[0]))
    if len(messages) > 2:
        lines.append("[... earlier exchanges omitted ...]")
    if len(messages) > 1:
        lines.append(_format_message(messages[-1]))
    return "\n\n".join(lines)


def _format_message(msg: dict[str, str]) -> str:
    """Format a single message as YOU/ASSISTANT label."""
    label = "YOU" if msg["role"] == "user" else "ASSISTANT"
    return f"{label}: {msg['content']}"


def build_attacker_context(
    conversation_history: list[dict[str, str]],
    scenario: dict,
) -> str:
    """Format conversation history as context for the attacker LLM.

    Parameters
    ----------
    conversation_history : list[dict]
        Full conversation so far, each item ``{"role": str, "content": str}``.
    scenario : dict
        The scenario being executed (used for domain context).

    Returns
    -------
    str
        Formatted context string ending with a generation instruction.
    """
    if not conversation_history:
        return "The conversation has not started yet.\n\nGenerate your opening message."

    # If > 20 messages (10 exchanges), summarize early turns
    if len(conversation_history) > 20:
        # Keep last 12 messages (6 turns / 3 exchanges) in full
        early = conversation_history[:-12]
        recent = conversation_history[-12:]

        early_summary = summarize_early_turns(early)

        recent_lines = [_format_message(m) for m in recent]

        parts = [
            "## EARLIER IN THE CONVERSATION (summarized)",
            early_summary,
            "",
            "## RECENT CONVERSATION (last 6 turns)",
            "\n\n".join(recent_lines),
        ]
    else:
        lines = [_format_message(m) for m in conversation_history]
        parts = [
            "## CONVERSATION SO FAR",
            "\n\n".join(lines),
        ]

    parts.append("")
    parts.append("Generate your next message.")

    return "\n".join(parts)
