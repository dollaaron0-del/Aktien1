from dataclasses import dataclass, field
from typing import List
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _parse_exchanges(raw: str) -> List[str]:
    known = {"XETRA", "NYSE", "NASDAQ", "TSE", "HKEX", "SSE", "LSE", "ASX"}
    return [e.strip().upper() for e in raw.split(",") if e.strip().upper() in known] or ["XETRA", "NYSE", "TSE"]


@dataclass
class Config:
    # API Keys
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    newsapi_key: str = field(default_factory=lambda: os.getenv("NEWSAPI_KEY", ""))
    twitter_bearer_token: str = field(default_factory=lambda: os.getenv("TWITTER_BEARER_TOKEN", ""))
    quiver_api_key: str = field(default_factory=lambda: os.getenv("QUIVER_API_KEY", ""))

    # ── Abgeschaltete Collector-Quellen ──────────────────────────────────────
    # Kill-Switch: Quellen per Namen (Key im Collector-Hub, bot/runner.py)
    # deaktivieren, ohne Code anzufassen. COLLECTORS_DISABLED=Komma-Liste.
    # Historie: reddit/patents/earn_transcripts/aaii_sentiment waren hier
    # per Default abgeschaltet (N3-Befund 5.7.2026: Endpoints tot bzw. 403
    # für Datacenter-IPs) und wurden im Juli 2026 komplett entfernt.
    collectors_disabled: List[str] = field(
        default_factory=lambda: [
            s.strip().lower()
            for s in os.getenv("COLLECTORS_DISABLED", "").split(",")
            if s.strip()
        ]
    )

    # ── Intraday-Scan (drittes optionales Analysefenster) ────────────────────
    # Aktivieren: INTRADAY_SCAN_ENABLED=true in .env
    # Empfehlung: 17:30 UTC = 19:30 MESZ (während der US-Session)
    # Scannt die volle Watchlist + BenchList ein drittes Mal pro Tag.
    intraday_scan_enabled: bool = field(
        default_factory=lambda: os.getenv("INTRADAY_SCAN_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    intraday_scan_time: str = field(
        default_factory=lambda: os.getenv("INTRADAY_SCAN_TIME", "17:30")
    )
    # ── Quiet-Mode (Telegram-Ruhe) ───────────────────────────────────────────
    # true = Hintergrund-Scanner senden KEINE Routine-Nachrichten mehr (nur Log).
    # Hörbar bleiben: die geplanten Analysen, der Abend-Digest, Trades/SL und
    # Fehler. Drastisch weniger Telegram-Lärm. Abschalten: QUIET_MODE=false.
    quiet_mode: bool = field(
        default_factory=lambda: os.getenv("QUIET_MODE", "true").lower() in ("1", "true", "yes")
    )
    # Interactive Brokers (TWS / IB Gateway)
    ibkr_host:      str = field(default_factory=lambda: os.getenv("IBKR_HOST",      "127.0.0.1"))
    ibkr_port:      int = field(default_factory=lambda: int(os.getenv("IBKR_PORT",  "7497")))
    ibkr_client_id: int = field(default_factory=lambda: int(os.getenv("IBKR_CLIENT_ID", "1")))
    ibkr_account:   str = field(default_factory=lambda: os.getenv("IBKR_ACCOUNT",   ""))

    # Broker mode: "paper", "ibkr"
    broker_mode: str = field(default_factory=lambda: os.getenv("BROKER_MODE", "paper"))
    initial_capital: float = field(default_factory=lambda: float(os.getenv("INITIAL_CAPITAL", "10000.0")))

    # Watchlist – aus .env lesen (WATCHLIST=AAPL,MSFT,NVDA,...) oder Standardliste
    watchlist: List[str] = field(default_factory=lambda: (
        [t.strip().upper() for t in os.getenv("WATCHLIST", "").split(",") if t.strip()]
        or ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]
    ))

    # Risk management
    max_position_pct: float = 0.20
    stop_loss_pct: float = 0.07
    take_profit_pct: float = 0.20

    # ── Konviction-Sizing & Liquiditäts-Steuerung (22.7.2026) ────────────────
    # Harte Obergrenze für EINE Position (% der Gesamt-Equity). Konvictions-
    # starke Picks dürfen bis hierher wachsen; darüber wird gekappt.
    max_single_position_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_SINGLE_POSITION_PCT", "0.25"))
    )
    # Zusätzlicher Größen-Aufschlag bei starkem Sentiment-Überschuss über der
    # Kaufschwelle (0.6 = bis +60 % auf die Confidence-Basis).
    conviction_max_bonus: float = field(
        default_factory=lambda: float(os.getenv("CONVICTION_MAX_BONUS", "0.6"))
    )
    # Cash-Reserve: Soft-Boden, den der Bot normal frei hält (Handlungsfähigkeit).
    cash_reserve_pct: float = field(
        default_factory=lambda: float(os.getenv("CASH_RESERVE_PCT", "0.10"))
    )
    # Harter Boden: darunter kauft der Bot NIE, auch bei viel erwartetem Rückfluss.
    cash_reserve_hard_pct: float = field(
        default_factory=lambda: float(os.getenv("CASH_RESERVE_HARD_PCT", "0.05"))
    )
    # Rückfluss-Timing: rechnet Kapital ein, das binnen N Tagen aus Positionen
    # frei wird (Einstieg + target_hold_days). Aktiv → Bot lehnt sich bei nahem
    # Rückfluss weiter aus der Soft-Reserve (bis zum harten Boden).
    reflow_sizing_enabled: bool = field(
        default_factory=lambda: os.getenv("REFLOW_SIZING_ENABLED", "true").lower() == "true"
    )
    reflow_lookahead_days: int = field(
        default_factory=lambda: int(os.getenv("REFLOW_LOOKAHEAD_DAYS", "5"))
    )

    # Sentiment thresholds (0–1)
    buy_threshold: float = 0.65
    sell_threshold: float = 0.35
    # Extra-Schwelle wenn der Heimatmarkt einer Aktie noch geschlossen ist (Pre-Market-Kauf)
    pre_market_threshold_boost: float = field(
        default_factory=lambda: float(os.getenv("PRE_MARKET_THRESHOLD_BOOST", "0.05"))
    )
    min_sources: int = field(default_factory=lambda: int(os.getenv("MIN_SOURCES", "1")))

    # ── Kapitalknappheits-Schwelle (25.7.2026) ──────────────────────────────
    # Sinkt das frei verfügbare Cash (% der Equity), steigt die Kaufschwelle -
    # asymmetrisch wie der Makro-Aufschlag (nur strenger, nie lockerer).
    # Grund: eine Kaufwelle band Ende Juli das Kapital für 6+ Tage, weil auch
    # mittelmäßige Signale noch durchgingen, bis das Cash-Polster hart am
    # Reserve-Boden aufschlug und der Bot abrupt komplett handlungsunfähig
    # wurde. Diese Schwelle wirkt VORHER: je knapper das Cash, desto höher die
    # Latte, sodass nur noch die stärksten Signale das restliche Pulver bekommen.
    capital_scarcity_threshold_enabled: bool = field(
        default_factory=lambda: os.getenv("CAPITAL_SCARCITY_THRESHOLD_ENABLED", "true").lower() == "true"
    )
    # Ab diesem Cash-Anteil (% der Equity) gilt "genug Pulver" - Schwelle bleibt
    # unverändert (100 % Normalbetrieb im Sinne des Users).
    capital_scarcity_cash_pct_full: float = field(
        default_factory=lambda: float(os.getenv("CAPITAL_SCARCITY_CASH_PCT_FULL", "0.20"))
    )
    # Bei/unter diesem Cash-Anteil (nahe cash_reserve_hard_pct) greift der volle
    # Aufschlag - hier wird ohnehin kaum noch Positionsgröße zugelassen.
    capital_scarcity_cash_pct_empty: float = field(
        default_factory=lambda: float(os.getenv("CAPITAL_SCARCITY_CASH_PCT_EMPTY", "0.05"))
    )
    # Maximaler Aufschlag auf buy_threshold bei komplett knappem Cash (zwischen
    # full und empty interpoliert, Kurvenform via capital_scarcity_curve_exponent).
    capital_scarcity_max_adj: float = field(
        default_factory=lambda: float(os.getenv("CAPITAL_SCARCITY_MAX_ADJ", "0.15"))
    )
    # Kurvenform der Interpolation: 1.0 = linear (alter Verlauf), >1.0 macht sie
    # exponentiell/konvex - bei reichlich Cash bleibt die Latte fast unverändert
    # (flexibel), erst nahe am Boden zieht sie merklich an (klare Richtlinie am
    # Ende der Kurve statt gleichmäßigem Anstieg über die ganze Spanne).
    # 2.0 = quadratisch, 3.0 = spürbar steiler nur im letzten Drittel.
    capital_scarcity_curve_exponent: float = field(
        default_factory=lambda: float(os.getenv("CAPITAL_SCARCITY_CURVE_EXPONENT", "2.0"))
    )

    # ── Selbstlern-Filter (analyzers/entry_filter.py) ───────────────────────
    # Konsultiert das gelernte Kalibrierungsmodell (data/calibration.json) beim
    # Kauf. AVOID → SKIP, CAUTION → kleinere Position. Fail-open: fehlt das Modell
    # oder ist der Bucket zu dünn (NEUTRAL), passiert nichts.
    learning_filter_enabled: bool = field(
        default_factory=lambda: os.getenv("LEARNING_FILTER_ENABLED", "true").lower() == "true"
    )
    # AVOID blockt den Kauf? (False = nur CAUTION-Sizing, kein Veto)
    learning_filter_block: bool = field(
        default_factory=lambda: os.getenv("LEARNING_FILTER_BLOCK", "true").lower() == "true"
    )
    # Positionsgrößen-Faktor bei CAUTION (0.5 = halbe Größe).
    learning_filter_caution_size_mult: float = field(
        default_factory=lambda: float(os.getenv("LEARNING_FILTER_CAUTION_SIZE_MULT", "0.5"))
    )
    # Positionsgrößen-Faktor bei PROCEED (>1.0 = größere Position bei klar positiver
    # gelernter Kante). Default 1.0 = aus → der Filter gated nur nach unten. Auf z.B.
    # 1.25 setzen, um die gelernte Kante auch zweiseitig (nach oben) zu nutzen.
    learning_filter_proceed_size_mult: float = field(
        default_factory=lambda: float(os.getenv("LEARNING_FILTER_PROCEED_SIZE_MULT", "1.0"))
    )

    # ── RLAgent-Veto (analyzers/rl_agent.py) ────────────────────────────────
    # Separat trainierter linearer Policy-Agent (eigenes Lern-Paradigma, parallel
    # zum statistischen EntryFilter). should_buy(state) → (kaufen?, size_mod 0.5–1.25).
    # Default AUS: der Agent ist sonst zwar trainiert, beeinflusst aber keine
    # Entscheidung. Bei True wird er beim Kauf konsultiert (Veto + zweiseitiges Sizing)
    # UND der 24h-Trainer in main.py gestartet. Aus = kein Veto, kein Training.
    rl_veto_enabled: bool = field(
        default_factory=lambda: os.getenv("RL_VETO_ENABLED", "false").lower() == "true"
    )

    # ── Marktbasierter Analyse-Zeitplan ─────────────────────────────────────
    # Welche Börsen beobachten? Analyse läuft je 30 Min vor Börseneröffnung.
    # Verfügbare Codes: XETRA NYSE NASDAQ TSE HKEX SSE LSE ASX
    market_exchanges: List[str] = field(
        default_factory=lambda: _parse_exchanges(os.getenv("MARKET_EXCHANGES", "XETRA,NYSE,TSE"))
    )
    # Vorlauf in Minuten vor Börseneröffnung
    market_lead_minutes: int = field(
        default_factory=lambda: int(os.getenv("MARKET_LEAD_MINUTES", "30"))
    )

    # Signale in der Warteschlange: Ablauf nach N Stunden
    signal_queue_max_age_hours: int = field(
        default_factory=lambda: int(os.getenv("SIGNAL_QUEUE_MAX_AGE_HOURS", "48"))
    )

    # Claude model
    claude_model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
    )
    # Leichtes Modell für Nebenaufrufe (Thesis-/Exit-Check offener Positionen).
    # Haiku kostet ~1/3 von Sonnet bei In+Out – die finale Katalysator-Analyse
    # bleibt auf claude_model. Leer/"" → nutzt claude_model (kein Tiering).
    claude_model_light: str = field(
        default_factory=lambda: os.getenv("CLAUDE_MODEL_LIGHT", "claude-haiku-4-5-20251001")
    )
    # Prompt-Cache-Lebensdauer für das System-Prompt + den (pro Zyklus konstanten)
    # Makro/Geo-Kontext. "1h" hält den Cache über den ganzen Zyklus warm (sonst
    # läuft der 5-min-Default zwischen langsamen CPU-Tickern ab → Vollpreis).
    # Erlaubte Werte: "5m" | "1h".
    claude_cache_ttl: str = field(
        default_factory=lambda: os.getenv("CLAUDE_CACHE_TTL", "1h")
    )
    # Dedup: identische News (gleicher Fingerprint) für denselben Ticker werden
    # innerhalb dieser Spanne nicht erneut an Claude geschickt – das letzte
    # Vollergebnis wird wiederverwendet. 0 = aus.
    claude_result_cache_hours: float = field(
        default_factory=lambda: float(os.getenv("CLAUDE_RESULT_CACHE_HOURS", "6"))
    )

    # Telegram notifications (optional)
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    # "important" (Default): nur Trades, kritische Fehler und Tages-Digest
    # erreichen Telegram — alles andere landet im Log/Dashboard.
    # "all": altes Verhalten, jede Nachricht wird gesendet.
    telegram_mode: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_MODE", "important").lower()
    )
    # Basis-URL des Dashboards für Telegram-Rückverweise (Ausbau-Roadmap H5.3),
    # z.B. "http://localhost:8503". Wichtige Nachrichten bekommen damit einen
    # Deep-Link direkt zur passenden Stelle im Leitstand.
    # Leer = Feature AUS (bewusster Default): Das Dashboard hört nur auf
    # 127.0.0.1 und ist ausschließlich über den SSH-Tunnel erreichbar — ein
    # Link nützt nur, wenn der Tunnel gerade steht. Kein Default-Wert, damit
    # nie ein toter Link im Chat landet.
    dashboard_url: str = field(
        default_factory=lambda: os.getenv("DASHBOARD_URL", "").strip()
    )

    # Externer Dead-Man-Switch (Roadmap 1.7, optional): Ping-URL eines Diensts
    # wie healthchecks.io. Bleibt der Ping aus (Server/Netz down, Bot-Prozess
    # tot), alarmiert der Dienst selbst extern — ergänzt watchdog.sh, das ja
    # auf demselben Server läuft und bei Totalausfall selbst nicht mehr melden
    # kann. Leer = Feature aus (kein Default-Endpunkt, Anmeldung ist User-Sache).
    dead_man_switch_url: str = field(
        default_factory=lambda: os.getenv("DEAD_MAN_SWITCH_URL", "").strip()
    )
    dead_man_switch_interval_min: int = field(
        default_factory=lambda: int(os.getenv("DEAD_MAN_SWITCH_INTERVAL_MIN", "5"))
    )

    # Focus mode: WEALTH_BUILDING | INCOME | TARGET_GOAL
    focus_mode: str = field(default_factory=lambda: os.getenv("FOCUS_MODE", "WEALTH_BUILDING"))
    target_goal_amount: float = field(
        default_factory=lambda: float(os.getenv("TARGET_GOAL_AMOUNT", "0") or 0)
    )
    target_goal_date: str = field(default_factory=lambda: os.getenv("TARGET_GOAL_DATE", ""))

    # Portfolio phase settings
    growth_target_multiple: float = field(
        default_factory=lambda: float(os.getenv("GROWTH_TARGET_MULTIPLE", "3.0"))
    )
    monthly_distribution_eur: float = field(
        default_factory=lambda: float(os.getenv("MONTHLY_DISTRIBUTION_EUR", "500.0"))
    )
    distribution_buffer_months: int = field(
        default_factory=lambda: int(os.getenv("DISTRIBUTION_BUFFER_MONTHS", "6"))
    )

    # Risk filters
    block_earnings_days: int = field(
        default_factory=lambda: int(os.getenv("BLOCK_EARNINGS_DAYS", "5"))
    )
    max_sector_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_SECTOR_PCT", "0.40"))
    )

    # Kelly criterion sizing
    use_kelly_sizing: bool = field(
        default_factory=lambda: os.getenv("USE_KELLY_SIZING", "false").lower() in ("1", "true", "yes")
    )
    kelly_fraction: float = field(
        default_factory=lambda: float(os.getenv("KELLY_FRACTION", "0.25"))
    )

    # ── Margin / Hebel (progressives Tier-System) ─────────────────────────────
    # false = kein Hebel (Standard)
    # true  = Bot verdient sich Hebel durch Performance (Tier 1–4: 1.25×–2.00×)
    # Erfordert einen IBKR Margin-Account (nicht Cash-Account); jede Order
    # läuft zusätzlich durch den whatIf-Margin-Check des IBKRBroker.
    use_margin: bool = field(
        default_factory=lambda: os.getenv("USE_MARGIN", "false").lower() in ("1", "true", "yes")
    )
    # Nur bei dieser Konfidenz Margin nutzen (HIGH empfohlen)
    margin_min_confidence: str = field(
        default_factory=lambda: os.getenv("MARGIN_MIN_CONFIDENCE", "HIGH")
    )

    # Watchlist auto-scanner
    auto_scan_watchlist: bool = field(
        default_factory=lambda: os.getenv("AUTO_SCAN_WATCHLIST", "false").lower() in ("1", "true", "yes")
    )
    scan_max_picks: int = field(
        default_factory=lambda: int(os.getenv("SCAN_MAX_PICKS", "3"))
    )

    # ── Ollama (lokales KI-Modell, Mac mini M5) ──────────────────────────────
    # false = nur Claude API; true = Ollama filtert vor, Claude bestätigt
    ollama_enabled: bool = field(
        default_factory=lambda: os.getenv("OLLAMA_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    ollama_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434")
    )
    # Modell-Empfehlung: llama3.1:8b (16GB), qwen2.5:14b (24GB), llama3.3:70b (32GB)
    ollama_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    )
    ollama_timeout: int = field(
        default_factory=lambda: int(os.getenv("OLLAMA_TIMEOUT", "30"))
    )
    # ── MLX (Apple Silicon – schneller als Ollama auf M-Series) ──────────────
    # Aktivierung: pip install mlx-lm && mlx_lm.server --model <model> --port 8080
    # MLX wird automatisch bevorzugt wenn verfügbar (OLLAMA als Fallback).
    mlx_enabled: bool = field(
        default_factory=lambda: os.getenv("MLX_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    mlx_url: str = field(
        default_factory=lambda: os.getenv("MLX_URL", "http://localhost:8080")
    )
    # Nur für Logging und capability-Erkennung – das aktive Modell setzt mlx-lm selbst
    mlx_model: str = field(
        default_factory=lambda: os.getenv("MLX_MODEL", "qwen2.5:32b")
    )
    mlx_timeout: int = field(
        default_factory=lambda: int(os.getenv("MLX_TIMEOUT", "120"))
    )
    # ── Frugal-Modus (Paper-Trading / Datenspar-Modus) ────────────────────────
    # true = Ollama übernimmt alle normalen Analysen; Claude nur noch für
    # offene Positionen, SEC/Earnings-Quellen und manuelle Dashboard-Anfragen.
    # Einsparung: ~85% weniger Claude-API-Kosten.
    frugal_mode: bool = field(
        default_factory=lambda: os.getenv("FRUGAL_MODE", "true").lower() in ("1", "true", "yes")
    )
    # Smart Frugal: passt Schwellen automatisch an Modellgröße an.
    # true (default) = 32b-Modell filtert aggressiver als 8b-Modell.
    frugal_smart_mode: bool = field(
        default_factory=lambda: os.getenv("FRUGAL_SMART_MODE", "true").lower() not in ("0", "false", "no")
    )
    # Mindest-Score damit Ollama im Frugal-Modus BUY empfehlen darf.
    # 32b: 0.70 (präziser), 8b: 0.65 (weniger zuverlässig → höhere Schwelle).
    frugal_buy_min_score: float = field(
        default_factory=lambda: float(os.getenv("FRUGAL_BUY_MIN_SCORE", "0.68"))
    )

    # ── Einstiegs-Timing + Exit-Management ───────────────────────────────────
    # EMA21-Check: Kurs darf max. X% über EMA21 liegen (sonst: Conditional Entry)
    # 0.06 = 6% → mehr Spielraum für Momentum-Aktien
    entry_ema_max_deviation: float = field(
        default_factory=lambda: float(os.getenv("ENTRY_EMA_MAX_DEVIATION", "0.06"))
    )
    # Partial Take-Profit: Bei X% Gewinn werden Y% der Position verkauft
    # Verbleibende Position läuft mit SL auf Breakeven weiter (Trailing)
    partial_tp_pct: float = field(
        default_factory=lambda: float(os.getenv("PARTIAL_TP_PCT", "0.15"))
    )
    partial_tp_sell_frac: float = field(
        default_factory=lambda: float(os.getenv("PARTIAL_TP_SELL_FRAC", "0.25"))
    )
    # Zweite Partial-TP-Stufe: bei X% Gewinn weitere 40% der verbleibenden Shares verkaufen
    partial_tp2_pct: float = field(
        default_factory=lambda: float(os.getenv("PARTIAL_TP2_PCT", "0.30"))
    )
    # SL-Cooldown: N Tage nach verlustigem Stop-Loss (ohne vorherigen
    # Partial-TP) kein Wiederkauf. Gilt im Live-Pfad (analyzers/sl_cooldown.py,
    # verdrahtet in executor + swing_strategy) UND im Backtest
    # (backtesting/engine.py via scripts/run_backtest.py).
    sl_cooldown_days: int = field(
        default_factory=lambda: int(os.getenv("SL_COOLDOWN_DAYS", "2"))
    )
    # Mindest-Trades bevor RL-Agent und adaptive Threshold-Anpassung aktiv werden
    min_trades_for_adaptive: int = field(
        default_factory=lambda: int(os.getenv("MIN_TRADES_FOR_ADAPTIVE", "50"))
    )
    # Volumen-Bestätigung: heutiges Volumen muss mind. X% des 20d-Durchschnitts betragen
    volume_confirm_ratio: float = field(
        default_factory=lambda: float(os.getenv("VOLUME_CONFIRM_RATIO", "0.80"))
    )
    # News-Staleness: Kurs bereits X% gestiegen seit Newsveröffentlichung → Signal veraltet
    news_stale_pct: float = field(
        default_factory=lambda: float(os.getenv("NEWS_STALE_PCT", "0.05"))
    )
    # ── Hedge / Rezessions-Absicherung ───────────────────────────────────────
    # Inverse ETFs kaufen wenn Marktregime BEAR oder CRISIS erreicht
    enable_hedging: bool = field(
        default_factory=lambda: os.getenv("ENABLE_HEDGING", "true").lower() in ("1", "true", "yes")
    )
    # Ab welchem Regime hedgen: NEUTRAL | BEAR | CRISIS
    hedge_from_regime: str = field(
        default_factory=lambda: os.getenv("HEDGE_FROM_REGIME", "BEAR")
    )
    # Maximaler Portfolio-Anteil für alle Krisen-Instrumente (Inverse ETFs + Safe Havens + Defensive)
    max_hedge_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_HEDGE_PCT", "0.35"))
    )
    # Wie oft Regime-Check läuft (in Stunden, zwischen den Vollanalysen)
    regime_check_interval_hours: int = field(
        default_factory=lambda: int(os.getenv("REGIME_CHECK_INTERVAL_HOURS", "6"))
    )


    # ── TradingView Webhook ───────────────────────────────────────────────────
    tradingview_webhook_enabled: bool = field(
        default_factory=lambda: os.getenv("TRADINGVIEW_WEBHOOK_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    # Achtung: 8080 ist auf diesem Server durch nginx (KI-Nachhilfe) belegt.
    tradingview_webhook_port: int = field(
        default_factory=lambda: int(os.getenv("TRADINGVIEW_WEBHOOK_PORT", "8089"))
    )
    tradingview_webhook_secret: str = field(
        default_factory=lambda: os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "")
    )

    # ── Multi-Timeframe-Bestätigung ───────────────────────────────────────────
    # true  = BUY erst wenn 2 verschiedene Timeframes bestätigen (z.B. 5m + 1h)
    # false = jedes Signal sofort ausführen (wie bisher)
    mtf_confirmation_enabled: bool = field(
        default_factory=lambda: os.getenv("MTF_CONFIRMATION_ENABLED", "false").lower() in ("1", "true", "yes")
    )

    # ── VIX Risk Management ───────────────────────────────────────────────────
    # VIX > vix_pause_threshold: Positionsgrößen reduziert
    # VIX > vix_block_threshold: keine neuen Käufe
    vix_risk_enabled: bool = field(
        default_factory=lambda: os.getenv("VIX_RISK_ENABLED", "true").lower() in ("1", "true", "yes")
    )

    # ── Earnings-Schutz ───────────────────────────────────────────────────────
    # Offene Positionen werden N Tage vor Earnings automatisch geschlossen
    earnings_protection_enabled: bool = field(
        default_factory=lambda: os.getenv("EARNINGS_PROTECTION_ENABLED", "true").lower() in ("1", "true", "yes")
    )
    earnings_protect_days: int = field(
        default_factory=lambda: int(os.getenv("EARNINGS_PROTECT_DAYS", "2"))
    )

    # ── Korrelations-Signale ──────────────────────────────────────────────────
    # Wenn ein führender ETF signalisiert, folgen korrelierte Aktien automatisch
    correlation_signals_enabled: bool = field(
        default_factory=lambda: os.getenv("CORRELATION_SIGNALS_ENABLED", "true").lower() in ("1", "true", "yes")
    )

    # ── Krypto ───────────────────────────────────────────────────────────────
    # Krypto-Handel via IBKR (PAXOS) bzw. Paper-Broker.
    # CRYPTO_ENABLED=true  aktiviert Krypto im normalen Bot-Zyklus.
    # --crypto-scan läuft immer manuell (unabhängig von CRYPTO_ENABLED).
    crypto_enabled: bool = field(
        default_factory=lambda: os.getenv("CRYPTO_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    # Welche Coins handeln? Format: BTC,ETH,SOL (ohne /USD – wird automatisch ergänzt)
    crypto_watchlist: List[str] = field(
        default_factory=lambda: [c.strip().upper() for c in os.getenv("CRYPTO_WATCHLIST", "BTC,ETH,SOL").split(",") if c.strip()]
    )
    # Maximaler Portfolio-Anteil für alle Krypto-Positionen zusammen
    crypto_max_portfolio_pct: float = field(
        default_factory=lambda: float(os.getenv("CRYPTO_MAX_PORTFOLIO_PCT", "0.15"))
    )
    # Strengere Stop-Loss für Krypto (höhere Volatilität)
    crypto_stop_loss_pct: float = field(
        default_factory=lambda: float(os.getenv("CRYPTO_STOP_LOSS_PCT", "0.10"))
    )
    crypto_take_profit_pct: float = field(
        default_factory=lambda: float(os.getenv("CRYPTO_TAKE_PROFIT_PCT", "0.25"))
    )

    # ── Europäische Aktien ────────────────────────────────────────────────────
    # EU-Aktien im XETRA/LSE-Format (z.B. SAP.DE, ASML.AS, LVMH.PA).
    # EU_STOCKS_ENABLED=true fügt EU-Watchlist zum normalen Bot-Zyklus hinzu.
    # --eu-scan läuft immer manuell.
    eu_stocks_enabled: bool = field(
        default_factory=lambda: os.getenv("EU_STOCKS_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    # Eigene EU-Watchlist (leer = Scanner-Empfehlungen nutzen)
    eu_watchlist: List[str] = field(
        default_factory=lambda: [t.strip().upper() for t in os.getenv("EU_WATCHLIST", "").split(",") if t.strip()]
    )


config = Config()


def validate_config() -> None:
    """
    Prüft kritische Konfigurationswerte beim Start.
    Gibt Warnungen aus und beendet das Programm bei fatalen Fehlern.
    """
    errors:   List[str] = []
    warnings: List[str] = []

    # ── Kritisch: ohne diese Keys läuft gar nichts ────────────────────────────
    if not config.anthropic_api_key:
        errors.append("ANTHROPIC_API_KEY fehlt – Claude-Analyse nicht möglich.")

    # ── Broker-spezifisch ─────────────────────────────────────────────────────
    if config.broker_mode == "ibkr":
        try:
            import ib_insync  # noqa: F401
        except ImportError:
            errors.append(
                "BROKER_MODE=ibkr, aber ib_insync fehlt. Bitte: pip install ib_insync"
            )

    # ── Warnungen für optionale aber empfohlene Keys ──────────────────────────
    if not config.telegram_bot_token or not config.telegram_chat_id:
        warnings.append("TELEGRAM_BOT_TOKEN / CHAT_ID fehlen – keine Push-Benachrichtigungen.")
    if not config.newsapi_key:
        warnings.append("NEWSAPI_KEY fehlt – NewsAPI-Quelle deaktiviert.")

    # ── Wertebereich-Prüfungen ────────────────────────────────────────────────
    if not (0.0 < config.stop_loss_pct < 1.0):
        errors.append(f"STOP_LOSS_PCT={config.stop_loss_pct} ungültig (muss 0–1 sein).")
    if not (0.0 < config.take_profit_pct < 5.0):
        errors.append(f"TAKE_PROFIT_PCT={config.take_profit_pct} ungültig.")
    if not (0.0 < config.max_position_pct <= 1.0):
        errors.append(f"MAX_POSITION_PCT={config.max_position_pct} ungültig.")
    if not (0.0 < config.buy_threshold <= 1.0):
        errors.append(f"BUY_THRESHOLD={config.buy_threshold} ungültig (muss 0–1 sein).")
    if not (0.0 <= config.capital_scarcity_cash_pct_empty < config.capital_scarcity_cash_pct_full):
        errors.append(
            f"CAPITAL_SCARCITY_CASH_PCT_EMPTY={config.capital_scarcity_cash_pct_empty} muss "
            f"kleiner sein als CAPITAL_SCARCITY_CASH_PCT_FULL={config.capital_scarcity_cash_pct_full}."
        )
    if not (0.0 <= config.capital_scarcity_max_adj <= 0.5):
        errors.append(f"CAPITAL_SCARCITY_MAX_ADJ={config.capital_scarcity_max_adj} ungültig (0–0,5 sinnvoll).")
    if not (0.0 < config.capital_scarcity_curve_exponent <= 10.0):
        errors.append(
            f"CAPITAL_SCARCITY_CURVE_EXPONENT={config.capital_scarcity_curve_exponent} "
            f"ungültig (0–10 sinnvoll; 1.0=linear, 2–3 typisch exponentiell)."
        )
    if config.initial_capital <= 0:
        errors.append(f"INITIAL_CAPITAL={config.initial_capital} ungültig.")

    if config.focus_mode == "TARGET_GOAL":
        if not config.target_goal_amount or config.target_goal_amount <= 0:
            warnings.append("FOCUS_MODE=TARGET_GOAL aber TARGET_GOAL_AMOUNT nicht gesetzt.")
        if not config.target_goal_date:
            warnings.append("FOCUS_MODE=TARGET_GOAL aber TARGET_GOAL_DATE nicht gesetzt.")

    if config.kelly_fraction > 0.5:
        warnings.append(
            f"KELLY_FRACTION={config.kelly_fraction} ist sehr hoch (>0.5) – "
            f"erhöhtes Risiko. Empfehlung: 0.25."
        )

    # ── Ausgabe ───────────────────────────────────────────────────────────────
    for w in warnings:
        print(f"[CONFIG WARNUNG] {w}", file=sys.stderr)

    if errors:
        print("\n[CONFIG FEHLER] Programm kann nicht starten:", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        sys.exit(1)
