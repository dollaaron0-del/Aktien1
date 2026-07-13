"""
Ausgelagerte Tab-Renderer für dashboard/app.py (Roadmap 4.4a, Monolith-Split).

app.py baut vor den Tabs einen Kontext (types.SimpleNamespace mit allen
bis dahin definierten Variablen: broker/portfolio/tracker/config/Helper-
Funktionen/…) und reicht ihn an jedes `render(ctx)` hier durch. Grund für
den Kontext statt einzeln durchgereichter Parameter: es gibt 25+ geteilte
Namen; ein Kontext-Objekt macht "eine vergessen" strukturell unmöglich
(jedes Modul bekommt automatisch alles, was zum Zeitpunkt der Tab-
Erzeugung im Modul-Namensraum stand) statt bei jeder Handverlesung neu zu
riskieren, eine Abhängigkeit zu übersehen.
"""
