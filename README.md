# UARO Mob Value

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://uaro-zenymob.streamlit.app/)

Live app: https://uaro-zenymob.streamlit.app/

Streamlit farming planner for exploring Ragnarok Online monster zeny value from Hercules pre-renewal monster, item, and spawn data.

The app is organized around searchable farming tables rather than raw database browsing:

- **Best farms** shows the filtered/sorted mob table. Select a mob row to inspect its drop value breakdown underneath the table.
- **Compare** lets players compare multiple mobs side by side.
- **Maps** groups mobs by parsed spawn map. Select a map row to show the mobs that spawn on that map underneath the table.
- **Prices** supports NPC-only, guild/default, personal session, and imported/shared price tables.
- **Raw data** keeps the old spreadsheet-style escape hatch.

## Run Locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the app:

```bash
python -m streamlit run streamlit_app.py
```

`streamlit_app.py` calls `RO2.main()` and reads `monster_ev.csv` by default. The CSV committed to this repo is generated from local Hercules-derived data.

## Regenerate Monster Data

`generate_monster_ev.py` calls `RO1.main()` and expects local source data in this layout:

```text
data/
  mob_db.conf
  item_db.conf
  mob_db2.conf
  item_db2.conf
  mobs_pre_re/
  mobs_common/
```

Regenerate outputs:

```bash
python generate_monster_ev.py
```

The full Hercules emulator clone is not required for deployment and should not be committed. Only the app files and generated `monster_ev.csv` are needed by Streamlit Community Cloud.

## Price Tables

The app supports several price-table modes without requiring a database yet:

- **NPC only**: uses item NPC sell values.
- **Guild/default table**: read from `guild_prices.json` when present, then `guild_prices.example.json`, then `manual_prices.example.json`.
- **Personal session**: editable in the UI for the current browser session.
- **Imported/shared table**: upload or paste exported JSON from another player.

For a curated guild table, copy `guild_prices.example.json` to `guild_prices.json`, edit prices, and commit it. For personal/local testing, use `manual_prices.json`; it is ignored by git so local market edits are not committed accidentally.

Exported/imported price tables use this wrapper format:

```json
{
  "name": "Example prices",
  "format": "uaro-mob-value.price-table.v1",
  "prices": {
    "Elunium": {
      "name": "Elunium",
      "price": 6000
    }
  }
}
```

The legacy flat format from `manual_prices.example.json` is still accepted.

## Expected Value Calculation

`generate_monster_ev.py` parses Hercules item and monster databases, then joins each monster drop slot with the item's NPC sell value. Hercules drop rates use `10000 = 100%`.

For each monster:

```text
drop_value = item_sell_price * (drop_chance / 10000)
monster_ev = sum(drop_value for all drop slots)
```

The generated CSV stores baseline EV values and a `drops_json` column with the raw drop details. In the Streamlit UI, `RO2.py` recalculates EV live when you change the drop-rate multiplier, Overcharge setting, or selected price table. Each adjusted drop slot is capped at 100% before it contributes to EV.

Merchant Overcharge applies only to NPC sell values. Manual market prices override NPC prices and are not multiplied by Overcharge.

`Map score` is a simple density proxy: `EV / kill * spawn count`. It is not a true zeny-per-hour estimate.

`Income profile` estimates whether a monster is stable, swingy, or lottery-like based on how concentrated its EV is in the top drop.

## Streamlit Community Cloud

Use `streamlit_app.py` as the app entrypoint. Streamlit installs packages from `requirements.txt` and loads the committed `monster_ev.csv`.

The current price-table implementation is intentionally backend-free. Personal edits are session-local unless exported or saved to a local JSON file. A future multi-user version should add authentication and persistent storage for named public/private price tables.

## Legacy Entrypoints

`RO1.py` and `RO2.py` remain the functional modules. `generate_monster_ev.py` and `streamlit_app.py` are the preferred public entrypoints.
