from desktop_ai_agent.core.chat_client import ChatClient
from desktop_ai_agent.core.core_router import CoreRouter
from desktop_ai_agent.core.folder_index import (
    ProgramCatalog,
    list_programs,
    resolve_path,
    resolve_program,
    search_programs,
)
from desktop_ai_agent.core.ollama_service import OllamaService
from desktop_ai_agent.core.openrouter_client import OpenRouterClient
from desktop_ai_agent.core.session_setup import SessionSetup

__all__ = [
    "ChatClient",
    "CoreRouter",
    "OllamaService",
    "OpenRouterClient",
    "ProgramCatalog",
    "SessionSetup",
    "list_programs",
    "resolve_path",
    "resolve_program",
    "search_programs",
]
