"""Subscription label persistence; no validation or process-config mutation."""

from pathlib import Path

from contracts.settings import SubscriptionSettings


class SubscriptionSettingsStore(SubscriptionSettings):
    def __init__(self, path: Path) -> None:
        self.path = path

    def save_template(self, template: str) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        replacement = f"SUB_URI_TEMPLATE={template}"
        for index, line in enumerate(lines):
            if line.startswith("SUB_URI_TEMPLATE="):
                lines[index] = replacement
                break
        else:
            lines.append(replacement)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
