# SPDX-License-Identifier: MIT
"""Model-specific ASUS FX608JPR ACPI workaround tooling."""


def require(condition, message):
    """Safety checks must remain enabled even under python -O."""
    if not condition:
        raise ValueError(message)
