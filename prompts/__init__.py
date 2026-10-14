"""System prompts and step instructions.

System prompt per persona: review_rules.md followed by the persona file. Step instructions: one file per review step.
`{claude}` / `{gpt}` in every file are replaced by the AI names.
"""

from pathlib import Path

from models import AI_NAMES

PROMPT_DIR = Path(__file__).parent
STEPS = ("analysis", "critique", "final", "followup")


def load_prompts(prompt_dir: Path = PROMPT_DIR) -> dict[str, str]:
    def read(name: str) -> str:
        text = (prompt_dir / f"{name}.md").read_text().strip()
        for persona, ai_name in AI_NAMES.items():
            text = text.replace(f"{{{persona}}}", ai_name)
        return text

    rules = read("review_rules")
    return {persona: f"{rules}\n\n{read(persona)}" for persona in AI_NAMES} | {step: read(step) for step in STEPS}
