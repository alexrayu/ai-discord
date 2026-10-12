"""System prompts: shared group-chat rules followed by the persona file.

`{claude}` / `{gpt}` in shared_rules.md are replaced by the AI names.
"""

from pathlib import Path

from transcript import AI_NAMES

PROMPT_DIR = Path(__file__).parent


def load_prompts(prompt_dir: Path = PROMPT_DIR) -> dict[str, str]:
    shared = (prompt_dir / "shared_rules.md").read_text().strip()
    for persona, name in AI_NAMES.items():
        shared = shared.replace(f"{{{persona}}}", name)
    return {
        persona: f"{shared}\n\n{(prompt_dir / f'{persona}.md').read_text().strip()}"
        for persona in AI_NAMES
    }
