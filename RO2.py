import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd
import streamlit as st


st.set_page_config(page_title="RO Monster EV Explorer", layout="wide")
st.title("RO Monster EV Explorer")
st.caption("Hercules pre-renewal monster expected value explorer")


ELEMENT_PREFIXES = ("Ele_", "ELE_", "Element_", "ELEMENT_")


def pretty_enum(value: Any) -> str:
    """Turn Hercules constants like Ele_Water or RC_Demon into compact labels."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    for prefix in ELEMENT_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    for prefix in ("RC_", "Size_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.replace("_", " ").strip()


@st.cache_data
def load_data(csv_path: str = "monster_ev.csv") -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Could not find {csv_path}. Run `python RO1.py` first."
        )

    df = pd.read_csv(path)

    numeric_defaults = {
        "id": 0,
        "level": 0,
        "hp": 0,
        "base_exp": 0,
        "job_exp": 0,
        "best_map_count": 0,
        "map_count": 0,
        "total_spawn_count": 0,
        "drop_count": 0,
        "mvp_drop_count": 0,
        "missing_item_count": 0,
        "expected_value_raw": 0.0,
        "expected_value": 0.0,
        "ev_generation_drop_multiplier": 1.0,
        "ev_overcharge_rate": 1.24,
    }
    for col, default in numeric_defaults.items():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(default)
            if isinstance(default, int):
                df[col] = df[col].astype(int)

    for col in ["is_boss", "has_mvp_drops", "ev_generation_cap_drop_rate", "mvp_drops_included_in_ev"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().isin(["true", "1", "yes"])

    text_columns = [
        "name",
        "sprite_name",
        "internal_name",
        "best_map",
        "race",
        "size",
        "element",
        "spawn_summary",
        "drops_summary",
        "drops_json",
        "ev_sell_mode",
    ]
    for col in text_columns:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    if "element" in df.columns:
        df["element_display"] = df["element"].apply(pretty_enum)

    return df


def safe_min_max(series: pd.Series, default_min: int = 0, default_max: int = 0) -> Tuple[int, int]:
    if series.empty:
        return default_min, default_max
    return int(series.min()), int(series.max())


def parse_drops_json(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [drop for drop in parsed if isinstance(drop, dict)]


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def normalize_manual_prices(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Normalize manual price JSON into {item_key: {name, price}}.

    The canonical key is AegisName when available. Display names are kept only
    as labels. Manual prices represent player-trade values and never receive
    Overcharge.
    """
    normalized: Dict[str, Dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return normalized

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

        if price < 0:
            continue
        normalized[item_key] = {"name": name or item_key, "price": float(price)}

    return normalized


def load_manual_prices(path: str | Path) -> Dict[str, Dict[str, Any]]:
    price_path = Path(path)
    if not price_path.exists():
        return {}
    try:
        raw = json.loads(price_path.read_text(encoding="utf-8"))
    except Exception:
        st.sidebar.warning(f"Could not read manual price file: {price_path}")
        return {}
    return normalize_manual_prices(raw)


def save_manual_prices(path: str | Path, manual_prices: Dict[str, Dict[str, Any]]) -> None:
    price_path = Path(path)
    price_path.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        str(key): {
            "name": str(value.get("name") or key),
            "price": int(value.get("price")) if float(value.get("price", 0)).is_integer() else float(value.get("price", 0)),
        }
        for key, value in sorted(manual_prices.items(), key=lambda item: str(item[1].get("name") or item[0]).lower())
    }
    price_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def rerun_app() -> None:
    try:
        st.rerun()
    except AttributeError:
        try:
            st.experimental_rerun()
        except AttributeError:
            pass


def drop_item_key(drop: Dict[str, Any]) -> str:
    return str(drop.get("aegis_name") or drop.get("name") or "").strip()


