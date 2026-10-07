"""Fills in the gallery of local/Home Assistant examples from the same file
the tests run them from, so the docs only ever show combinations that work."""

from pathlib import Path

import yaml

MARKER = "<!-- blended-examples -->"
EXAMPLES = Path(__file__).parent.parent / "tests" / "blended_examples.yaml"
WHERE = {"local": "locally", "server": "inside Home Assistant"}


def render() -> str:
    supported, refused = [], []
    for example in yaml.safe_load(EXAMPLES.read_text(encoding="utf8")):
        code = example["code"].rstrip("\n")
        parts = [f"#### {example['title']}", example["about"].strip()]
        parts.append(f"```python\n{code}\n```")
        if example.get("refused"):
            refused.append("\n\n".join(parts))
            continue
        steps = ", then ".join(WHERE[side] for side in example["runs"])
        parts.append(f"Runs {steps}.")
        supported.append("\n\n".join(parts))
    return "\n\n".join([
        *supported,
        "### Not Supported",
        "The shell refuses these with an explanation, rather than running them.",
        *refused,
    ])


def on_page_markdown(markdown, page, config, files):
    return markdown.replace(MARKER, render()) if MARKER in markdown else markdown
