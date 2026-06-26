# UARO Mob Value

Streamlit app for exploring Ragnarok Online monster zeny value from Hercules pre-renewal monster, item, and spawn data.

## Run Locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the app:

```bash
python -m streamlit run RO2.py
```

`RO2.py` reads `monster_ev.csv` by default. The CSV committed to this repo is generated from the current local Hercules-derived data.

## Regenerate Monster Data

`RO1.py` is included as the regeneration script. It expects local source data in this layout:

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
python RO1.py
```

The full Hercules emulator clone is not required for deployment and should not be committed. Only the app files and generated `monster_ev.csv` are needed by Streamlit Community Cloud.

## Streamlit Community Cloud

Use `RO2.py` as the app entrypoint. Streamlit will install packages from `requirements.txt` and load the committed `monster_ev.csv`.

Optional market price overrides can be copied from `manual_prices.example.json` to `manual_prices.json` for local use. `manual_prices.json` is ignored so local market edits are not committed accidentally.

## Expected Value Calculation

`RO1.py` parses Hercules item and monster databases, then joins each monster drop slot with the item's NPC sell value. Hercules drop rates use `10000 = 100%`.

For each monster:

```text
drop_value = item_sell_price * (drop_chance / 10000)
monster_ev = sum(drop_value for all drop slots)
```

The generated CSV stores baseline EV values and a `drops_json` column with the raw drop details. In the Streamlit UI, `RO2.py` recalculates EV live when you change the drop-rate multiplier. Each adjusted drop slot is capped at 100% before it contributes to EV.

Merchant Overcharge applies only to NPC sell values. Manual market prices, when enabled in the app, override NPC prices and are not multiplied by Overcharge.
