"""Pure HTML parsing and data-normalisation functions for the Urja portal.

These functions take raw portal HTML and return plain Python dicts/lists ready
to be validated by Pydantic models. They are intentionally pure (no network
access, no global state) so they can be unit-tested with saved HTML fixtures.

Design notes
------------
The legacy portal's exact HTML structure is not yet confirmed (see PROTOCOL.md
recon checklist). The parsers below are deliberately defensive: they try a few
likely selectors in order and fall back to generic table parsing, so they keep
working even if class names differ. Once recon is complete, tighten the
selectors here and remove the fallbacks that are no longer needed.

Column mapping uses table headers when available, so column reordering on the
portal does not silently misalign data. Missing values become ``None`` rather
than crashing.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional

from bs4 import BeautifulSoup, Tag


# --- Normalisation helpers ----------------------------------------------------

# Matches numbers possibly wrapped in currency symbols, thousands separators,
# and surrounding whitespace, e.g. "Rs. 1,234.50" -> "1234.50".
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def clean_text(value: Optional[str]) -> Optional[str]:
    """Strip whitespace and return ``None`` for empty strings."""
    if value is None:
        return None
    text = value.strip()
    return text or None


def to_float(value: Optional[str]) -> Optional[float]:
    """Convert a numeric string to float, tolerating commas and units.

    Returns ``None`` if the value cannot be parsed.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    match = _NUMBER_RE.search(text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except (ValueError, TypeError):
        return None


def to_int(value: Optional[str]) -> Optional[int]:
    """Convert a numeric string to int, tolerating commas. Returns ``None`` if not parseable."""
    f = to_float(value)
    return int(f) if f is not None else None


_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%b %d, %Y %H:%M:%S",
    "%b %d, %Y",
)


def to_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse a date string from several common portal formats.

    Returns ``None`` if the value cannot be parsed into any known format.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def first_present(*values: Optional[str]) -> Optional[str]:
    """Return the first non-empty, non-``None`` value."""
    for v in values:
        cleaned = clean_text(v)
        if cleaned is not None:
            return cleaned
    return None


# --- Table helpers -------------------------------------------------------------


def _table_header_map(table: Tag) -> Dict[str, int]:
    """Map lowercased header text -> column index for a ``<table>``.

    Returns an empty dict if the table has no ``<th>`` headers.
    """
    headers: List[str] = []
    head_row = table.find("tr")
    if head_row is not None:
        for cell in head_row.find_all(["th", "td"]):
            headers.append(clean_text(cell.get_text()) or "")
    return {h.lower(): i for i, h in enumerate(headers) if h}


def _row_cells(row: Tag) -> List[str]:
    """Return the trimmed text of each ``<td>``/``<th>`` in a row."""
    return [clean_text(cell.get_text()) or "" for cell in row.find_all(["td", "th"])]


def _select_table(soup: BeautifulSoup, candidates: List[str]) -> Optional[Tag]:
    """Find the first ``<table>`` matching any candidate selector/CSS class.

    ``candidates`` is a list of CSS class names tried in order. Falls back to
    the first ``<table>`` in the document if none match.
    """
    for cls in candidates:
        table = soup.find("table", class_=cls) or soup.find("table", id=cls)
        if table is not None:
            return table
    tables = soup.find_all("table")
    return tables[0] if tables else None


# --- Meters listing parser -----------------------------------------------------


# Header aliases mapped to our output keys. Keys here intentionally match the
# Meter Pydantic model field names.
_METER_HEADER_ALIASES = {
    "meter_id": ["meter id", "meter", "id", "meterid", "meter no", "meter no."],
    "serial_number": [
        "serial",
        "serial number",
        "serial no",
        "serial no.",
        "serial#",
    ],
    "status": ["status", "state"],
    "location": ["location", "site", "premise", "address"],
}


def _resolve_column(header_map: Dict[str, int], aliases: List[str]) -> Optional[int]:
    """Find a column index from a header map using a list of alias names."""
    for alias in aliases:
        if alias in header_map:
            return header_map[alias]
    return None


