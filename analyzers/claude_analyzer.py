"""Rückwärtskompat-Shim.

Dieses Modul hieß früher ``analyzers.claude_analyzer``. Da der Bot je nach
``LLM_PROVIDER`` mit Claude *oder* Gemini analysiert, ist der Code nach
``analyzers.llm_analyzer`` gewandert (Klasse ``ClaudeAnalyzer`` -> ``LLMAnalyzer``).

Der alte Importpfad bleibt gültig, damit persistierte Decision-Provenance und
etwaige externe Aufrufer nicht brechen. Neuer Code sollte ``analyzers.llm_analyzer``
importieren.
"""

from analyzers.llm_analyzer import *  # noqa: F401,F403
from analyzers.llm_analyzer import (  # noqa: F401  (explizit, u.a. für `import name`-Zugriff)
    AnalysisResult,
    ClaudeAnalyzer,
    LLMAnalyzer,
    NewsTrustFilter,
    _stamp_route,
    _SYSTEM_PROMPT,
    _USER_TEMPLATE_CRYPTO,
    _USER_TEMPLATE_STANDARD,
    _USER_TEMPLATE_THESIS_CHECK,
)
