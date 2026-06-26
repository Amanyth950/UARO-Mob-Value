#!/usr/bin/env python3
"""
Build a Ragnarok Online monster expected-value CSV from Hercules pre-renewal data.

Expected project layout, matching the files you copied from Hercules:

  data/
    mob_db.conf
    item_db.conf
    mob_db2.conf          optional
    item_db2.conf         optional
    mobs_pre_re/          optional but recommended
    mobs_common/          optional

Run:

  python RO1_hercules.py

Outputs:

  monster_ev.csv
  monsters_hercules.json
  items_hercules.json

Notes:
- Hercules mob drop chances use 10000 = 100%.
- Item EV uses NPC Sell price by default.
- Spawn counts are aggregated from permanent spawn lines under the spawn dirs.
- The CSV includes drops_json so RO2 can recalculate EV live with a UI multiplier.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import logging
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("ro_hercules_ev")


Token = Tuple[str, Any]


class HerculesConfError(ValueError):
    """Raised when a Hercules .conf file cannot be parsed."""


class DuplicateValues(list):
    """Container used internally when a Hercules object repeats the same key.

    Hercules drop blocks can legally repeat an AegisName, for example two
    independent Apple drop slots on the same monster. A normal dict would
    overwrite the first value; this marker lets the normalizer sum all slots.
    """


class HerculesConfParser:
    """
    Small parser for the subset of libconfig-style syntax used by Hercules DBs.

    It supports:
    - root assignments such as mob_db: (...)
    - dictionaries: { Key: Value }
    - tuples/lists: (...), [...]
    - strings, numbers, booleans, constants/identifiers
    - script strings: <" ... ">
    - // line comments and /* block comments */
    """

    _PUNCT = set("{}()[]:,;")

    def __init__(self, text: str, source: str = "<memory>") -> None:
        self.text = text
        self.source = source
        self.tokens: List[Token] = list(self._tokenize(text))
        self.pos = 0

    @classmethod
    def parse_file(cls, path: Path) -> Dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return cls(text, str(path)).parse()

    def parse(self) -> Dict[str, Any]:
        if not self.tokens:
            return {}

        # Common Hercules DB shape: root_key: (...)
        if self._peek_type() == "IDENT" and self._peek_type(1) == ":":
            key = str(self._advance()[1])
            self._expect(":")
            value = self._parse_value()
            return {key: value}

        value = self._parse_value()
        return {"_root": value}

    def _tokenize(self, text: str) -> Iterable[Token]:
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]

            if ch.isspace():
                i += 1
                continue

            # // line comment
            if ch == "/" and i + 1 < n and text[i + 1] == "/":
                i += 2
                while i < n and text[i] not in "\r\n":
                    i += 1
                continue

            # /* block comment */
            if ch == "/" and i + 1 < n and text[i + 1] == "*":
                end = text.find("*/", i + 2)
                i = n if end == -1 else end + 2
                continue

            # Hercules script string: <" ... ">
            if ch == "<" and i + 1 < n and text[i + 1] == '"':
                i += 2
                buf: List[str] = []
                while i < n:
                    if text[i] == '"' and i + 1 < n and text[i + 1] == ">":
                        i += 2
                        break
                    if text[i] == "\\" and i + 1 < n:
                        buf.append(text[i + 1])
                        i += 2
                        continue
                    buf.append(text[i])
                    i += 1
                yield ("STRING", "".join(buf))
                continue

            if ch == '"':
                i += 1
                buf = []
                while i < n:
                    if text[i] == '"':
                        i += 1
                        break
                    if text[i] == "\\" and i + 1 < n:
                        esc = text[i + 1]
                        if esc == "n":
                            buf.append("\n")
                        elif esc == "t":
                            buf.append("\t")
                        else:
                            buf.append(esc)
                        i += 2
                        continue
                    buf.append(text[i])
                    i += 1
                yield ("STRING", "".join(buf))
                continue

            if ch in self._PUNCT:
                yield (ch, ch)
                i += 1
                continue

            # Number, including hex and negatives.
            if ch == "-" or ch.isdigit():
                start = i
                if ch == "-":
                    i += 1
                if i + 1 < n and text[i] == "0" and text[i + 1] in "xX":
                    i += 2
                    while i < n and (text[i].isdigit() or text[i].lower() in "abcdef"):
                        i += 1
                    raw = text[start:i]
                    yield ("NUMBER", int(raw, 16))
                    continue
                while i < n and text[i].isdigit():
                    i += 1
                # Minimal float support, just in case a custom DB uses it.
                if i < n and text[i] == ".":
                    i += 1
                    while i < n and text[i].isdigit():
                        i += 1
                    raw = text[start:i]
                    yield ("NUMBER", float(raw))
                else:
                    raw = text[start:i]
                    yield ("NUMBER", int(raw))
                continue

            # Identifier / constant. Hercules AegisNames and enum constants fit here.
            if ch.isalpha() or ch == "_":
                start = i
                i += 1
                while i < n and (text[i].isalnum() or text[i] == "_"):
                    i += 1
                ident = text[start:i]
                if ident == "true":
                    yield ("BOOL", True)
                elif ident == "false":
                    yield ("BOOL", False)
                else:
                    yield ("IDENT", ident)
                continue

            raise HerculesConfError(f"Unexpected character {ch!r} in {self.source} at offset {i}")

    def _peek(self, offset: int = 0) -> Optional[Token]:
        idx = self.pos + offset
        if idx >= len(self.tokens):
            return None
        return self.tokens[idx]

    def _peek_type(self, offset: int = 0) -> Optional[str]:
        token = self._peek(offset)
        return token[0] if token else None

    def _advance(self) -> Token:
        if self.pos >= len(self.tokens):
            raise HerculesConfError(f"Unexpected end of input in {self.source}")
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def _expect(self, typ: str) -> Token:
        token = self._advance()
        if token[0] != typ:
            raise HerculesConfError(f"Expected {typ!r}, got {token[0]!r} in {self.source}")
        return token

    def _consume_if(self, typ: str) -> bool:
        if self._peek_type() == typ:
            self.pos += 1
            return True
        return False

    def _parse_value(self) -> Any:
        token = self._advance()
        typ, value = token

        if typ == "{":
            return self._parse_object()
        if typ == "(":
            return self._parse_sequence(")")
        if typ == "[":
            return self._parse_sequence("]")
        if typ in {"STRING", "NUMBER", "BOOL"}:
            return value
        if typ == "IDENT":
            # Constants such as IT_ETC, RC_Brute, Ele_Fire are kept as strings.
            return value

        raise HerculesConfError(f"Unexpected token {typ!r} in {self.source}")

    def _parse_sequence(self, end_typ: str) -> List[Any]:
        values: List[Any] = []
        while self._peek_type() != end_typ:
            if self._peek_type() is None:
                raise HerculesConfError(f"Unclosed sequence in {self.source}")
            if self._consume_if(",") or self._consume_if(";"):
                continue
            values.append(self._parse_value())
            self._consume_if(",")
            self._consume_if(";")
        self._expect(end_typ)
        return values

    def _parse_object(self) -> Dict[str, Any]:
        obj: Dict[str, Any] = {}
        while self._peek_type() != "}":
            if self._peek_type() is None:
                raise HerculesConfError(f"Unclosed object in {self.source}")
            if self._consume_if(",") or self._consume_if(";"):
                continue

            key_token = self._advance()
            if key_token[0] not in {"IDENT", "STRING", "NUMBER"}:
                raise HerculesConfError(
                    f"Expected object key, got {key_token[0]!r} in {self.source}"
                )
            key = str(key_token[1])

            # Most Hercules objects use Key: Value. Some flag blocks in custom
            # databases appear as bare identifiers, e.g. { CanMove Looter }.
            # Treat those as boolean flags instead of failing the whole parse.
            if self._consume_if(":"):
                value = self._parse_value()
            else:
                value = True

            # Drop blocks may legally repeat an AegisName. Preserve duplicates
            # so the EV calculation counts every independent drop slot.
            if key in obj:
                if isinstance(obj[key], DuplicateValues):
                    obj[key].append(value)
                else:
                    obj[key] = DuplicateValues([obj[key], value])
            else:
                obj[key] = value

            self._consume_if(",")
            self._consume_if(";")

        self._expect("}")
        return obj


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return default
        try:
            return int(value, 0)
        except ValueError:
            return default
    return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "yes", "1"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _first_int(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, list) and value:
        return _first_int(value[0])
    return _as_int(value, None)


def _value_to_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _load_db_entries(path: Path, root_key: str) -> List[Dict[str, Any]]:
    if not path.exists():
        logger.info("Skipping missing %s", path)
        return []

    parsed = HerculesConfParser.parse_file(path)
    root = parsed.get(root_key)
    if root is None:
        logger.warning("%s does not contain root key %s", path, root_key)
        return []
    if not isinstance(root, list):
        logger.warning("%s root key %s is not a list", path, root_key)
        return []

    entries = [entry for entry in root if isinstance(entry, dict)]
    logger.info("Loaded %d raw %s entries from %s", len(entries), root_key, path)
    return entries


@dataclass
class ItemDb:
    by_id: Dict[int, Dict[str, Any]]
    by_aegis: Dict[str, Dict[str, Any]]


def _normalize_item(
    raw: Dict[str, Any],
    existing_by_id: Dict[int, Dict[str, Any]],
    existing_by_aegis: Dict[str, Dict[str, Any]],
    source: str,
) -> Optional[Dict[str, Any]]:
    item_id = _as_int(raw.get("Id"), None)
    if item_id is None:
        return None

    base: Dict[str, Any] = {}

    if _as_bool(raw.get("Inherit"), False) and item_id in existing_by_id:
        base = copy.deepcopy(existing_by_id[item_id])

    clone_ref = raw.get("CloneItem")
    if clone_ref is not None:
        clone: Optional[Dict[str, Any]] = None
        clone_id = _as_int(clone_ref, None)
        if clone_id is not None:
            clone = existing_by_id.get(clone_id)
        if clone is None:
            clone = existing_by_aegis.get(str(clone_ref))
        if clone:
            # Clone first, then apply inherited/base and current fields.
            cloned = copy.deepcopy(clone)
            cloned.pop("id", None)
            cloned.pop("aegis_name", None)
            base = {**cloned, **base}

    item = {**base, **raw}
    aegis_name = _value_to_string(item.get("AegisName"))
    display_name = _value_to_string(item.get("Name")) or aegis_name or str(item_id)

    buy = _as_int(item.get("Buy"), None)
    sell = _as_int(item.get("Sell"), None)
    if sell is None and buy is not None:
        sell = buy // 2
    if buy is None and sell is not None:
        buy = sell * 2
    if buy is None:
        buy = 0
    if sell is None:
        sell = 0

    normalized = {
        "id": item_id,
        "aegis_name": aegis_name or f"ITEM_{item_id}",
        "name": display_name,
        "type": _value_to_string(item.get("Type")) or "IT_ETC",
        "buy": buy,
        "sell": sell,
        "ignore_overcharge": _as_bool(item.get("IgnoreOvercharge"), False),
        "source": source,
    }
    return normalized


def load_items(item_db: Path, item_db2: Optional[Path] = None) -> ItemDb:
    by_id: Dict[int, Dict[str, Any]] = {}
    by_aegis: Dict[str, Dict[str, Any]] = {}

    for path in [item_db, item_db2]:
        if path is None or not path.exists():
            continue
        for raw in _load_db_entries(path, "item_db"):
            item = _normalize_item(raw, by_id, by_aegis, str(path))
            if not item:
                continue
            by_id[item["id"]] = item
            by_aegis[item["aegis_name"]] = item

    logger.info("Normalized %d items", len(by_id))
    return ItemDb(by_id=by_id, by_aegis=by_aegis)


def _iter_drop_slots(raw_chance: Any) -> Iterable[Any]:
    if isinstance(raw_chance, DuplicateValues):
        yield from raw_chance
    else:
        yield raw_chance


def _normalize_drops(raw_drops: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_drops, dict):
        return []

    drops: List[Dict[str, Any]] = []
    for aegis_name, raw_chance in raw_drops.items():
        for slot_value in _iter_drop_slots(raw_chance):
            chance = _first_int(slot_value)
            if chance is None or chance <= 0:
                continue
            drops.append(
                {
                    "aegis_name": str(aegis_name),
                    "chance": int(chance),
                    "chance_percent": round(chance / 100.0, 4),
                }
            )
    drops.sort(key=lambda d: (-int(d["chance"]), d["aegis_name"]))
    return drops


def _normalize_monster(
    raw: Dict[str, Any],
    existing_by_id: Dict[int, Dict[str, Any]],
    source: str,
) -> Optional[Dict[str, Any]]:
    mob_id = _as_int(raw.get("Id"), None)
    if mob_id is None:
        return None

    base: Dict[str, Any] = {}
    if _as_bool(raw.get("Inherit"), False) and mob_id in existing_by_id:
        base = copy.deepcopy(existing_by_id[mob_id])

    mob = {**base, **raw}
    mode = mob.get("Mode") if isinstance(mob.get("Mode"), dict) else {}
    drops = _normalize_drops(mob.get("Drops"))
    mvp_drops = _normalize_drops(mob.get("MvpDrops"))
    mvp_exp = _as_int(mob.get("MvpExp"), 0) or 0

    sprite_name = _value_to_string(mob.get("SpriteName")) or _value_to_string(mob.get("Name")) or str(mob_id)
    internal_name = _value_to_string(mob.get("Name")) or sprite_name
    display_name = _value_to_string(mob.get("JName")) or internal_name

    element = mob.get("Element")
    element_type = None
    element_level = None
    if isinstance(element, list):
        if len(element) >= 1:
            element_type = _value_to_string(element[0])
        if len(element) >= 2:
            element_level = _as_int(element[1], None)
    elif isinstance(element, str):
        element_type = element

    normalized = {
        "id": mob_id,
        "sprite_name": sprite_name,
        "internal_name": internal_name,
        "name": display_name,
        "level": _as_int(mob.get("Lv"), 1) or 1,
        "hp": _as_int(mob.get("Hp"), 1) or 1,
        "base_exp": _as_int(mob.get("Exp"), 0) or 0,
        "job_exp": _as_int(mob.get("JExp"), 0) or 0,
        "race": _value_to_string(mob.get("Race")) or "RC_Formless",
        "size": _value_to_string(mob.get("Size")) or "Size_Medium",
        "element": element_type,
        "element_level": element_level,
        "mode": mode,
        "is_boss": _as_bool(mode.get("Boss"), False),
        "mvp_exp": mvp_exp,
        "has_mvp_drops": bool(mvp_drops) or mvp_exp > 0,
        "drops": drops,
        "mvp_drops": mvp_drops,
        "source": source,
    }
    return normalized


def load_monsters(mob_db: Path, mob_db2: Optional[Path] = None) -> Dict[int, Dict[str, Any]]:
    monsters: Dict[int, Dict[str, Any]] = {}

    for path in [mob_db, mob_db2]:
        if path is None or not path.exists():
            continue
        for raw in _load_db_entries(path, "mob_db"):
            monster = _normalize_monster(raw, monsters, str(path))
            if not monster:
                continue
            monsters[monster["id"]] = monster

    logger.info("Normalized %d monsters", len(monsters))
    return monsters


def strip_script_comments(text: str) -> str:
    """Remove // and /* */ comments while preserving strings well enough for spawn parsing."""
    out: List[str] = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            if i < n:
                out.append(text[i])
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] in "\r\n":
                    out.append(text[i])
                i += 1
            i += 2 if i + 1 < n else 0
            continue

        out.append(ch)
        i += 1

    return "".join(out)


