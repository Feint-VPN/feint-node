"""Persist the operator-editable subscription label independently of startup config."""

from utils.settings import Settings


def update_template(settings: Settings, template: str) -> None:
    normalized = template.strip()
    if not normalized:
        raise ValueError("SUB_URI_TEMPLATE cannot be empty")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("SUB_URI_TEMPLATE must be a single line")
    try:
        normalized.format(protocol="vless", Protocol="VLESS", username="demo")
    except KeyError as exc:
        raise ValueError(
            f"Unsupported placeholder '{{{exc.args[0]}}}'. Allowed: {{protocol}}, {{Protocol}}, {{username}}"
        ) from exc
    except ValueError as exc:
        raise ValueError(f"Invalid SUB_URI_TEMPLATE: {exc}") from exc

    path = settings.ENV_FILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replacement = f"SUB_URI_TEMPLATE={normalized}"
    for index, line in enumerate(lines):
        if line.startswith("SUB_URI_TEMPLATE="):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    settings.SUB_URI_TEMPLATE = normalized