def parse_meters(html: str) -> List[Dict]:
    """Parse the meters listing HTML into a list of meter dicts.

    Tries a table with common class names first, then any table. If no table is
    present, attempts to parse repeating card/row elements. Returns ``[]`` if
    no recognisable structure is found.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = _select_table(soup, ["meters-table", "meter-list", "meters", "table"])
    if table is not None:
        return _parse_meters_table(table)

    # Fallback: repeating elements with a meter-id data attribute or class.
    cards = soup.select("[data-meter-id], .meter-card, .meter-row")
    if cards:
        return [_parse_meter_card(card) for card in cards]

    return []


def _parse_meters_table(table: Tag) -> List[Dict]:
    header_map = _table_header_map(table)
    col_meter = _resolve_column(header_map, _METER_HEADER_ALIASES["meter_id"])
    col_serial = _resolve_column(header_map, _METER_HEADER_ALIASES["serial_number"])
    col_status = _resolve_column(header_map, _METER_HEADER_ALIASES["status"])
    col_location = _resolve_column(header_map, _METER_HEADER_ALIASES["location"])

    rows = table.find_all("tr")
    # Skip the header row if the first row contained <th>.
    data_rows = rows[1:] if rows and rows[0].find("th") else rows

    meters: List[Dict] = []
    for row in data_rows:
        cells = _row_cells(row)
        if not cells or all(not c for c in cells):
            continue
        meter_id = cells[col_meter] if col_meter is not None and col_meter < len(cells) else None
        # If no header map, assume first column is the meter id (positional).
        if col_meter is None and cells:
            meter_id = meter_id or cells[0]
        if not meter_id or "no meters" in meter_id.lower():
            continue
        meters.append(
            {
                "meter_id": meter_id,
                "serial_number": (
                    cells[col_serial]
                    if col_serial is not None and col_serial < len(cells)
                    else None
                ),
                "status": (
                    cells[col_status]
                    if col_status is not None and col_status < len(cells)
                    else None
                ) or "UNKNOWN",
                "location": (
                    cells[col_location]
                    if col_location is not None and col_location < len(cells)
                    else None
                ),
            }
        )
    return meters


def _parse_meter_card(card: Tag) -> Dict:
    """Parse a single meter card/row element (fallback path)."""
    meter_id = card.get("data-meter-id") or clean_text(card.find(class_="meter-id").get_text()) if card.find(class_="meter-id") else None  # type: ignore[union-attr]
    serial = _text_of(card, "serial-number", "serial")
    status = _text_of(card, "status", "state") or "UNKNOWN"
    location = _text_of(card, "location", "site")
    return {
        "meter_id": meter_id or "UNKNOWN",
        "serial_number": serial,
        "status": status,
        "location": location,
    }


def _text_of(parent: Tag, *classes: str) -> Optional[str]:
    """Return the text of the first child matching any of the given classes."""
    for cls in classes:
        el = parent.find(class_=cls)
        if el is not None:
            return clean_text(el.get_text())
    return None


# --- Meter detail parser -------------------------------------------------------


def parse_meter_details(html: str, meter_id: str) -> Optional[Dict]:
    """Parse a meter detail page into a dict matching ``MeterDetail``.

    Tries well-known element ids/classes, then a definition list, then a
    generic key/value table. Returns ``None`` if the page looks like a
    not-found / login redirect (no recognisable meter content).
    """
    soup = BeautifulSoup(html, "html.parser")

    if _looks_like_login_or_missing(soup, meter_id):
        return None

    data: Dict = {"meter_id": meter_id}
    # Try id-based selectors first (most stable if present).
    data["serial_number"] = first_present(
        _text_by_id(soup, "meter-serial"),
        _text_by_id(soup, "serial-number"),
        _text_by_class(soup, "serial-number"),
    )
    data["status"] = first_present(
        _text_by_id(soup, "meter-status"),
        _text_by_class(soup, "status-value"),
        _text_by_class(soup, "status"),
    ) or "UNKNOWN"
    data["location"] = first_present(
        _text_by_id(soup, "meter-location"),
        _text_by_class(soup, "location-value"),
        _text_by_class(soup, "location"),
    )
    data["install_date"] = to_datetime(
        first_present(_text_by_id(soup, "install-date"), _text_by_class(soup, "install-date"))
    )
    data["last_reading"] = to_float(
        first_present(_text_by_id(soup, "last-reading"), _text_by_class(soup, "last-reading"))
    )
    data["last_reading_at"] = to_datetime(
        first_present(
            _text_by_id(soup, "last-reading-at"),
            _text_by_class(soup, "last-reading-time"),
        )
    )
    data["firmware_version"] = first_present(
        _text_by_id(soup, "firmware-version"),
        _text_by_class(soup, "firmware"),
    )
    data["customer"] = first_present(
        _text_by_id(soup, "customer"),
        _text_by_class(soup, "customer"),
    )

    # Merge definition list if present or needed.
    dl_data = _parse_detail_definition_list(soup)
    for k, v in dl_data.items():
        if k in (
            "serial_number",
            "status",
            "location",
            "install_date",
            "last_reading",
            "last_reading_at",
            "firmware_version",
            "customer",
        ):
            if data.get(k) is None or (k == "status" and data.get("status") == "UNKNOWN"):
                data[k] = v

    # Extract location from breadcrumbs if not present.
    if not data.get("location"):
        nav = soup.find("nav")
        if nav:
            crumbs = [
                clean_text(s.get_text())
                for s in nav.find_all("span")
                if clean_text(s.get_text()) and "›" not in s.get_text()
            ]
            if crumbs:
                data["location"] = crumbs[-1]

    return data


def _looks_like_login_or_missing(soup: BeautifulSoup, meter_id: str) -> bool:
    """Heuristic: detect a login redirect or a 'meter not found' page."""
    text = soup.get_text(" ", strip=True).lower()
    if "login" in text and "password" in text and meter_id.lower() not in text:
        return True
    if "not found" in text and meter_id.lower() not in text:
        return True
    return False


def _parse_detail_definition_list(soup: BeautifulSoup) -> Dict:
    """Parse a ``<dl>`` definition list or a 2-column key/value table."""
    data: Dict = {}
    dl = soup.find("dl")
    if dl is not None:
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            key = clean_text(dt.get_text())
            val = clean_text(dd.get_text())
            if key:
                data[_normalise_detail_key(key)] = val
        return data

    table = _select_table(soup, ["meter-details", "details", "info"])
    if table is not None:
        rows = table.find_all("tr")
        for row in rows:
            cells = _row_cells(row)
            if len(cells) >= 2 and cells[0]:
                data[_normalise_detail_key(cells[0])] = cells[1]
    return data


def _normalise_detail_key(key: str) -> str:
    """Map a portal-visible label to a model field name."""
    mapping = {
        "meter id": "meter_id",
        "meter": "meter_id",
        "serial": "serial_number",
        "serial no": "serial_number",
        "serial no.": "serial_number",
        "serial number": "serial_number",
        "status": "status",
        "installation status": "status",
        "install status": "status",
        "location": "location",
        "install date": "install_date",
        "installed": "install_date",
        "installation type": "installation_type",
        "phase type": "phase_type",
        "make": "make",
        "dt": "dt",
        "last reading": "last_reading",
        "last reading at": "last_reading_at",
        "firmware": "firmware_version",
        "firmware version": "firmware_version",
        "customer": "customer",
    }
    return mapping.get(key.lower(), key.lower().replace(" ", "_"))


def _text_by_id(soup: BeautifulSoup, element_id: str) -> Optional[str]:
    el = soup.find(id=element_id)
    return clean_text(el.get_text()) if el is not None else None


def _text_by_class(soup: BeautifulSoup, cls: str) -> Optional[str]:
    el = soup.find(class_=cls)
    return clean_text(el.get_text()) if el is not None else None


# --- Consumption parser --------------------------------------------------------


_CONSUMPTION_HEADER_ALIASES = {
    "timestamp": ["timestamp", "date", "time", "datetime", "reading time", "period"],
    "kwh": ["kwh", "consumption", "value", "reading", "energy", "units"],
    "unit": ["unit", "units", "uom"],
}


def parse_consumption(html: str, meter_id: str) -> Dict:
    """Parse a consumption history page into a ``ConsumptionHistory`` dict.

    Returns a dict with ``meter_id`` and ``readings`` (list). If the page has no
    table, returns an empty readings list.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = _select_table(soup, ["consumption-table", "consumption", "readings", "table"])

    readings: List[Dict] = []
    if table is not None:
        header_map = _table_header_map(table)
        col_ts = _resolve_column(header_map, _CONSUMPTION_HEADER_ALIASES["timestamp"])
        col_kwh = _resolve_column(header_map, _CONSUMPTION_HEADER_ALIASES["kwh"])
        col_unit = _resolve_column(header_map, _CONSUMPTION_HEADER_ALIASES["unit"])

        rows = table.find_all("tr")
        data_rows = rows[1:] if rows and rows[0].find("th") else rows
        for row in data_rows:
            cells = _row_cells(row)
            if not cells or all(not c for c in cells):
                continue
            ts_idx = col_ts if col_ts is not None else 0
            kwh_idx = col_kwh if col_kwh is not None else 1
            ts_raw = cells[ts_idx] if ts_idx < len(cells) else None
            kwh_raw = cells[kwh_idx] if kwh_idx < len(cells) else None
            ts = to_datetime(ts_raw)
            kwh = to_float(kwh_raw)
            unit = (
                cells[col_unit]
                if col_unit is not None and col_unit < len(cells)
                else "kWh"
            ) or "kWh"
            if ts is None or kwh is None:
                # Skip rows that don't have parseable date + value.
                continue
            readings.append({"timestamp": ts, "kwh": kwh, "unit": unit})

    return {"meter_id": meter_id, "readings": readings}


