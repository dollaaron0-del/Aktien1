"""
AnalysisCache – speichert das letzte Claude-Analyse-Ergebnis pro Ticker.

Wird vom TradingView-Executor als Bestätigungs-Filter genutzt:
Ein TradingView-BUY-Signal wird nur ausgeführt wenn das letzte
bekannte Sentiment für den Ticker nicht bärisch ist.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from typing import Optional, Dict

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "analysis_cache.json")
_MAX_AGE_HOURS = 24  # Analysen älter als 24h werden ignoriert


class AnalysisCache:

    def store(self, ticker: str, direction: str, sentiment_score: float,
              confidence: str, recommendation: str) -> None:
        """Speichert das aktuelle Analyse-Ergebnis für einen Ticker.

        Schutz gegen das stille Überschreiben echter Kaufsignale: Wenn die lokale
        Engine (Ollama) ins Timeout läuft UND Claude budgetgesperrt ist, liefert
        der Analyzer ein leeres Platzhalter-Ergebnis (Score ~0.5, NEUTRAL, LOW,
        SKIP/HOLD). So ein Nicht-Signal darf einen erst kürzlich erzeugten echten
        BUY NICHT verdrängen (sonst geht das Signal vor der Ausführung verloren –
        vgl. ASML 18.6.). In dem Fall bleibt der bestehende BUY erhalten."""
        key = ticker.upper()
        data = self._load()
        if self._is_no_signal(direction, sentiment_score, confidence, recommendation):
            prev = data.get(key)
            if prev and prev.get("recommendation") == "BUY" and self._is_fresh(prev):
                return  # frischen echten BUY nicht mit leerem SKIP überschreiben
        data[key] = {
            "direction":       direction,
            "sentiment_score": sentiment_score,
            "confidence":      confidence,
            "recommendation":  recommendation,
            "updated_at":      datetime.utcnow().isoformat(),
        }
        self._save(data)

    @staticmethod
    def _is_no_signal(direction: str, score: float, confidence: str,
                      recommendation: str) -> bool:
        """True für das leere Fallback-Ergebnis (Ollama-Timeout + Claude gesperrt):
        neutrale Richtung, niedrige Konviktion, ~0.5-Score, kein Kauf."""
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.5
        return (
            (recommendation or "").upper() in ("SKIP", "HOLD")
            and (confidence or "").upper() == "LOW"
            and (direction or "").upper() == "NEUTRAL"
            and 0.43 <= score <= 0.57
        )

    def _is_fresh(self, entry: Dict) -> bool:
        try:
            age = datetime.utcnow() - datetime.fromisoformat(entry["updated_at"])
            return age <= timedelta(hours=_MAX_AGE_HOURS)
        except Exception:
            return False

    def get(self, ticker: str) -> Optional[Dict]:
        """Gibt das letzte Analyse-Ergebnis zurück, wenn es < 24h alt ist."""
        data = self._load()
        entry = data.get(ticker.upper())
        if not entry:
            return None
        try:
            age = datetime.utcnow() - datetime.fromisoformat(entry["updated_at"])
            if age > timedelta(hours=_MAX_AGE_HOURS):
                return None
        except Exception:
            return None
        return entry

    def is_buy_confirmed(self, ticker: str) -> tuple[bool, str]:
        """
        Prüft ob das letzte Sentiment einen TradingView-BUY bestätigt.
        Returns (allowed, reason).
        """
        entry = self.get(ticker)
        if not entry:
            return True, ""  # Kein Datenpunkt → nicht blockieren

        direction = entry.get("direction", "NEUTRAL")
        score     = entry.get("sentiment_score", 0.5)
        conf      = entry.get("confidence", "LOW")

        if direction == "BEARISH" and conf != "LOW":
            return False, f"Sentiment bärisch ({score:.2f}, {conf}) – TV-Signal blockiert"
        if score < 0.35:
            return False, f"Sentiment-Score zu niedrig ({score:.2f}) – TV-Signal blockiert"
        return True, ""

    def _load(self) -> Dict:
        try:
            with open(_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data: Dict) -> None:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=os.path.dirname(_FILE), suffix=".tmp", delete=False
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, _FILE)