SPAWN_RE = re.compile(
    r"^\s*([A-Za-z0-9_]+),[^\r\n\t]*[\t ]+monster[\t ]+(.+?)[\t ]+(-?\d+)\s*,\s*(\d+)\b",
    re.IGNORECASE,
)


def parse_spawn_line(line: str) -> Optional[Tuple[str, int, int]]:
    match = SPAWN_RE.match(line)
    if not match:
        return None

    map_name = match.group(1)
    mob_id = int(match.group(3))
    amount = int(match.group(4))

    # Negative IDs are random branch groups, not concrete monster rows.
    if mob_id <= 0 or amount <= 0:
        return None

    return map_name, mob_id, amount


def load_spawns(spawn_dirs: Iterable[Path]) -> Dict[int, Counter]:
    spawn_counts: Dict[int, Counter] = defaultdict(Counter)
    files_scanned = 0
    lines_matched = 0

    for spawn_dir in spawn_dirs:
        if not spawn_dir.exists():
            logger.info("Skipping missing spawn dir %s", spawn_dir)
            continue
        if spawn_dir.is_file():
            files = [spawn_dir]
        else:
            files = [p for p in spawn_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".txt", ".conf"}]

        for path in files:
            files_scanned += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            text = strip_script_comments(text)
            for line in text.splitlines():
                parsed = parse_spawn_line(line)
                if parsed is None:
                    continue
                map_name, mob_id, amount = parsed
                spawn_counts[mob_id][map_name] += amount
                lines_matched += 1

    logger.info("Scanned %d spawn files and matched %d permanent spawn lines", files_scanned, lines_matched)
    logger.info("Found spawn counts for %d monsters", len(spawn_counts))
    return spawn_counts


