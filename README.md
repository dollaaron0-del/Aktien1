# Stock Sentiment Trading Bot

Automatischer Swing-Trading-Bot für macOS. Sammelt Nachrichten von Reddit, Yahoo Finance und NewsAPI, analysiert diese mit der Claude AI-API und handelt Aktien automatisch.

## Voraussetzungen

- Python 3.11+
- macOS (oder Linux)
- Anthropic API-Key (claude.ai → API → Keys)
- Optional: Reddit-App-Credentials, NewsAPI-Key

## Installation

```bash
cd Aktien
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Dann `.env` öffnen und mindestens `ANTHROPIC_API_KEY` eintragen.

## Verwendung

```bash
# Einmalige Analyse aller Watchlist-Aktien
python main.py --once

# Portfolioübersicht und Trade-History
python main.py --status

# Dauerbetrieb (tägliche Analyse + stündlicher Stop-Loss-Check)
python main.py
```

## Konfiguration

In `config.py` anpassen:

| Parameter | Bedeutung | Standard |
|-----------|-----------|---------|
| `watchlist` | Zu beobachtende Ticker | AAPL, MSFT, NVDA, TSLA, AMZN, GOOGL, META |
| `max_position_pct` | Max. Portfolioanteil pro Position | 20 % |
| `stop_loss_pct` | Stop-Loss-Schwelle | 7 % |
| `take_profit_pct` | Take-Profit-Schwelle | 20 % |
| `buy_threshold` | Mindest-Sentiment-Score für Kauf | 0.65 |
| `sell_threshold` | Maximal-Score für Verkauf | 0.35 |
| `analysis_hour/minute` | Uhrzeit tägliche Analyse | 08:30 |

## Architektur

```
collectors/      ← Datensammlung (Reddit, Yahoo, NewsAPI)
analyzers/       ← Claude-API Sentiment-Analyse
strategy/        ← Swing-Trading-Logik (Entry/Exit)
broker/          ← Broker-Abstraktion (Paper-Trading)
portfolio/       ← Positionsverwaltung + Persistenz
data/            ← portfolio.json (automatisch erstellt)
```

## Broker-Wechsel

### Alpaca (US-Aktien, kostenlos)
1. Konto erstellen: alpaca.markets
2. In `.env`: `BROKER_MODE=alpaca`, Alpaca-Keys eintragen
3. Alpaca-Integration in `broker/alpaca_broker.py` hinzufügen (Vorlage: `paper_broker.py`)

### Scalable Capital / Trade Republic
Beide Broker bieten **keine öffentliche Trading-API** an. Paper-Trading-Modus verwenden oder Alpaca/IBKR nutzen.

## Haftungsausschluss

Dieses Programm ist ein Lernprojekt. Echtes Trading mit eigenem Kapital erfolgt auf eigenes Risiko. Der Autor übernimmt keine Haftung für finanzielle Verluste.
