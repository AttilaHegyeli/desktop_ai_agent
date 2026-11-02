"""Wire layers; no business logic.

Pipeline (when implemented): Input → Orchestrator → Core AI → Action → Output.
Today only Core + UI setup launcher are active.
"""

from __future__ import annotations


def main() -> int:
    # Must run before Tk creates its window: otherwise Windows may report scaled
    # coordinates and mouse positions would be off on displays above 100% scale.
    from desktop_ai_agent.core.monitors import enable_dpi_awareness

    enable_dpi_awareness()

    from desktop_ai_agent.ui.launcher import LauncherApp

    LauncherApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())