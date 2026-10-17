"""Program Files path index + search tool for the desktop agent."""

from desktop_ai_agent.core.folder_index.catalog import ProgramCatalog
from desktop_ai_agent.core.folder_index.indexer import index_program_files
from desktop_ai_agent.core.folder_index.tool import (
    list_programs,
    programs_as_text,
    resolve_path,
    resolve_program,
    search_programs,
)

__all__ = [
    "ProgramCatalog",
    "index_program_files",
    "list_programs",
    "programs_as_text",
    "resolve_path",
    "resolve_program",
    "search_programs",
]