def adjusted_chance(raw_chance: int, multiplier: float, cap: bool) -> float:
    chance = float(raw_chance) * multiplier
    if cap:
        chance = min(chance, 10000.0)
    return max(0.0, chance)


def sale_price(item: Dict[str, Any], sell_mode: str, overcharge_rate: float) -> int:
    base_sell = _as_int(item.get("sell"), 0) or 0
    if sell_mode == "overcharge" and not _as_bool(item.get("ignore_overcharge"), False):
        return int(math.floor(base_sell * overcharge_rate))
    return base_sell


def compute_monster_ev(
    monster: Dict[str, Any],
    items: ItemDb,
    drop_rate_multiplier: float,
    cap_drop_rate: bool,
    include_mvp_drops: bool,
    sell_mode: str,
    overcharge_rate: float,
) -> Tuple[float, float, int, List[str], List[Dict[str, Any]]]:
    """Return raw_ev, adjusted_ev, missing_item_count, drop summary, and drop detail rows.

    Drop detail rows are written to the CSV as JSON so the Streamlit UI can
    recalculate EV live when a player changes the drop-rate multiplier. The
    100% cap is applied per drop slot before EV is summed.
    """
    raw_ev = 0.0
    adjusted_ev = 0.0
    missing = 0
    summary: List[str] = []
    details: List[Dict[str, Any]] = []

    drops: List[Tuple[Dict[str, Any], bool]] = [(drop, False) for drop in monster.get("drops", [])]
    if include_mvp_drops:
        drops.extend((drop, True) for drop in monster.get("mvp_drops", []))

    for drop, is_mvp_drop in drops:
        aegis_name = str(drop.get("aegis_name", ""))
        raw_chance = _as_int(drop.get("chance"), 0) or 0
        adj_chance = adjusted_chance(raw_chance, drop_rate_multiplier, cap_drop_rate)
        item = items.by_aegis.get(aegis_name)

        if item is None:
            missing += 1
            summary.append(f"{aegis_name} @ {raw_chance / 100:.2f}% (missing item)")
            details.append(
                {
                    "aegis_name": aegis_name,
                    "name": aegis_name,
                    "raw_chance": raw_chance,
                    "raw_chance_percent": round(raw_chance / 100.0, 4),
                    "adjusted_chance_generation": round(adj_chance, 4),
                    "adjusted_chance_percent_generation": round(adj_chance / 100.0, 4),
                    "base_sell_price": 0,
                    "sell_price": 0,
                    "sell_price_generation": 0,
                    "ignore_overcharge": False,
                    "ev_raw": 0.0,
                    "ev_generation": 0.0,
                    "is_mvp_drop": is_mvp_drop,
                    "missing_item": True,
                }
            )
            continue

        base_price = _as_int(item.get("sell"), 0) or 0
        price = sale_price(item, sell_mode, overcharge_rate)
        raw_drop_ev = price * (raw_chance / 10000.0)
        adjusted_drop_ev = price * (adj_chance / 10000.0)
        raw_ev += raw_drop_ev
        adjusted_ev += adjusted_drop_ev
        summary.append(f"{item['name']} @ {raw_chance / 100:.2f}% x {price}z")
        details.append(
            {
                "aegis_name": aegis_name,
                "item_id": item.get("id"),
                "name": item.get("name") or aegis_name,
                "raw_chance": raw_chance,
                "raw_chance_percent": round(raw_chance / 100.0, 4),
                "adjusted_chance_generation": round(adj_chance, 4),
                "adjusted_chance_percent_generation": round(adj_chance / 100.0, 4),
                "base_sell_price": base_price,
                "sell_price": price,
                "sell_price_generation": price,
                "ignore_overcharge": _as_bool(item.get("ignore_overcharge"), False),
                "ev_raw": round(raw_drop_ev, 6),
                "ev_generation": round(adjusted_drop_ev, 6),
                "is_mvp_drop": is_mvp_drop,
                "missing_item": False,
            }
        )

    return raw_ev, adjusted_ev, missing, summary, details


