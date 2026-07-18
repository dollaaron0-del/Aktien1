"""Wochenbriefing-Panel — bis 18.7.2026 eigener Tab, seit dem Tab-Umbau
ein Abschnitt im Kartei-Tab (dashboard/tabs/dossier.py)."""
import streamlit as st


def render(ctx) -> None:
    st.subheader("📰 Wochenbriefing")
    st.caption(
        "Claude analysiert jeden Samstag/Sonntag: Earnings-Kalender, Marktlage, "
        "Makro-News. Das Briefing fließt in alle Analysen der Folgewoche ein."
    )

    briefings = ctx.weekend_prep.get_latest_briefing(limit=3)
    current = ctx.weekend_prep.get_current_briefing()

    b_col1, b_col2 = st.columns([5, 2])
    with b_col2:
        if st.button("🔄 Neues Briefing generieren", width="stretch"):
            with st.spinner("Claude bereitet Wochenbriefing vor…"):
                result = ctx.weekend_prep.run()
            if result:
                st.success("Briefing generiert!")
                st.rerun()
            else:
                st.error("Fehler beim Generieren (API-Key oder Daten fehlen).")

    with b_col1:
        if briefings:
            tabs_brief = st.tabs([f"KW {b['week_start']}" for b in briefings])
            for tab_b, brief in zip(tabs_brief, briefings):
                with tab_b:
                    generated = brief.get("generated_at", "")[:16]
                    is_current = brief.get("week_start") == (current and brief.get("week_start"))
                    if is_current:
                        st.success(f"✅ Aktuelles Briefing · Erstellt: {generated}")
                    else:
                        st.caption(f"Erstellt: {generated}")
                    st.markdown(brief["briefing"])
        else:
            st.info(
                "Noch kein Briefing vorhanden.  \n"
                "Wird automatisch am Wochenende generiert oder jetzt mit dem Button oben starten."
            )
