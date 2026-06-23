"""
strategy_lab – Roadmap für die historische Strategie-Suche (Phase A).

Härtet das *mechanische* Gerüst (Entry-Technik, Risiko, Regime) über lange
Historie. WICHTIG: kann die eigentliche News-Sentiment-Alpha des Bots NICHT
backtesten (keine Dekaden-Historie von Claude-Urteilen) – siehe README im
Modul-Docstring von strategy_lab.lab.

Phasen:
  1. Datenschicht + Dekaden-Backtest der einen bestehenden Strategie (HIER).
  2. mehrere Strategie-Familien + Parameter-Grids.
  3. Walk-Forward-Selektion + Robustheits-Ranking (Anti-Overfit-Herz).
  4. Survivorship-Registry + Regime-Tagging.
  5. Meta-Allokator → speist Gewinner additiv in den Bot.
"""
from strategy_lab.strategies import Strategy, REGISTRY, register, get, all_names
# Side-effect-Import: registriert die Strategie-Familien (Phase 2).
import strategy_lab.families  # noqa: F401,E402

__all__ = ["Strategy", "REGISTRY", "register", "get", "all_names"]