def best_spawn(spawns_for_mob: Counter) -> Tuple[Optional[str], int, int, int, str]:
    if not spawns_for_mob:
        return None, 0, 0, 0, ""
    ordered = sorted(spawns_for_mob.items(), key=lambda kv: (-kv[1], kv[0]))
    best_map, best_count = ordered[0]
    total_spawn_count = sum(spawns_for_mob.values())
    map_count = len(spawns_for_mob)
    spawn_summary = "; ".join(f"{map_name}:{count}" for map_name, count in ordered[:12])
    return best_map, best_count, map_count, total_spawn_count, spawn_summary


def build_outputs(
    data_dir: Path,
    mob_db: Path,
    item_db: Path,
    mob_db2: Optional[Path],
    item_db2: Optional[Path],
    spawn_dirs: List[Path],
    csv_out: Path,
    monsters_out: Path,
    items_out: Path,
    drop_rate_multiplier: float,
    cap_drop_rate: bool,
    include_mvp_drops: bool,
    sell_mode: str,
    overcharge_rate: float,
) -> None:
    logger.info("Loading Hercules item DB")
    items = load_items(item_db, item_db2)

    logger.info("Loading Hercules monster DB")
    monsters = load_monsters(mob_db, mob_db2)

    logger.info("Loading permanent spawns")
    spawns = load_spawns(spawn_dirs)

    rows: List[Dict[str, Any]] = []
    monster_json: List[Dict[str, Any]] = []

    for mob_id in sorted(monsters):
        monster = copy.deepcopy(monsters[mob_id])
        spawns_for_mob = spawns.get(mob_id, Counter())
        best_map, best_count, map_count, total_spawn_count, spawn_summary = best_spawn(spawns_for_mob)
        raw_ev, adjusted_ev, missing_items, drop_summary, drop_details = compute_monster_ev(
            monster=monster,
            items=items,
            drop_rate_multiplier=drop_rate_multiplier,
            cap_drop_rate=cap_drop_rate,
            include_mvp_drops=include_mvp_drops,
            sell_mode=sell_mode,
            overcharge_rate=overcharge_rate,
        )

        monster["spawns"] = dict(sorted(spawns_for_mob.items(), key=lambda kv: (-kv[1], kv[0])))
        monster["best_map"] = best_map
        monster["best_map_count"] = best_count
        monster["map_count"] = map_count
        monster["total_spawn_count"] = total_spawn_count
        monster["expected_value_raw"] = round(raw_ev, 4)
        monster["expected_value"] = round(adjusted_ev, 4)
        monster["missing_item_count"] = missing_items
        monster["drop_details"] = drop_details
        monster_json.append(monster)

        rows.append(
            {
                "id": mob_id,
                "sprite_name": monster.get("sprite_name"),
                "internal_name": monster.get("internal_name"),
                "name": monster.get("name"),
                "level": monster.get("level"),
                "hp": monster.get("hp"),
                "base_exp": monster.get("base_exp"),
                "job_exp": monster.get("job_exp"),
                "race": monster.get("race"),
                "size": monster.get("size"),
                "element": monster.get("element"),
                "element_level": monster.get("element_level"),
                "is_boss": monster.get("is_boss"),
                "has_mvp_drops": monster.get("has_mvp_drops"),
                "best_map": best_map or "",
                "best_map_count": best_count,
                "map_count": map_count,
                "total_spawn_count": total_spawn_count,
                "drop_count": len(monster.get("drops", [])),
                "mvp_drop_count": len(monster.get("mvp_drops", [])),
                "missing_item_count": missing_items,
                "expected_value_raw": f"{raw_ev:.2f}",
                "expected_value": f"{adjusted_ev:.2f}",
                "ev_generation_drop_multiplier": drop_rate_multiplier,
                "ev_generation_cap_drop_rate": cap_drop_rate,
                "ev_sell_mode": sell_mode,
                "ev_overcharge_rate": overcharge_rate,
                "mvp_drops_included_in_ev": include_mvp_drops,
                "spawn_summary": spawn_summary,
                "drops_summary": "; ".join(drop_summary[:20]),
                "drops_json": json.dumps(drop_details, ensure_ascii=False, separators=(",", ":")),
            }
        )

    csv_out.parent.mkdir(parents=True, exist_ok=True)
    with csv_out.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(rows[0].keys()) if rows else [
            "id", "name", "level", "best_map", "best_map_count", "expected_value"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    monsters_out.parent.mkdir(parents=True, exist_ok=True)
    with monsters_out.open("w", encoding="utf-8") as f:
        json.dump(monster_json, f, ensure_ascii=False, indent=2)

    item_rows = sorted(items.by_id.values(), key=lambda item: item["id"])
    items_out.parent.mkdir(parents=True, exist_ok=True)
    with items_out.open("w", encoding="utf-8") as f:
        json.dump(item_rows, f, ensure_ascii=False, indent=2)

    logger.info("Wrote %d monster rows to %s", len(rows), csv_out)
    logger.info("Wrote %d monster JSON rows to %s", len(monster_json), monsters_out)
    logger.info("Wrote %d item JSON rows to %s", len(item_rows), items_out)


def default_spawn_dirs(data_dir: Path) -> List[Path]:
    candidates = [data_dir / "mobs_pre_re", data_dir / "mobs_common"]
    return [path for path in candidates if path.exists()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build monster_ev.csv from Hercules pre-renewal DB files.")
    parser.add_argument("--data-dir", default="data", help="Folder containing copied Hercules files. Default: data")
    parser.add_argument("--mob-db", default=None, help="Path to mob_db.conf. Default: data/mob_db.conf")
    parser.add_argument("--item-db", default=None, help="Path to item_db.conf. Default: data/item_db.conf")
    parser.add_argument("--mob-db2", default=None, help="Path to mob_db2.conf. Default: data/mob_db2.conf if present")
    parser.add_argument("--item-db2", default=None, help="Path to item_db2.conf. Default: data/item_db2.conf if present")
    parser.add_argument(
        "--spawn-dir",
        action="append",
        default=None,
        help="Spawn directory or file. Can be repeated. Default: data/mobs_pre_re and data/mobs_common if present.",
    )
    parser.add_argument("--csv-out", default="monster_ev.csv", help="Output CSV path. Default: monster_ev.csv")
    parser.add_argument("--monsters-out", default="monsters_hercules.json", help="Output monster JSON path.")
    parser.add_argument("--items-out", default="items_hercules.json", help="Output item JSON path.")
    parser.add_argument(
        "--drop-rate-multiplier",
        type=float,
        default=1.0,
        help="Multiplier applied to drop chances for expected_value. Raw EV is always also written. Default: 1.0",
    )
    parser.add_argument(
        "--no-cap-drop-rate",
        action="store_true",
        help="Do not cap adjusted drop chances at 10000. Default caps at 100%.",
    )
    parser.add_argument(
        "--include-mvp-drops",
        action="store_true",
        help="Include MvpDrops in EV. Default excludes them.",
    )
    parser.add_argument(
        "--sell-mode",
        choices=["base", "overcharge"],
        default="base",
        help="Use raw NPC sell price or Overcharge-adjusted sell price. Default: base.",
    )
    parser.add_argument(
        "--overcharge-rate",
        type=float,
        default=1.24,
        help="Multiplier used when --sell-mode overcharge. Default: 1.24.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    data_dir = Path(args.data_dir)
    mob_db = Path(args.mob_db) if args.mob_db else data_dir / "mob_db.conf"
    item_db = Path(args.item_db) if args.item_db else data_dir / "item_db.conf"
    mob_db2 = Path(args.mob_db2) if args.mob_db2 else data_dir / "mob_db2.conf"
    item_db2 = Path(args.item_db2) if args.item_db2 else data_dir / "item_db2.conf"
    spawn_dirs = [Path(p) for p in args.spawn_dir] if args.spawn_dir else default_spawn_dirs(data_dir)

    if not mob_db.exists():
        raise FileNotFoundError(f"Missing monster DB: {mob_db}")
    if not item_db.exists():
        raise FileNotFoundError(f"Missing item DB: {item_db}")
    if not spawn_dirs:
        logger.warning("No spawn dirs found. best_map_count will be zero for all monsters.")

    build_outputs(
        data_dir=data_dir,
        mob_db=mob_db,
        item_db=item_db,
        mob_db2=mob_db2 if mob_db2.exists() else None,
        item_db2=item_db2 if item_db2.exists() else None,
        spawn_dirs=spawn_dirs,
        csv_out=Path(args.csv_out),
        monsters_out=Path(args.monsters_out),
        items_out=Path(args.items_out),
        drop_rate_multiplier=args.drop_rate_multiplier,
        cap_drop_rate=not args.no_cap_drop_rate,
        include_mvp_drops=args.include_mvp_drops,
        sell_mode=args.sell_mode,
        overcharge_rate=args.overcharge_rate,
    )


if __name__ == "__main__":
    main()