def extract_item_catalog(df: pd.DataFrame) -> pd.DataFrame:
    """Build a distinct item catalog from drops_json for the manual price editor."""
    catalog: Dict[str, Dict[str, Any]] = {}
    if "drops_json" not in df.columns:
        return pd.DataFrame(columns=["key", "name", "aegis_name", "base_sell_price", "drop_slots"])

    for drops in df["drops_json"].apply(parse_drops_json):
        for drop in drops:
            key = drop_item_key(drop)
            if not key:
                continue
            name = str(drop.get("name") or key).strip() or key
            aegis_name = str(drop.get("aegis_name") or key).strip() or key
            base_sell = as_float(drop.get("base_sell_price", drop.get("sell_price")), 0.0)
            existing = catalog.setdefault(
                key,
                {
                    "key": key,
                    "name": name,
                    "aegis_name": aegis_name,
                    "base_sell_price": base_sell,
                    "drop_slots": 0,
                },
            )
            existing["drop_slots"] += 1
            if not existing.get("base_sell_price") and base_sell:
                existing["base_sell_price"] = base_sell

    if not catalog:
        return pd.DataFrame(columns=["key", "name", "aegis_name", "base_sell_price", "drop_slots"])
    return pd.DataFrame(catalog.values()).sort_values(["name", "aegis_name"], kind="stable").reset_index(drop=True)


def manual_price_for_drop(drop: Dict[str, Any], manual_prices: Dict[str, Dict[str, Any]]) -> float | None:
    """Return the manual player-trade price for a drop, if one is configured."""
    keys = [drop_item_key(drop), str(drop.get("name") or "").strip()]
    for key in keys:
        if key and key in manual_prices:
            price = as_float(manual_prices[key].get("price"), -1.0)
            if price >= 0:
                return price
    return None


def price_source_for_drop(
    drop: Dict[str, Any],
    use_overcharge: bool,
    use_manual_prices: bool,
    manual_prices: Dict[str, Dict[str, Any]],
) -> str:
    if use_manual_prices and manual_price_for_drop(drop, manual_prices) is not None:
        return "Manual"
    if use_overcharge and not bool(drop.get("ignore_overcharge")):
        return "NPC + Overcharge"
    return "NPC"


def adjusted_drop_chance(raw_chance: Any, multiplier: float) -> float:
    """Hercules uses 10000 = 100%; cap each adjusted drop slot at 100%."""
    return min(max(as_float(raw_chance, 0.0) * multiplier, 0.0), 10000.0)


def effective_sell_price(
    drop: Dict[str, Any],
    use_overcharge: bool,
    overcharge_rate: float,
    use_manual_prices: bool = False,
    manual_prices: Dict[str, Dict[str, Any]] | None = None,
) -> float:
    """Return the sell value used for live EV calculations.

    Manual prices represent player-trade prices. When enabled and present for
    the item, they override NPC sell value and do not receive Overcharge.
    """
    manual_prices = manual_prices or {}
    if use_manual_prices:
        manual_price = manual_price_for_drop(drop, manual_prices)
        if manual_price is not None:
            return manual_price

    base_price = as_float(drop.get("base_sell_price", drop.get("sell_price")), 0.0)
    if use_overcharge and not bool(drop.get("ignore_overcharge")):
        return int(base_price * overcharge_rate)
    return base_price


def recalc_ev_from_drops(
    drops: Iterable[Dict[str, Any]],
    multiplier: float,
    use_overcharge: bool,
    overcharge_rate: float,
    use_manual_prices: bool = False,
    manual_prices: Dict[str, Dict[str, Any]] | None = None,
) -> float:
    total = 0.0
    for drop in drops:
        if drop.get("missing_item"):
            continue
        sell_price = effective_sell_price(drop, use_overcharge, overcharge_rate, use_manual_prices, manual_prices)
        raw_chance = as_float(drop.get("raw_chance"), 0.0)
        total += sell_price * (adjusted_drop_chance(raw_chance, multiplier) / 10000.0)
    return total


def adjusted_drop_summary(
    drops: Iterable[Dict[str, Any]],
    multiplier: float,
    use_overcharge: bool,
    overcharge_rate: float,
    use_manual_prices: bool = False,
    manual_prices: Dict[str, Dict[str, Any]] | None = None,
    limit: int = 12,
) -> str:
    pieces: List[Tuple[float, str]] = []
    for drop in drops:
        name = str(drop.get("name") or drop.get("aegis_name") or "").strip()
        if not name:
            continue
        raw_chance = as_float(drop.get("raw_chance"), 0.0)
        adjusted = adjusted_drop_chance(raw_chance, multiplier)
        sell_price = as_int(effective_sell_price(drop, use_overcharge, overcharge_rate, use_manual_prices, manual_prices), 0)
        source = price_source_for_drop(drop, use_overcharge, use_manual_prices, manual_prices or {})
        marker = " MVP" if bool(drop.get("is_mvp_drop")) else ""
        missing = " missing" if bool(drop.get("missing_item")) else ""
        pieces.append((sell_price * (adjusted / 10000.0), f"{name}{marker} @ {adjusted / 100:.2f}% x {sell_price}z [{source}]{missing}"))
    pieces.sort(key=lambda item: item[0], reverse=True)
    return "; ".join(piece for _, piece in pieces[:limit])




