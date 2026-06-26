import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd
import streamlit as st

CSV_PATH = "monster_ev.csv"
MANUAL_PRICE_PATH = "manual_prices.json"
MANUAL_PRICE_EXAMPLE_PATH = "manual_prices.example.json"
ELEMENT_PREFIXES = ("Ele_", "ELE_", "Element_", "ELEMENT_")

st.set_page_config(page_title="uaRO Farming Planner", layout="wide")


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def pretty_enum(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return ""
    for prefix in ELEMENT_PREFIXES + ("RC_", "Size_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.replace("_", " ").strip()


def numeric_series(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce").fillna(default)
    return pd.Series(default, index=df.index, dtype="float64")


def safe_min_max(series: pd.Series, default_min: int = 0, default_max: int = 0) -> Tuple[int, int]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return default_min, default_max
    return int(numeric.min()), int(numeric.max())


@st.cache_data
def load_data(csv_path: str = CSV_PATH) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {csv_path}. Run `python generate_monster_ev.py` and commit the CSV.")
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()

    numeric_defaults = {
        "id": 0,
        "level": 0,
        "hp": 0,
        "best_map_count": 0,
        "map_count": 0,
        "total_spawn_count": 0,
        "drop_count": 0,
        "mvp_drop_count": 0,
        "missing_item_count": 0,
        "expected_value_raw": 0.0,
        "expected_value": 0.0,
    }
    for col, default in numeric_defaults.items():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(default)
            if isinstance(default, int):
                df[col] = df[col].astype(int)

    for col in ["is_boss", "has_mvp_drops", "ev_generation_cap_drop_rate", "mvp_drops_included_in_ev"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().isin(["true", "1", "yes"])

    for col in ["name", "sprite_name", "internal_name", "best_map", "race", "size", "element", "spawn_summary", "drops_summary", "drops_json"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    df["element_display"] = df["element"].apply(pretty_enum) if "element" in df.columns else ""
    return df


def parse_drops_json(value: Any) -> List[Dict[str, Any]]:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [drop for drop in parsed if isinstance(drop, dict)] if isinstance(parsed, list) else []


def parse_spawn_summary(value: Any) -> Dict[str, int]:
    spawns: Dict[str, int] = {}
    text = str(value or "").strip()
    if not text:
        return spawns
    for piece in text.split(";"):
        part = piece.strip()
        if not part or ":" not in part:
            continue
        map_name, count_text = part.rsplit(":", 1)
        map_name = map_name.strip()
        count = as_int(count_text.strip(), 0)
        if map_name and count > 0:
            spawns[map_name] = spawns.get(map_name, 0) + count
    return spawns


def drop_item_key(drop: Dict[str, Any]) -> str:
    return str(drop.get("aegis_name") or drop.get("name") or "").strip()


def normalize_manual_prices(raw: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    if isinstance(raw.get("prices"), dict):
        raw = raw["prices"]
    prices: Dict[str, Dict[str, Any]] = {}
    for key, value in raw.items():
        item_key = str(key).strip()
        if not item_key:
            continue
        if isinstance(value, dict):
            price = as_float(value.get("price"), -1.0)
            name = str(value.get("name") or value.get("item") or item_key).strip()
        else:
            price = as_float(value, -1.0)
            name = item_key
        if price >= 0:
            prices[item_key] = {"name": name or item_key, "price": float(price)}
    return prices


def load_price_file(path: str | Path) -> Dict[str, Dict[str, Any]]:
    price_path = Path(path)
    if not price_path.exists():
        return {}
    try:
        return normalize_manual_prices(json.loads(price_path.read_text(encoding="utf-8")))
    except Exception:
        return {}


def export_price_payload(prices: Dict[str, Dict[str, Any]], name: str) -> Dict[str, Any]:
    clean = {}
    for key, value in sorted(prices.items(), key=lambda item: str(item[1].get("name") or item[0]).lower()):
        price = as_float(value.get("price"), 0.0)
        clean[str(key)] = {"name": str(value.get("name") or key), "price": int(price) if price.is_integer() else price}
    return {"name": name, "format": "uaro-mob-value.price-table.v1", "prices": clean}


def price_editor_dataframe(prices: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
    rows = [
        {
            "AegisName": key,
            "Item": str(value.get("name") or key),
            "Manual Price": as_int(value.get("price"), 0),
        }
        for key, value in sorted(prices.items(), key=lambda item: str(item[1].get("name") or item[0]).lower())
    ]
    return pd.DataFrame(rows, columns=["AegisName", "Item", "Manual Price"])


def prices_from_editor_dataframe(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    prices: Dict[str, Dict[str, Any]] = {}
    if df is None or df.empty:
        return prices
    for _, row in df.iterrows():
        key = str(row.get("AegisName") or "").strip()
        if not key:
            continue
        price = as_float(row.get("Manual Price"), -1.0)
        if price < 0:
            continue
        name = str(row.get("Item") or key).strip() or key
        prices[key] = {"name": name, "price": float(price)}
    return prices


def init_price_state() -> None:
    if "personal_prices" not in st.session_state:
        st.session_state.personal_prices = load_price_file(MANUAL_PRICE_PATH)


def get_price_tables() -> Dict[str, Dict[str, Any]]:
    return {
        "NPC only": {"prices": {}, "visibility": "built-in"},
        "Example table": {"prices": load_price_file(MANUAL_PRICE_EXAMPLE_PATH), "visibility": "read-only"},
        "Personal table": {"prices": st.session_state.personal_prices, "visibility": "editable/session"},
    }


def manual_price_for_drop(drop: Dict[str, Any], manual_prices: Dict[str, Dict[str, Any]]) -> float | None:
    for key in [drop_item_key(drop), str(drop.get("name") or "").strip()]:
        if key in manual_prices:
            price = as_float(manual_prices[key].get("price"), -1.0)
            if price >= 0:
                return price
    return None


def adjusted_drop_chance(raw_chance: Any, multiplier: float) -> float:
    return min(max(as_float(raw_chance, 0.0) * multiplier, 0.0), 10000.0)


def price_source(drop: Dict[str, Any], use_overcharge: bool, use_manual: bool, prices: Dict[str, Dict[str, Any]]) -> str:
    if use_manual and manual_price_for_drop(drop, prices) is not None:
        return "Manual"
    if use_overcharge and not bool(drop.get("ignore_overcharge")):
        return "NPC + Overcharge"
    return "NPC"


def effective_sell_price(drop: Dict[str, Any], use_overcharge: bool, overcharge_rate: float, use_manual: bool, prices: Dict[str, Dict[str, Any]]) -> float:
    manual = manual_price_for_drop(drop, prices) if use_manual else None
    if manual is not None:
        return manual
    base = as_float(drop.get("base_sell_price", drop.get("sell_price")), 0.0)
    return int(base * overcharge_rate) if use_overcharge and not bool(drop.get("ignore_overcharge")) else base


def drop_details_dataframe(drops: Iterable[Dict[str, Any]], multiplier: float, use_overcharge: bool, overcharge_rate: float, use_manual: bool, prices: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for drop in drops:
        raw = as_float(drop.get("raw_chance"), 0.0)
        adjusted = adjusted_drop_chance(raw, multiplier)
        manual = manual_price_for_drop(drop, prices) if use_manual else None
        sell = effective_sell_price(drop, use_overcharge, overcharge_rate, use_manual, prices)
        ev = 0.0 if bool(drop.get("missing_item")) else sell * adjusted / 10000.0
        rows.append(
            {
                "Item": str(drop.get("name") or drop.get("aegis_name") or ""),
                "Adjusted EV": ev,
                "EV Share": 0.0,
                "Adjusted Chance": adjusted / 100.0,
                "Effective Sell": int(sell) if float(sell).is_integer() else sell,
                "Price Source": price_source(drop, use_overcharge, use_manual, prices),
                "Base Chance": raw / 100.0,
                "Base Sell": as_int(drop.get("base_sell_price", drop.get("sell_price")), 0),
                "Manual Price": "" if manual is None else int(manual) if float(manual).is_integer() else manual,
                "Type": "MVP" if bool(drop.get("is_mvp_drop")) else "Normal",
                "AegisName": str(drop.get("aegis_name") or ""),
                "Missing Item": bool(drop.get("missing_item")),
            }
        )
    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail
    total = as_float(detail["Adjusted EV"].sum(), 0.0)
    detail["EV Share"] = detail["Adjusted EV"] / total * 100.0 if total > 0 else 0.0
    preferred = ["Item", "Adjusted EV", "EV Share", "Adjusted Chance", "Effective Sell", "Price Source", "Base Chance", "Base Sell", "Manual Price", "Type", "AegisName", "Missing Item"]
    return detail[preferred].sort_values("Adjusted EV", ascending=False, kind="stable").reset_index(drop=True)


def summarize_drops(detail: pd.DataFrame, limit: int = 3) -> str:
    if detail.empty:
        return ""
    return ", ".join(f"{row['Item']} ({as_float(row['EV Share']):.0f}%)" for _, row in detail.head(limit).iterrows())


def top_value_share_from_detail(detail: pd.DataFrame) -> float:
    if detail.empty or as_float(detail["Adjusted EV"].sum(), 0.0) <= 0:
        return 0.0
    total = as_float(detail["Adjusted EV"].sum(), 0.0)
    return as_float(detail["Adjusted EV"].max(), 0.0) / total * 100.0


def recalc_monster(row: pd.Series, multiplier: float, use_overcharge: bool, overcharge_rate: float, use_manual: bool, prices: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    detail = drop_details_dataframe(parse_drops_json(row.get("drops_json", "")), multiplier, use_overcharge, overcharge_rate, use_manual, prices)
    return {"expected_value": as_float(detail["Adjusted EV"].sum(), 0.0) if not detail.empty else as_float(row.get("expected_value"), 0.0), "top_drops": summarize_drops(detail), "top_value_share": top_value_share_from_detail(detail)}


def apply_ui_ev_settings(df: pd.DataFrame, multiplier: float, use_overcharge: bool, overcharge_rate: float, use_manual: bool, prices: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
    df = df.copy()
    if df.empty or "drops_json" not in df.columns:
        return df
    derived = df.apply(lambda row: recalc_monster(row, multiplier, use_overcharge, overcharge_rate, use_manual, prices), axis=1)
    df["expected_value"] = derived.apply(lambda d: d["expected_value"])
    df["top_drops"] = derived.apply(lambda d: d["top_drops"])
    df["top_value_share"] = derived.apply(lambda d: d["top_value_share"])
    expected = numeric_series(df, "expected_value")
    hp = numeric_series(df, "hp")
    spawns = numeric_series(df, "best_map_count")
    df["ev_per_1k_hp"] = expected.divide(hp.where(hp > 0)).fillna(0.0) * 1000.0
    df["map_value_score"] = expected * spawns
    return df


def extract_item_catalog(df: pd.DataFrame) -> pd.DataFrame:
    catalog: Dict[str, Dict[str, Any]] = {}
    if "drops_json" not in df.columns:
        return pd.DataFrame(columns=["key", "name", "aegis_name", "base_sell_price", "drop_slots"])
    for drops in df["drops_json"].apply(parse_drops_json):
        for drop in drops:
            key = drop_item_key(drop)
            if not key:
                continue
            entry = catalog.setdefault(key, {"key": key, "name": str(drop.get("name") or key), "aegis_name": key, "base_sell_price": as_float(drop.get("base_sell_price", drop.get("sell_price")), 0.0), "drop_slots": 0})
            entry["drop_slots"] += 1
    return pd.DataFrame(catalog.values()).sort_values(["name", "aegis_name"], kind="stable").reset_index(drop=True) if catalog else pd.DataFrame(columns=["key", "name", "aegis_name", "base_sell_price", "drop_slots"])


def render_sidebar(df: pd.DataFrame) -> Tuple[Dict[str, Any], str, Dict[str, Dict[str, Any]]]:
    st.sidebar.header("Assumptions")
    multiplier = st.sidebar.number_input("Drop rate multiplier", min_value=0.0, value=5.0, step=0.5)
    use_overcharge = st.sidebar.checkbox("Apply merchant Overcharge (+24%)", value=True)
    overcharge_rate = st.sidebar.number_input("Overcharge multiplier", min_value=1.0, value=1.24, step=0.01, format="%.2f", disabled=not use_overcharge)
    tables = get_price_tables()
    selected_table = st.sidebar.selectbox("Price table", list(tables.keys()), index=1 if len(tables) > 1 else 0)
    prices = tables[selected_table]["prices"]
    st.sidebar.caption(f"{len(prices):,} manual price override(s).")

    st.sidebar.header("Farm filters")
    name_query = st.sidebar.text_input("Monster name contains", value="")
    map_query = st.sidebar.text_input("Map contains", value="")
    element_map: Dict[str, List[str]] = {}
    if "element" in df.columns and not df.empty:
        pairs = df[["element", "element_display"]].drop_duplicates().sort_values(["element_display", "element"])
        for _, row in pairs.iterrows():
            raw = str(row.get("element") or "").strip()
            label = str(row.get("element_display") or raw).strip()
            if raw:
                element_map.setdefault(label, []).append(raw)
    selected_elements = st.sidebar.multiselect("Element", list(element_map.keys()), default=[])
    lvl_min, lvl_max = safe_min_max(df["level"] if "level" in df.columns else pd.Series(dtype=int))
    level_range = st.sidebar.slider("Level range", lvl_min, lvl_max, (lvl_min, lvl_max))
    sp_min, sp_max = safe_min_max(df["best_map_count"] if "best_map_count" in df.columns else pd.Series(dtype=int))
    spawn_range = st.sidebar.slider("Best-map spawn count", sp_min, sp_max, (max(1, sp_min) if sp_max >= 1 else sp_min, sp_max))
    min_ev = st.sidebar.number_input("Minimum EV / kill", min_value=0.0, value=0.0, step=1.0)
    include_boss = st.sidebar.checkbox("Include boss-flagged monsters", value=False) if "is_boss" in df.columns else True
    include_mvp = st.sidebar.checkbox("Include monsters with MVP drops", value=False) if "has_mvp_drops" in df.columns else True
    sort_candidates = [c for c in ["map_value_score", "expected_value", "ev_per_1k_hp", "best_map_count", "level", "hp", "name"] if c in df.columns or c in {"map_value_score", "ev_per_1k_hp"}]
    sort_by = st.sidebar.selectbox("Sort by", sort_candidates, index=0 if sort_candidates else None)
    ascending = st.sidebar.checkbox("Ascending sort", value=False)
    settings = {"multiplier": multiplier, "use_overcharge": use_overcharge, "overcharge_rate": overcharge_rate, "use_manual": selected_table != "NPC only", "name_query": name_query, "map_query": map_query, "element_map": element_map, "selected_elements": selected_elements, "level_range": level_range, "spawn_range": spawn_range, "min_ev": min_ev, "include_boss": include_boss, "include_mvp": include_mvp, "sort_by": sort_by, "ascending": ascending}
    return settings, selected_table, prices


def filter_dataframe(df: pd.DataFrame, s: Dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    if s["selected_elements"] and "element" in out.columns:
        allowed = {raw for label in s["selected_elements"] for raw in s["element_map"].get(label, [])}
        out = out[out["element"].isin(allowed)]
    if "level" in out.columns:
        out = out[(out["level"] >= s["level_range"][0]) & (out["level"] <= s["level_range"][1])]
    if "best_map_count" in out.columns:
        out = out[(out["best_map_count"] >= s["spawn_range"][0]) & (out["best_map_count"] <= s["spawn_range"][1])]
    if "expected_value" in out.columns:
        out = out[out["expected_value"] >= s["min_ev"]]
    if s["name_query"].strip() and "name" in out.columns:
        q = s["name_query"].strip().lower()
        mask = out["name"].str.lower().str.contains(q, regex=False)
        for col in ["sprite_name", "internal_name"]:
            if col in out.columns:
                mask = mask | out[col].str.lower().str.contains(q, regex=False)
        out = out[mask]
    if s["map_query"].strip() and "best_map" in out.columns:
        q = s["map_query"].strip().lower()
        mask = out["best_map"].str.lower().str.contains(q, regex=False)
        if "spawn_summary" in out.columns:
            mask = mask | out["spawn_summary"].str.lower().str.contains(q, regex=False)
        out = out[mask]
    if not s["include_boss"] and "is_boss" in out.columns:
        out = out[~out["is_boss"]]
    if not s["include_mvp"] and "has_mvp_drops" in out.columns:
        out = out[~out["has_mvp_drops"]]
    if s["sort_by"]:
        out = out.sort_values(s["sort_by"], ascending=s["ascending"], kind="stable")
    return out


def clean_table(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["name", "expected_value", "map_value_score", "ev_per_1k_hp", "top_drops", "level", "hp", "element_display", "best_map", "best_map_count", "is_boss", "has_mvp_drops", "id"]
    visible = [c for c in cols if c in df.columns]
    return df[visible].rename(columns={"name": "Monster", "expected_value": "EV / kill", "map_value_score": "Map score", "ev_per_1k_hp": "EV / 1k HP", "top_drops": "Main value drops", "element_display": "Element", "best_map": "Best map", "best_map_count": "Best-map spawns", "is_boss": "Boss", "has_mvp_drops": "Has MVP drops", "id": "ID", "level": "Level", "hp": "HP"})


def monster_options(df: pd.DataFrame) -> Dict[str, int]:
    opts = {}
    for idx, row in df.reset_index(drop=True).iterrows():
        label = f"{row.get('name') or row.get('internal_name') or 'Unknown'} - ID {row.get('id', '')} - {row.get('best_map') or 'no map'}"
        opts[label if label not in opts else f"{label} ({idx})"] = idx
    return opts


def select_row_from_table(table_df: pd.DataFrame, source_df: pd.DataFrame, fallback_label: str) -> pd.Series | None:
    selected_position = None
    try:
        event = st.dataframe(
            table_df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )
        rows = getattr(getattr(event, "selection", None), "rows", [])
        if rows:
            selected_position = int(rows[0])
    except TypeError:
        st.dataframe(table_df, use_container_width=True, hide_index=True)
        if not source_df.empty:
            opts = {str(label): idx for idx, label in enumerate(table_df.iloc[:, 0].astype(str).tolist())}
            chosen = st.selectbox(fallback_label, [""] + list(opts.keys()), index=0)
            if chosen:
                selected_position = opts[chosen]
    if selected_position is None or selected_position < 0 or selected_position >= len(source_df):
        return None
    return source_df.reset_index(drop=True).iloc[selected_position]


def render_metrics(df: pd.DataFrame, selected_table: str, prices: Dict[str, Dict[str, Any]], settings: Dict[str, Any]) -> None:
    cols = st.columns(6)
    cols[0].metric("Matching mobs", f"{len(df):,}")
    cols[1].metric("Highest EV / kill", f"{df['expected_value'].max() if len(df) and 'expected_value' in df else 0:,.0f}")
    cols[2].metric("Median EV / kill", f"{df['expected_value'].median() if len(df) and 'expected_value' in df else 0:,.0f}")
    cols[3].metric("Highest map score", f"{df['map_value_score'].max() if len(df) and 'map_value_score' in df else 0:,.0f}")
    cols[4].metric("Drop multiplier", f"x{settings['multiplier']:g}")
    cols[5].metric("Price table", selected_table, f"{len(prices)} prices")


def render_selected_monster_drops(row: pd.Series, settings: Dict[str, Any], prices: Dict[str, Dict[str, Any]]) -> None:
    st.subheader(f"Drops for {row.get('name', 'selected monster')}")
    cols = st.columns(6)
    cols[0].metric("Monster ID", str(row.get("id", "")))
    cols[1].metric("Level", str(row.get("level", "")))
    cols[2].metric("Element", row.get("element_display") or "-")
    cols[3].metric("Best map", row.get("best_map") or "-")
    cols[4].metric("Spawns", f"{as_int(row.get('best_map_count')):,}")
    cols[5].metric("EV / kill", f"{as_float(row.get('expected_value')):,.2f}")
    detail = drop_details_dataframe(parse_drops_json(row.get("drops_json", "")), settings["multiplier"], settings["use_overcharge"], settings["overcharge_rate"], settings["use_manual"], prices)
    if detail.empty:
        st.info(row.get("drops_summary") or "No drop details are available. Regenerate the CSV with drops_json if needed.")
        return
    capped = int((detail["Adjusted Chance"] >= 100.0).sum())
    st.caption(f"Main value: {summarize_drops(detail, 5) or '-'} | capped drops: {capped}")
    st.dataframe(detail, use_container_width=True, hide_index=True)
    with st.expander("Spawn summary"):
        st.write(str(row.get("spawn_summary") or "No spawn summary available."))


def render_best_farms(df: pd.DataFrame, settings: Dict[str, Any], prices: Dict[str, Dict[str, Any]]) -> None:
    st.subheader("Mobs")
    st.caption("Filtered and sorted mob table. Select a row to inspect its drop value breakdown below.")
    if df.empty:
        st.info("No monsters match the current filters.")
        return
    source_df = df.reset_index(drop=True)
    selected = select_row_from_table(clean_table(source_df), source_df, "Inspect monster drops")
    if selected is not None:
        render_selected_monster_drops(selected, settings, prices)
    else:
        st.info("Select a monster row above to show its drop value breakdown here.")


def render_compare(df: pd.DataFrame) -> None:
    st.subheader("Compare farms")
    if df.empty:
        st.info("No farms are available under the current filters.")
        return
    opts = monster_options(df)
    selected = st.multiselect("Pick 2-5 monsters", list(opts.keys()), default=list(opts.keys())[: min(3, len(opts))], max_selections=5)
    rows = [df.reset_index(drop=True).iloc[opts[label]] for label in selected]
    compare = pd.DataFrame([
        {"Monster": r.get("name"), "EV / kill": as_float(r.get("expected_value")), "Map score": as_float(r.get("map_value_score")), "EV / 1k HP": as_float(r.get("ev_per_1k_hp")), "Top value share": as_float(r.get("top_value_share")), "Best map": r.get("best_map"), "Spawns": as_int(r.get("best_map_count")), "Level": as_int(r.get("level")), "HP": as_int(r.get("hp")), "Element": r.get("element_display"), "Main drops": r.get("top_drops")} for r in rows
    ])
    st.dataframe(compare, use_container_width=True, hide_index=True)


def build_map_monster_rows(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for source_idx, row in df.reset_index(drop=True).iterrows():
        spawns = parse_spawn_summary(row.get("spawn_summary", ""))
        if not spawns and str(row.get("best_map") or "").strip():
            spawns = {str(row.get("best_map")).strip(): as_int(row.get("best_map_count"), 0)}
        for map_name, count in spawns.items():
            ev = as_float(row.get("expected_value"), 0.0)
            rows.append(
                {
                    "source_idx": source_idx,
                    "Map": map_name,
                    "Monster": row.get("name"),
                    "Spawn count": count,
                    "EV / kill": ev,
                    "Map score": ev * count,
                    "EV / 1k HP": as_float(row.get("ev_per_1k_hp"), 0.0),
                    "Main value drops": row.get("top_drops"),
                    "Level": as_int(row.get("level"), 0),
                    "HP": as_int(row.get("hp"), 0),
                    "Element": row.get("element_display"),
                    "ID": row.get("id"),
                }
            )
    return pd.DataFrame(rows)


def render_maps(df: pd.DataFrame) -> None:
    st.subheader("Maps")
    if df.empty:
        st.info("No map data is available under the current filters.")
        return
    map_monsters = build_map_monster_rows(df)
    if map_monsters.empty:
        st.info("No parsed spawn locations are available.")
        return
    best_idx = map_monsters.groupby("Map")["Map score"].idxmax()
    best = map_monsters.loc[best_idx, ["Map", "Monster", "Map score"]].rename(columns={"Monster": "Highest-score mob", "Map score": "Highest mob score"})
    grouped = (
        map_monsters.groupby("Map")
        .agg(
            Monsters=("Monster", "count"),
            Total_spawns=("Spawn count", "sum"),
            Total_map_score=("Map score", "sum"),
            Average_EV=("EV / kill", "mean"),
            Best_EV=("EV / kill", "max"),
        )
        .reset_index()
        .merge(best, on="Map", how="left")
        .sort_values("Total_map_score", ascending=False)
        .reset_index(drop=True)
    )
    grouped_display = grouped.rename(columns={"Total_spawns": "Total spawns", "Total_map_score": "Total map score", "Average_EV": "Average EV", "Best_EV": "Best EV"})
    selected = select_row_from_table(grouped_display, grouped, "Inspect map monsters")
    if selected is None:
        st.info("Select a map row above to show matching monsters here.")
        return
    selected_map = str(selected.get("Map") or "")
    st.subheader(f"Monsters on {selected_map}")
    detail = map_monsters[map_monsters["Map"] == selected_map].sort_values("Map score", ascending=False, kind="stable")
    st.dataframe(detail.drop(columns=["source_idx"], errors="ignore"), use_container_width=True, hide_index=True)


def render_prices(raw_df: pd.DataFrame, selected_table: str, prices: Dict[str, Dict[str, Any]]) -> None:
    st.subheader("Price tables")
    tables = get_price_tables()
    st.dataframe(
        pd.DataFrame(
            [
                {"Table": name, "Entries": len(info["prices"]), "Visibility": info["visibility"]}
                for name, info in tables.items()
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.subheader("Personal table")
    st.caption("Edit overrides directly in the table. Export/import also operates on this same Personal table.")

    personal_df = price_editor_dataframe(st.session_state.personal_prices)
    edited_df = st.data_editor(
        personal_df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="personal_price_editor",
        column_config={
            "AegisName": st.column_config.TextColumn("AegisName", help="Internal item key used in drops_json."),
            "Item": st.column_config.TextColumn("Item", help="Display name for readability."),
            "Manual Price": st.column_config.NumberColumn("Manual Price", min_value=0, step=100, format="%d z"),
        },
    )

    b1, b2, b3 = st.columns(3)
    if b1.button("Apply table edits", use_container_width=True):
        st.session_state.personal_prices = prices_from_editor_dataframe(edited_df)
        st.rerun()
    if b2.button("Clear personal table", disabled=not bool(st.session_state.personal_prices), use_container_width=True):
        st.session_state.personal_prices = {}
        st.rerun()
    b3.download_button(
        "Export personal JSON",
        json.dumps(export_price_payload(st.session_state.personal_prices, "Personal prices"), ensure_ascii=False, indent=2),
        file_name="manual_prices.json",
        mime="application/json",
        use_container_width=True,
        disabled=not bool(st.session_state.personal_prices),
    )

    with st.expander("Add item from catalog"):
        catalog = extract_item_catalog(raw_df)
        if catalog.empty:
            st.info("No item catalog is available. Regenerate monster_ev.csv with drops_json.")
        else:
            def fmt(key: str) -> str:
                row = catalog[catalog["key"] == key].iloc[0]
                return f"{row['name']} ({key}) - NPC {as_int(row['base_sell_price']):,}z"
            key = st.selectbox("Item", catalog["key"].tolist(), format_func=fmt)
            row = catalog[catalog["key"] == key].iloc[0]
            default = as_int(st.session_state.personal_prices.get(key, {}).get("price"), as_int(row["base_sell_price"]))
            price = st.number_input("Manual player price", min_value=0, value=default, step=100)
            if st.button("Add / update selected item", use_container_width=True):
                st.session_state.personal_prices[key] = {"name": str(row["name"]), "price": int(price)}
                st.rerun()

    st.divider()
    st.subheader("Import into Personal table")
    upload = st.file_uploader("Upload manual price JSON", type=["json"])
    pasted = st.text_area("Or paste JSON", height=120)
    replace_existing = st.checkbox("Replace current personal table", value=True)
    if st.button("Import", use_container_width=True):
        try:
            raw_text = upload.getvalue().decode("utf-8") if upload is not None else pasted
            raw = json.loads(raw_text)
            imported = normalize_manual_prices(raw)
            if imported:
                if replace_existing:
                    st.session_state.personal_prices = imported
                else:
                    st.session_state.personal_prices = {**st.session_state.personal_prices, **imported}
                st.success(f"Imported {len(imported):,} price override(s) into Personal table.")
                st.rerun()
            else:
                st.warning("No prices found in that JSON.")
        except Exception as exc:
            st.error(f"Could not import price table: {exc}")

    if selected_table == "Personal table":
        st.info(f"The active price table is Personal table with {len(st.session_state.personal_prices):,} override(s).")


def render_raw(df: pd.DataFrame) -> None:
    st.subheader("Raw data")
    st.download_button("Download filtered CSV", df.to_csv(index=False).encode("utf-8"), file_name="uaro_filtered_monsters.csv", mime="text/csv", disabled=df.empty)
    st.dataframe(df.drop(columns=["drops_json"], errors="ignore"), use_container_width=True, hide_index=True)


def main() -> None:
    st.title("uaRO Farming Planner")
    st.caption("Monster value explorer, farming comparison tool, and price-table sandbox for guild use.")
    init_price_state()
    try:
        raw_df = load_data(CSV_PATH)
    except Exception as exc:
        st.error(str(exc))
        st.stop()
    if raw_df.empty:
        st.warning("`monster_ev.csv` is empty or missing usable rows. Run `python generate_monster_ev.py` with Hercules data, commit the generated CSV, and redeploy Streamlit.")
        st.stop()

    settings, selected_table, prices = render_sidebar(raw_df)
    df = apply_ui_ev_settings(raw_df, settings["multiplier"], settings["use_overcharge"], settings["overcharge_rate"], settings["use_manual"], prices)
    filtered = filter_dataframe(df, settings)
    render_metrics(filtered, selected_table, prices, settings)

    tabs = st.tabs(["Best farms", "Compare", "Maps", "Prices", "Raw data"])
    with tabs[0]:
        render_best_farms(filtered, settings, prices)
    with tabs[1]:
        render_compare(filtered)
    with tabs[2]:
        render_maps(filtered)
    with tabs[3]:
        render_prices(raw_df, selected_table, prices)
    with tabs[4]:
        render_raw(filtered)

    with st.expander("How the numbers are interpreted"):
        st.markdown(f"""
- `EV / kill` is recalculated from `drops_json` using the current drop multiplier: **x{settings['multiplier']:g}**.
- Each drop slot is capped at **100%** before EV is summed.
- Manual player prices override NPC prices and do **not** receive Overcharge.
- `Map score` is `EV / kill * spawn count`; it is a simple density proxy, not zeny/hour.
- Boss-flagged monsters and monsters with MVP drops are hidden by default.
        """.strip())


if __name__ == "__main__":
    main()
