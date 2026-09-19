"""Match Read Aloud fragments only against a registered generated display.

Open WebUI 0.11.3 removes Markdown, splits at newlines/punctuation and merges
short parts. Retain annotated sentence atoms so any of its three groupings can
be resolved without guessing whether an arbitrary title is diagnostic text.
The browser acceptance executes the pinned upstream source-map implementation.
"""

import re
import unicodedata


def _without_formatting(text: str) -> str:
    # The order follows pinned removeFormattings(). Emoji removal affects only
    # comparison keys below; the original answer remains available for speech.
    replacements = [
        (r"```[\s\S]*?```", ""), (r"(?m)^\|.*\|$", ""),
        (r"(?:\*\*|__)(.*?)(?:\*\*|__)", r"\1"),
        (r"(?:[*_])(.*?)(?:[*_])", r"\1"), (r"~~(.*?)~~", r"\1"),
        (r"`([^`]+)`", r"\1"),
        (r"!?\[([^\]]*)\](?:\([^)]+\)|\[[^\]]*\])", r"\1"),
        (r"(?m)^\[[^\]]+\]:\s*.*$", ""), (r"(?m)^#{1,6}\s+", ""),
        (r"(?m)^\s*[-*+]\s+", ""), (r"(?m)^\s*\d+\.\s+", ""),
        (r"(?m)^\s*>[> ]*", ""), (r"(?m)^\s*:\s+", ""),
        (r"\[\^[^\]]*\]", ""), (r"\n{2,}", "\n"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return text


def comparison_text(text: str) -> str:
    # The client strips RGI emoji. Folding symbol/variation characters on both
    # sides makes matching independent of its Unicode version, never changes
    # spoken answers, and does not introduce any global title/keyword blacklist.
    text = re.sub(r"[#*0-9]\ufe0f?\u20e3", "", text)
    text = "".join(char for char in text if unicodedata.category(char) not in {"So", "Sk", "Cf", "Mn"})
    return " ".join(text.split())


def display_atoms(display: str, answer: str) -> list[tuple[str, str]]:
    start = 0
    if not display.startswith(answer) and display.startswith("**Working**\n") and "\n---\n\n" in display:
        start = display.index("\n---\n\n") + len("\n---\n\n")
    start = display.find(answer, start) if answer else len(display)
    if start < 0:
        return []
    regions = [(display[:start], False), (answer, True), (display[start + len(answer):], False)]
    atoms = []
    for content, is_answer in regions:
        content = re.sub(r"<details[^>]*>[\s\S]*?</details>", "", content, flags=re.IGNORECASE)
        # Code blocks are omitted by cleanText and must not create atoms.
        content = re.sub(r"```[\s\S]*?```", "", content)
        for part in re.split(r"(?<=[.!?])\s+|\n+", content):
            cleaned = _without_formatting(part.strip())
            key = comparison_text(cleaned)
            if key:
                atoms.append((key, cleaned if is_answer else ""))
    return atoms


def resolve_part(text: str, atoms: list[tuple[str, str]]) -> str | None:
    key = comparison_text(text)
    if not key:
        return None
    silent_match = False
    for start in range(len(atoms)):
        candidate = ""
        answer_parts = []
        for index in range(start, len(atoms)):
            atom, spoken = atoms[index]
            candidate += (" " if candidate else "") + atom
            if spoken:
                answer_parts.append(spoken)
            if candidate == key:
                if answer_parts:
                    return " ".join(answer_parts)
                silent_match = True
                break
            if not key.startswith(candidate):
                break
    return "" if silent_match else None