def drop_details_dataframe(
    drops: Iterable[Dict[str, Any]],
    multiplier: float,
    use_overcharge: bool,
    overcharge_rate: float,
    use_manual_prices: bool = False,
    manual_prices: Dict[str, Dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Build a clean per-drop table for the selected monster."""
    rows: List[Dict[str, Any]] = []
    for drop in drops:
        item_name = str(drop.get("name") or drop.get("aegis_name") or "").strip()
        aegis_name = str(drop.get("aegis_name") or "").strip()
        raw_chance = as_float(drop.get("raw_chance"), 0.0)
        adjusted_chance = adjusted_drop_chance(raw_chance, multiplier)
        base_sell_price = as_float(drop.get("base_sell_price", drop.get("sell_price")), 0.0)
        manual_prices = manual_prices or {}
        manual_price = manual_price_for_drop(drop, manual_prices) if use_manual_prices else None
        sell_price = effective_sell_price(drop, use_overcharge, overcharge_rate, use_manual_prices, manual_prices)
        ev = 0.0 if bool(drop.get("missing_item")) else sell_price * (adjusted_chance / 10000.0)
        price_source = price_source_for_drop(drop, use_overcharge, use_manual_prices, manual_prices)
        rows.append(
            {
                "Item": item_name or aegis_name,
                "AegisName": aegis_name,
                "Type": "MVP" if bool(drop.get("is_mvp_drop")) else "Normal",
                "Base Chance": raw_chance / 100.0,
                "Adjusted Chance": adjusted_chance / 100.0,
                "Base Sell": int(base_sell_price) if base_sell_price.is_integer() else base_sell_price,
                "Effective Sell": int(sell_price) if float(sell_price).is_integer() else sell_price,
                "Price Source": price_source,
                "Manual Price": "" if manual_price is None else int(manual_price) if float(manual_price).is_integer() else manual_price,
                "Overcharge Ignored": bool(drop.get("ignore_overcharge")) or manual_price is not None,
                "Adjusted EV": ev,
                "Missing Item": bool(drop.get("missing_item")),
            }
        )

    detail_df = pd.DataFrame(rows)
    if detail_df.empty:
        return detail_df

    total_ev = float(detail_df["Adjusted EV"].sum())
    if total_ev > 0:
        detail_df["EV Share"] = detail_df["Adjusted EV"] / total_ev * 100.0
    else:
        detail_df["EV Share"] = 0.0

    preferred_order = [
        "Item",
        "Adjusted Chance",
        "Effective Sell",
        "EV Share",
        "Price Source",
        "Adjusted EV",
        "Base Chance",
        "Base Sell",
        "Manual Price",
        "Type",
        "AegisName",
        "Overcharge Ignored",
        "Missing Item",
    ]
    remaining = [col for col in detail_df.columns if col not in preferred_order]
    detail_df = detail_df[preferred_order + remaining]

    return detail_df.sort_values("Adjusted EV", ascending=False, kind="stable").reset_index(drop=True)


def render_selected_monster_drops(
    row: pd.Series,
    multiplier: float,
    use_overcharge: bool,
    overcharge_rate: float,
    use_manual_prices: bool,
    manual_prices: Dict[str, Dict[str, Any]],
) -> None:
    """Render selected monster metadata and its drops below the main table."""
    name = str(row.get("name", "Selected monster"))
    monster_id = row.get("id", "")
    level = row.get("level", "")
    element = row.get("element_display", row.get("element", ""))
    best_map = row.get("best_map", "")
    best_count = row.get("best_map_count", "")
    expected_value = as_float(row.get("expected_value"), 0.0)

    st.subheader(f"Drops for {name}")
    meta_cols = st.columns(5)
    meta_cols[0].metric("Monster ID", str(monster_id))
    meta_cols[1].metric("Level", str(level))
    meta_cols[2].metric("Element", str(element) if str(element).strip() else "-")
    meta_cols[3].metric("Best map", str(best_map) if str(best_map).strip() else "-")
    meta_cols[4].metric("Adjusted EV", f"{expected_value:,.2f}")

    drops = parse_drops_json(row.get("drops_json", ""))
    if not drops:
        fallback = str(row.get("adjusted_drops_summary") or row.get("drops_summary") or "").strip()
        if fallback:
            st.info(fallback)
        else:
            st.info("No drop details are available for this monster. Regenerate the CSV with the updated RO1.py if needed.")
        return

    detail_df = drop_details_dataframe(drops, multiplier, use_overcharge, overcharge_rate, use_manual_prices, manual_prices)
    if detail_df.empty:
        st.info("No drops found for this monster.")
        return

    total_ev = detail_df["Adjusted EV"].sum()
    capped_count = int((detail_df["Adjusted Chance"] >= 100.0).sum())
    st.caption(
        f"Drop multiplier x{multiplier:g}; Overcharge {'on' if use_overcharge else 'off'}; "
        f"manual prices {'on' if use_manual_prices else 'off'}; "
        f"each drop slot is capped at 100%. Total adjusted drop EV: {total_ev:,.2f}. "
        f"Capped drops: {capped_count}."
    )
    st.dataframe(
        detail_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Base Chance": st.column_config.NumberColumn("Base Chance", format="%.2f%%"),
            "Adjusted Chance": st.column_config.NumberColumn("Adjusted Chance", format="%.2f%%"),
            "EV Share": st.column_config.NumberColumn("EV Share", format="%.2f%%"),
            "Base Sell": st.column_config.NumberColumn("Base Sell", format="%d z"),
            "Effective Sell": st.column_config.NumberColumn("Effective Sell", format="%d z"),
            "Manual Price": st.column_config.NumberColumn("Manual Price", format="%d z"),
            "Adjusted EV": st.column_config.NumberColumn("Adjusted EV", format="%.2f"),
        },
    )


def select_monster_from_table(table_df: pd.DataFrame, detail_df: pd.DataFrame) -> pd.Series | None:
    """Render the main table and return the selected row when possible.

    Streamlit's dataframe row-selection API exists in newer versions. For older
    versions, the app falls back to a selectbox underneath the unchanged table.
    """
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
        if not detail_df.empty and "name" in detail_df.columns:
            labels = detail_df.apply(
                lambda row: f"{row.get('name', '')} — ID {row.get('id', '')}", axis=1
            ).tolist()
            chosen = st.selectbox(
                "Inspect monster drops",
                options=[""] + labels,
                index=0,
                help="Your Streamlit version does not support clicking dataframe rows, so use this fallback selector.",
            )
            if chosen:
                selected_position = labels.index(chosen)

    if selected_position is None:
        return None
    if selected_position < 0 or selected_position >= len(detail_df):
        return None
    return detail_df.iloc[selected_position]

def apply_ui_drop_multiplier(
    df: pd.DataFrame,
    multiplier: float,
    use_overcharge: bool,
    overcharge_rate: float,
    use_manual_prices: bool = False,
    manual_prices: Dict[str, Dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Recalculate expected_value from drops_json when available.

    If a CSV was generated by an older RO1.py and lacks drops_json, the stored
    expected_value column is left unchanged.
    """
    df = df.copy()
    if "drops_json" not in df.columns:
        df["ev_recalculated_in_ui"] = False
        return df

    parsed = df["drops_json"].apply(parse_drops_json)
    has_details = parsed.apply(bool)
    if has_details.any():
        df["expected_value_base_rate"] = df.get("expected_value_raw", df.get("expected_value", 0.0))
        manual_prices = manual_prices or {}
        recalculated = parsed.apply(
            lambda drops: recalc_ev_from_drops(
                drops, multiplier, use_overcharge, overcharge_rate, use_manual_prices, manual_prices
            )
        )
        adjusted_summary = parsed.apply(
            lambda drops: adjusted_drop_summary(
                drops, multiplier, use_overcharge, overcharge_rate, use_manual_prices, manual_prices
            )
        )
        df.loc[has_details, "expected_value"] = recalculated[has_details]
        df.loc[has_details, "adjusted_drops_summary"] = adjusted_summary[has_details]
        df["ev_recalculated_in_ui"] = has_details
    else:
        df["ev_recalculated_in_ui"] = False
    return df



def render_manual_price_controls(df: pd.DataFrame) -> Tuple[bool, Dict[str, Dict[str, Any]]]:
    """Render sidebar controls for persistent manual player-trade prices."""
    st.sidebar.header("Manual Prices")
    price_file = st.sidebar.text_input(
        "Manual price file",
        value="manual_prices.json",
        help="Stored locally next to the app unless you provide another path.",
    )
    manual_prices = load_manual_prices(price_file)
    use_manual_prices = st.sidebar.checkbox(
        "Use manual player prices",
        value=bool(manual_prices),
        help="When enabled, configured player-trade prices override NPC sell values and do not receive Overcharge.",
    )

    item_catalog = extract_item_catalog(df)
    st.sidebar.caption(f"{len(manual_prices)} manual price override(s) loaded.")

    with st.sidebar.expander("Edit manual prices", expanded=False):
        if item_catalog.empty:
            st.info("No item catalog is available. Regenerate monster_ev.csv with a RO1.py version that writes drops_json.")
            return use_manual_prices, manual_prices

        def format_item(key: str) -> str:
            match = item_catalog[item_catalog["key"] == key]
            if match.empty:
                return key
            row = match.iloc[0]
            base = as_int(row.get("base_sell_price"), 0)
            aegis = str(row.get("aegis_name") or key)
            name = str(row.get("name") or key)
            if aegis and aegis != name:
                return f"{name} ({aegis}) — NPC {base:,}z"
            return f"{name} — NPC {base:,}z"

        keys = item_catalog["key"].tolist()
        selected_key = st.selectbox("Item", options=keys, format_func=format_item)
        selected_row = item_catalog[item_catalog["key"] == selected_key].iloc[0]
        selected_name = str(selected_row.get("name") or selected_key)
        base_price = as_int(selected_row.get("base_sell_price"), 0)
        existing_price = manual_prices.get(selected_key, {}).get("price")
        default_price = int(existing_price) if existing_price is not None else max(base_price, 0)

        manual_price = st.number_input(
            "Manual player price",
            min_value=0,
            value=default_price,
            step=100,
            help="This value is treated as a player-trade price. Overcharge is not applied to it.",
        )
        col_save, col_remove = st.columns(2)
        if col_save.button("Save price", use_container_width=True):
            manual_prices[selected_key] = {"name": selected_name, "price": int(manual_price)}
            save_manual_prices(price_file, manual_prices)
            st.success(f"Saved {selected_name}: {int(manual_price):,}z")
            rerun_app()

        remove_disabled = selected_key not in manual_prices
        if col_remove.button("Remove", use_container_width=True, disabled=remove_disabled):
            manual_prices.pop(selected_key, None)
            save_manual_prices(price_file, manual_prices)
            st.success(f"Removed manual price for {selected_name}")
            rerun_app()

        if manual_prices:
            existing_rows = []
            for key, value in sorted(manual_prices.items(), key=lambda item: str(item[1].get("name") or item[0]).lower()):
                existing_rows.append(
                    {
                        "Item": value.get("name") or key,
                        "AegisName": key,
                        "Manual Price": as_int(value.get("price"), 0),
                    }
                )
            st.dataframe(
                pd.DataFrame(existing_rows),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Manual Price": st.column_config.NumberColumn("Manual Price", format="%d z"),
                },
            )
            if st.button("Clear all manual prices", use_container_width=True):
                save_manual_prices(price_file, {})
                st.success("Cleared all manual prices")
                rerun_app()
        else:
            st.caption("No manual prices saved yet.")

    return use_manual_prices, manual_prices

def main() -> None:
    csv_path = st.sidebar.text_input("CSV path", value="monster_ev.csv")

    try:
        df = load_data(csv_path)
    except Exception as exc:
        st.error(str(exc))
        st.stop()

    st.sidebar.header("Server Rates")
    drop_rate_multiplier = st.sidebar.number_input(
        "Drop rate multiplier",
        min_value=0.0,
        value=5.0,
        step=0.5,
        help="Applied live to each drop slot. Each adjusted drop chance is capped at 100% before EV is summed.",
    )
    use_overcharge = st.sidebar.checkbox(
        "Apply merchant Overcharge (+24%)",
        value=False,
        help="When enabled, item sell values are multiplied by the Overcharge rate before EV is calculated. Items flagged to ignore Overcharge are left unchanged.",
    )
    overcharge_rate = st.sidebar.number_input(
        "Overcharge multiplier",
        min_value=1.0,
        value=1.24,
        step=0.01,
        format="%.2f",
        disabled=not use_overcharge,
    )
    st.sidebar.caption("Adjusted drop chances are capped per item at 100%.")

    use_manual_prices, manual_prices = render_manual_price_controls(df)

    df = apply_ui_drop_multiplier(
        df,
        drop_rate_multiplier,
        use_overcharge,
        overcharge_rate,
        use_manual_prices,
        manual_prices,
    )
    if "ev_recalculated_in_ui" in df.columns and not bool(df["ev_recalculated_in_ui"].any()):
        st.sidebar.warning("This CSV has no drops_json column, so EV cannot be recalculated live. Regenerate it with the updated RO1.py.")

    st.sidebar.header("Search Parameters")

    name_query = st.sidebar.text_input("Monster name contains", value="")
    map_query = st.sidebar.text_input("Map contains", value="")

    if "element" in df.columns:
        element_pairs = (
            df[["element", "element_display"]]
            .dropna()
            .drop_duplicates()
            .sort_values(["element_display", "element"])
        )
        element_label_to_values: Dict[str, List[str]] = {}
        for _, row in element_pairs.iterrows():
            raw = str(row.get("element", "")).strip()
            label = str(row.get("element_display", "")).strip() or raw
            if raw:
                element_label_to_values.setdefault(label, []).append(raw)
        selected_elements = st.sidebar.multiselect(
            "Element",
            options=list(element_label_to_values.keys()),
            default=[],
            help="Leave blank to include all elements.",
        )
    else:
        element_label_to_values = {}
        selected_elements = []

    level_min_available, level_max_available = safe_min_max(df["level"] if "level" in df.columns else pd.Series(dtype=int))
    level_min, level_max = st.sidebar.slider(
        "Level range",
        level_min_available,
        level_max_available,
        (level_min_available, level_max_available),
    )

    spawn_min_available, spawn_max_available = safe_min_max(
        df["best_map_count"] if "best_map_count" in df.columns else pd.Series(dtype=int)
    )
    spawn_min, spawn_max = st.sidebar.slider(
        "Best-map spawn count range",
        spawn_min_available,
        spawn_max_available,
        (spawn_min_available, spawn_max_available),
    )

    ev_threshold = st.sidebar.number_input(
        "Minimum Expected Value",
        min_value=0.0,
        value=0.0,
        step=1.0,
        help="Uses the live adjusted EV after the drop-rate multiplier and 100% cap.",
    )

    if "is_boss" in df.columns:
        include_boss = st.sidebar.checkbox("Include boss-flagged monsters", value=True)
    else:
        include_boss = True

    if "has_mvp_drops" in df.columns:
        include_mvp = st.sidebar.checkbox("Include monsters with MVP drops", value=True)
    else:
        include_mvp = True

    sort_candidates = [
        col
        for col in [
            "expected_value",
            "expected_value_base_rate",
            "expected_value_raw",
            "best_map_count",
            "total_spawn_count",
            "level",
            "hp",
            "name",
        ]
        if col in df.columns
    ]
    sort_by = st.sidebar.selectbox("Sort by", sort_candidates, index=0 if sort_candidates else None)
    ascending = st.sidebar.checkbox("Ascending sort", value=False)

    df_filtered = df.copy()

    if selected_elements and "element" in df_filtered.columns:
        allowed = {raw for label in selected_elements for raw in element_label_to_values.get(label, [])}
        df_filtered = df_filtered[df_filtered["element"].isin(allowed)]
    if "level" in df_filtered.columns:
        df_filtered = df_filtered[(df_filtered["level"] >= level_min) & (df_filtered["level"] <= level_max)]
    if "best_map_count" in df_filtered.columns:
        df_filtered = df_filtered[
            (df_filtered["best_map_count"] >= spawn_min)
            & (df_filtered["best_map_count"] <= spawn_max)
        ]
    if "expected_value" in df_filtered.columns:
        df_filtered = df_filtered[df_filtered["expected_value"] >= ev_threshold]
    if name_query.strip() and "name" in df_filtered.columns:
        q = name_query.strip().lower()
        name_mask = df_filtered["name"].str.lower().str.contains(q, regex=False)
        if "sprite_name" in df_filtered.columns:
            name_mask = name_mask | df_filtered["sprite_name"].str.lower().str.contains(q, regex=False)
        if "internal_name" in df_filtered.columns:
            name_mask = name_mask | df_filtered["internal_name"].str.lower().str.contains(q, regex=False)
        df_filtered = df_filtered[name_mask]
    if map_query.strip() and "best_map" in df_filtered.columns:
        q = map_query.strip().lower()
        map_mask = df_filtered["best_map"].str.lower().str.contains(q, regex=False)
        if "spawn_summary" in df_filtered.columns:
            map_mask = map_mask | df_filtered["spawn_summary"].str.lower().str.contains(q, regex=False)
        df_filtered = df_filtered[map_mask]
    if not include_boss and "is_boss" in df_filtered.columns:
        df_filtered = df_filtered[~df_filtered["is_boss"]]
    if not include_mvp and "has_mvp_drops" in df_filtered.columns:
        df_filtered = df_filtered[~df_filtered["has_mvp_drops"]]

    if sort_by:
        df_filtered = df_filtered.sort_values(sort_by, ascending=ascending, kind="stable")

    total_matches = len(df_filtered)
    total_ev = df_filtered["expected_value"].sum() if "expected_value" in df_filtered.columns else 0.0
    median_ev = df_filtered["expected_value"].median() if total_matches and "expected_value" in df_filtered.columns else 0.0
    max_ev = df_filtered["expected_value"].max() if total_matches and "expected_value" in df_filtered.columns else 0.0

    metric_cols = st.columns(6)
    metric_cols[0].metric("Matching monsters", f"{total_matches:,}")
    metric_cols[1].metric("Max adjusted EV", f"{max_ev:,.2f}")
    metric_cols[2].metric("Median adjusted EV", f"{median_ev:,.2f}")
    metric_cols[3].metric("Drop multiplier", f"x{drop_rate_multiplier:g}")
    metric_cols[4].metric("Overcharge", "on" if use_overcharge else "off")
    metric_cols[5].metric("Manual prices", "on" if use_manual_prices else "off", f"{len(manual_prices)} saved")

    preferred_cols = [
        "name",
        "expected_value",
        "level",
        "hp",
        "element_display",
        "best_map_count",
        "best_map",
        "id",
        "sprite_name",
        "internal_name",
        "race",
        "size",
        "element",
        "element_level",
        "is_boss",
        "map_count",
        "total_spawn_count",
        "drop_count",
        "mvp_drop_count",
        "missing_item_count",
        "expected_value_base_rate",
        "expected_value_raw",
        "adjusted_drops_summary",
        "drops_summary",
        "spawn_summary",
    ]
    visible_cols = [col for col in preferred_cols if col in df_filtered.columns]
    remaining_cols = [col for col in df_filtered.columns if col not in visible_cols and col != "drops_json"]

    st.subheader("Matching Monsters")
    if total_matches:
        st.caption("Click a monster row to inspect its drops below the main table.")
    else:
        st.caption("No monsters match the current filters.")

    detail_df = df_filtered.reset_index(drop=True)
    table_df = detail_df[visible_cols + remaining_cols]
    selected_row = select_monster_from_table(table_df, detail_df)

    if selected_row is not None:
        render_selected_monster_drops(
            selected_row,
            drop_rate_multiplier,
            use_overcharge,
            overcharge_rate,
            use_manual_prices,
            manual_prices,
        )
    elif total_matches:
        st.info("Select a monster row above to show its drops here.")

    with st.expander("Notes"):
        st.markdown(
            f"""
- `expected_value` is recalculated in the UI from `drops_json` using the current drop multiplier: **x{drop_rate_multiplier:g}**.
- Each individual drop slot is capped at **100%** before EV is summed. Example: a 30% drop at x5 becomes 100%, not 150%.
- Merchant Overcharge is optional in the UI. When enabled, NPC sell prices are multiplied by the configured Overcharge rate, default **1.24**.
- Manual player-trade prices are stored in `manual_prices.json` by default. When enabled, they override NPC prices and do **not** receive Overcharge.
- `expected_value_base_rate` / `expected_value_raw` are the baseline x1 values generated from Hercules data.
- `best_map_count` is aggregated from permanent spawn files, not from an HTML database.
- Server-specific uaRO customizations are included only if you put those customized files into `data/` before generation.
            """.strip()
        )


if __name__ == "__main__":
    main()
