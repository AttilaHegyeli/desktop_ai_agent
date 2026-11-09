"""Wire layers; no business logic.



Pipeline: Input → Orchestrator → Core AI → Action → Output.

"""



from __future__ import annotations





def main() -> int:

    # Every launch: install / refresh runtime deps (no requirements.txt).

    from desktop_ai_agent.bootstrap_deps import ensure_dependencies



    ensure_dependencies(log=print)



    # Load OPENROUTER_API_KEY from repo-root .env before UI/config reads it.

    from desktop_ai_agent.core.config_loader import load_dotenv



    load_dotenv()



    # Must run before Tk creates its window: otherwise Windows may report scaled

    # coordinates and mouse positions would be off on displays above 100% scale.

    from desktop_ai_agent.core.monitors import enable_dpi_awareness



    enable_dpi_awareness()



    from desktop_ai_agent.ui.launcher import LauncherApp



    LauncherApp().run()

    return 0





if __name__ == "__main__":

    raise SystemExit(main())