# --- Hierarchy parser ----------------------------------------------------------


def parse_hierarchy(html: str) -> Dict:
    """Parse a hierarchy/network tree page into a ``HierarchyNode`` dict.

    Tries a nested ``<ul>``/``<ol>`` structure first. If absent, attempts a
    parent/child table with id columns. Returns a single root node.
    """
    soup = BeautifulSoup(html, "html.parser")
    root_ul = soup.find(["ul", "ol"], class_="hierarchy") or soup.find(
        ["ul", "ol"], id="hierarchy"
    )
    if root_ul is not None:
        children = [_parse_hierarchy_list_item(li) for li in root_ul.find_all("li", recursive=False)]
        return {
            "node_id": "root",
            "name": "Network",
            "node_type": "root",
            "children": [c for c in children if c],
        }

    # Fallback: flat table with parent/child columns.
    table = _select_table(soup, ["hierarchy-table", "hierarchy", "tree"])
    if table is not None:
        return _parse_hierarchy_table(table)
    return {"node_id": "root", "name": "Network", "node_type": "root", "children": []}


def _parse_hierarchy_list_item(li: Tag) -> Optional[Dict]:
    """Parse one ``<li>`` of a hierarchy list into a node with nested children."""
    # The node's own text is the first direct text node; nested <ul>/<ol> hold children.
    nested = li.find(["ul", "ol"])
    label_parts = []
    for child in li.children:
        if isinstance(child, Tag) and child.name in ("ul", "ol"):
            break
        if isinstance(child, str):
            t = child.strip()
            if t:
                label_parts.append(t)
    label = " ".join(label_parts).strip() or "unnamed"
    children: List[Dict] = []
    if nested is not None:
        children = [
            _parse_hierarchy_list_item(sub_li)
            for sub_li in nested.find_all("li", recursive=False)
        ]
        children = [c for c in children if c]
    node_type = "meter" if not children and re.match(r"^[A-Za-z]+-\d+", label) else "group"
    return {
        "node_id": label,
        "name": label,
        "node_type": node_type,
        "children": children,
    }


