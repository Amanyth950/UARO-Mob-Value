import math

import pandas as pd
import streamlit as st

import RO2 as app
import RO2_perf as perf

PAGE_SIZE = 200


def page_slice(df: pd.DataFrame, key: str):
    total = len(df)
    if total <= PAGE_SIZE:
        return df.reset_index(drop=True)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1, key=key)
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    st.caption(f"Showing rows {start + 1:,}-{min(end, total):,} of {total:,}. Narrow filters for faster browsing.")
    return df.iloc[start:end].reset_index(drop=True)


def monster_label(row: pd.Series) -> str:
    return f"{row.get('name', '')} | EV {app.as_float(row.get('expected_value')):,.0f} | ID {app.as_int(row.get('id'))}"


def render_best_farms(df: pd.DataFrame, settings, prices) -> None:
    st.subheader("Mobs")
    app.render_tab_help("best_farms")
    if df.empty:
        st.info("No monsters match the current filters.")
        return
    visible = page_slice(df.reset_index(drop=True), "mob_page")
    st.dataframe(app.clean_table(visible), use_container_width=True, hide_index=True)
    choices = [-1] + list(range(len(visible)))
    selected = st.selectbox(
        "Inspect monster",
        choices,
        index=0,
        format_func=lambda i: "Select a monster" if i == -1 else monster_label(visible.iloc[i]),
        key="monster_picker",
    )
    if selected == -1:
        st.info("Select a monster above to show its drop value breakdown here.")
        return
    app.render_selected_monster_drops(visible.iloc[selected], settings, prices)


def main() -> None:
    app.apply_layout_css()
    st.title("Mob Value Planner")
    st.caption("Monster value explorer, farming comparison tool, and price-table sandbox.")
    try:
        raw = app.load_data(app.CSV_PATH)
    except Exception as exc:
        st.error(str(exc))
        st.stop()
    if raw.empty:
        st.warning("`monster_ev.csv` is empty or missing usable rows. Run `python generate_monster_ev.py` with source data, commit the generated CSV, and redeploy Streamlit.")
        st.stop()

    perf.init_state(raw)
    settings = perf.sidebar(raw)
    prices = st.session_state.personal_prices
    df = perf.cached_ev(raw, perf.pkey(settings), perf.mkey(prices))
    filtered = app.filter_dataframe(df, settings)
    app.render_metrics(filtered, settings, prices)

    tabs = st.tabs(["Best farms", "Maps", "Items", "Raw data"])
    with tabs[0]:
        render_best_farms(filtered, settings, prices)
    with tabs[1]:
        app.render_maps(filtered)
    with tabs[2]:
        app.render_items(raw, settings, prices)
    with tabs[3]:
        app.render_raw(filtered)


if __name__ == "__main__":
    main()
