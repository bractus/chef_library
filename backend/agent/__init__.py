"""O chef que le os trechos recuperados, pesquisa na web quando precisa e responde.

`chef` fala com o modelo; `prompts` guarda o que ele deve dizer; `memory`
guarda as ultimas trocas de cada conversa (efemera).
"""
from .chef import ask_stream, standalone_query
from .memory import MEMORY
from .prompts import build_context

__all__ = ["MEMORY", "ask_stream", "build_context", "standalone_query"]