def _parse_hierarchy_table(table: Tag) -> Dict:
    """Build a tree from a flat parent-id/child-id table."""
    header_map = _table_header_map(table)
    col_parent = _resolve_column(header_map, ["parent", "parent id", "parent_id"])
    col_child = _resolve_column(header_map, ["child", "child id", "child_id"])
    col_name = _resolve_column(header_map, ["name", "label", "child name"])
    nodes: Dict[str, Dict] = {}
    children_map: Dict[str, List[str]] = {}
    rows = table.find_all("tr")
    data_rows = rows[1:] if rows and rows[0].find("th") else rows
    for row in data_rows:
        cells = _row_cells(row)
        if len(cells) < 2:
            continue
        parent = cells[col_parent] if col_parent is not None and col_parent < len(cells) else cells[0]
        child = cells[col_child] if col_child is not None and col_child < len(cells) else cells[1]
        name = cells[col_name] if col_name is not None and col_name < len(cells) else child
        if not child:
            continue
        nodes.setdefault(child, {"node_id": child, "name": name or child, "node_type": "group", "children": []})
        children_map.setdefault(parent, []).append(child)
        nodes.setdefault(parent, {"node_id": parent, "name": parent, "node_type": "group", "children": []})

    # Wire children.
    for parent_id, child_ids in children_map.items():
        nodes[parent_id]["children"] = [nodes[c] for c in child_ids if c in nodes]

    # Root = the parent that never appears as a child.
    all_children = {c for kids in children_map.values() for c in kids}
    roots = [nid for nid in children_map if nid not in all_children]
    root_id = roots[0] if roots else (next(iter(children_map)) if children_map else "root")
    if root_id in nodes:
        return nodes[root_id]
    return {"node_id": "root", "name": "Network", "node_type": "root", "children": []}
