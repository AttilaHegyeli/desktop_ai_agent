from __future__ import annotations


def main() -> int:
    from desktop_ai_agent.ui.launcher import LauncherApp

    LauncherApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
