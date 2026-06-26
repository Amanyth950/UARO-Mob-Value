# Mob Value Planner

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://zenymob.streamlit.app/)

Live app: https://zenymob.streamlit.app/

Unofficial fan-made utility for exploring pre-renewal RO monster zeny value from monster, item, and spawn data.

This project is not affiliated with any server, publisher, game operator, or emulator project.

The app is organized around searchable farming tables rather than raw database browsing:

- **Best farms** shows the filtered/sorted mob table. Select a mob row to inspect its drop value breakdown underneath the table.
- **Compare** lets players compare multiple mobs side by side.
- **Maps** groups mobs by parsed spawn map. Select a map row to show the mobs that spawn on that map underneath the table.
- **Prices** supports NPC-only, read-only example, and editable personal price tables.
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

`streamlit_app.py` calls `RO2.main()` and reads `monster_ev.csv` by default. The CSV committed to this repo is generated from local source data.

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

Only the app files and generated `monster_ev.csv` are needed by Streamlit Community Cloud. Do not commit full upstream emulator/database clones.

## Price Tables

The app supports three price-table modes without requiring a database:

- **NPC only**: uses item NPC sell values.
- **Example table**: read-only overrides from `manual_prices.example.json`.
- **Personal table**: editable session-local overrides.

The **Prices** tab edits the Personal table directly with a table editor. You can add rows manually, remove rows, update prices inline, or add an item from the generated item catalog. Press **Apply table edits** after changing the table.

Import/export is now part of the Personal table workflow:

- **Export personal JSON** downloads the current Personal table as `manual_prices.json`.
- **Import** loads a JSON file or pasted JSON into the Personal table.
- Imports can either replace the current Personal table or merge into it.

For local testing, `manual_prices.json` is still ignored by git. If present locally, it is loaded as the initial Personal table. The deployed example prices come from `manual_prices.example.json`.

Exported/imported price tables use this wrapper format:

```json
{
  "name": "Example prices",
  "format": "mob-value-planner.price-table.v1",
  "prices": {
    "Elunium": {
      "name": "Elunium",
      "price": 6000
    }
  }
}
```

The legacy flat format from `manual_prices.example.json` and `manual_prices.json` is still accepted.

## Expected Value Calculation

`generate_monster_ev.py` parses item and monster databases, then joins each monster drop slot with the item's NPC sell value. Drop rates use `10000 = 100%`.

For each monster:

```text
drop_value = item_sell_price * (drop_chance / 10000)
expected_value = sum(drop_value for all drop slots)
```

The generated CSV stores baseline expected value data and a `drops_json` column with the raw drop details. In the Streamlit UI, `RO2.py` recalculates expected value live when you change the drop-rate multiplier, Overcharge setting, or selected price table. Each adjusted drop slot is capped at 100% before it contributes to expected value.

Merchant Overcharge applies only to NPC sell values. Manual market prices override NPC prices and are not multiplied by Overcharge.

`Map score` is a simple density proxy: `Expected Value * spawn count`. It is not a true zeny-per-hour estimate.

## Streamlit Community Cloud

Use `streamlit_app.py` as the app entrypoint. Streamlit installs packages from `requirements.txt` and loads the committed `monster_ev.csv`.

The current price-table implementation is intentionally backend-free. Personal edits are session-local unless exported. A future multi-user version should add authentication and persistent storage for named public/private price tables.

## Legacy Entrypoints

`RO1.py` and `RO2.py` remain the functional modules. `generate_monster_ev.py` and `streamlit_app.py` are the preferred public entrypoints.
