#!/usr/bin/env python3
"""
rekompensa.pl — Generator wezwań do zapłaty (standalone)
Wypełnia template wezwanie_template.docx danymi z JSON.

Użycie:
    generate-demand --json input.json --template wezwanie.docx --output output.docx
"""

import json
import os
import sys
import argparse
import zipfile
import tempfile
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# ---------------------------------------------------------------------------
# Normalizacja nazw podmiotów — import z demand_utils (flat)
# ---------------------------------------------------------------------------

from demand_generator.utils import normalize_entity_name  # noqa: E402

# ---------------------------------------------------------------------------
# Warianty tonalne — single source of truth in demand_variants.py
# ---------------------------------------------------------------------------

from demand_generator.variants import (  # noqa: E402
    DEMAND_VARIANTS,
    TEMPLATE_THREAT_ORIGINAL as TEMPLATE_THREAT,
    TEMPLATE_CLOSING_ORIGINAL as TEMPLATE_CLOSING,
)

# ---------------------------------------------------------------------------
# Wymagane klucze w data dict dla fill_template_from_dict
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = [
    "creditor_name",
    "debtor_name",
]

# ---------------------------------------------------------------------------
# Kwota słownie (polski)
# ---------------------------------------------------------------------------

_ONES = [
    "", "jeden", "dwa", "trzy", "cztery", "pięć",
    "sześć", "siedem", "osiem", "dziewięć",
]
_TEENS = [
    "dziesięć", "jedenaście", "dwanaście", "trzynaście", "czternaście",
    "piętnaście", "szesnaście", "siedemnaście", "osiemnaście", "dziewiętnaście",
]
_TENS = [
    "", "dziesięć", "dwadzieścia", "trzydzieści", "czterdzieści",
    "pięćdziesiąt", "sześćdziesiąt", "siedemdziesiąt", "osiemdziesiąt", "dziewięćdziesiąt",
]
_HUNDREDS = [
    "", "sto", "dwieście", "trzysta", "czterysta",
    "pięćset", "sześćset", "siedemset", "osiemset", "dziewięćset",
]

# (singular, plural_2_4, plural_5+)
_GROUPS = [
    ("", "", ""),
    ("tysiąc", "tysiące", "tysięcy"),
    ("milion", "miliony", "milionów"),
]


def _plural_form(n, singular, plural_2_4, plural_5_plus):
    """Wybiera formę polskiego rzeczownika dla liczebnika."""
    if n == 1:
        return singular
    last_two = n % 100
    last_one = n % 10
    if 12 <= last_two <= 14:
        return plural_5_plus
    if 2 <= last_one <= 4:
        return plural_2_4
    return plural_5_plus


def _chunk_to_words(n):
    """Konwertuje liczbę 0-999 na słowa."""
    if n == 0:
        return ""
    parts = []
    h = n // 100
    remainder = n % 100
    if h > 0:
        parts.append(_HUNDREDS[h])
    if 10 <= remainder <= 19:
        parts.append(_TEENS[remainder - 10])
    else:
        t = remainder // 10
        o = remainder % 10
        if t > 0:
            parts.append(_TENS[t])
        if o > 0:
            parts.append(_ONES[o])
    return " ".join(parts)


def kwota_slownie(amount):
    """
    Konwertuje kwotę Decimal/float na tekst polski.
    Np. 157720.00 -> "sto pięćdziesiąt siedem tysięcy siedemset dwadzieścia złotych 00/100"
    """
    amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    integer_part = int(amount)
    fractional = int((amount - integer_part) * 100)

    if integer_part == 0:
        words = "zero"
    else:
        chunks = []
        n = integer_part
        while n > 0:
            chunks.append(n % 1000)
            n //= 1000

        word_parts = []
        for i in reversed(range(len(chunks))):
            chunk = chunks[i]
            if chunk == 0:
                continue
            chunk_words = _chunk_to_words(chunk)
            if i == 0:
                word_parts.append(chunk_words)
            else:
                group_name = _plural_form(
                    chunk,
                    _GROUPS[i][0],
                    _GROUPS[i][1],
                    _GROUPS[i][2],
                )
                # Specjalny przypadek: "tysiąc" (nie "jeden tysiąc")
                if chunk == 1 and i >= 1:
                    word_parts.append(group_name)
                else:
                    word_parts.append(f"{chunk_words} {group_name}")

        words = " ".join(word_parts).strip()

    # Forma "złotych"
    last_two = integer_part % 100
    last_one = integer_part % 10
    if integer_part == 1:
        zloty = "złoty"
    elif 12 <= last_two <= 14:
        zloty = "złotych"
    elif 2 <= last_one <= 4:
        zloty = "złote"
    else:
        zloty = "złotych"

    return f"{words} {zloty} {fractional:02d}/100"


# ---------------------------------------------------------------------------
# Art. 10 — referencja ustawowa zależna od tierów
# ---------------------------------------------------------------------------

def art_10_reference(tiers: set) -> str:
    """
    Generuje referencję do art. 10 ustawy w zależności od tierów rekompensat.

    EUR_40  = art. 10 ust. 1 pkt 1
    EUR_70  = art. 10 ust. 1 pkt 2
    EUR_100 = art. 10 ust. 1 pkt 3

    Jeśli mix tierów -> "art. 10 ust. 1 pkt 1, 2 i 3" (odpowiednio).
    """
    tier_map = {
        "EUR_40": "1",
        "EUR_70": "2",
        "EUR_100": "3",
    }
    pkts = sorted(tier_map[t] for t in tiers if t in tier_map)

    if not pkts:
        return "10 ust. 1"

    if len(pkts) == 1:
        pkt_str = f"pkt {pkts[0]}"
    elif len(pkts) == 2:
        pkt_str = f"pkt {pkts[0]} i {pkts[1]}"
    else:
        pkt_str = f"pkt {pkts[0]}, {pkts[1]} i {pkts[2]}"

    return f"10 ust. 1 {pkt_str}"


# ---------------------------------------------------------------------------
# Format kwoty z niełamliwą spacją (jak w oryginale)
# ---------------------------------------------------------------------------

def format_pln(amount) -> str:
    """Formatuje kwotę PLN: 157\u00a0720,00"""
    amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    integer_part = int(amount)
    fractional = int((amount - integer_part) * 100)

    # Grupowanie tysięcy z niełamliwą spacją
    int_str = f"{integer_part:,}".replace(",", "\u00a0")
    return f"{int_str},{fractional:02d}"


# ---------------------------------------------------------------------------
# Generator — standalone (dane z dict/JSON)
# ---------------------------------------------------------------------------

def fill_template_from_dict(template_path: Path, output_path: Path,
                            data: dict, strategy: str = "standard_collect"):
    """
    Wypełnia template danymi z dict (bez bazy).
    Użyteczne do testowania i do integracji z innymi systemami.

    Wymagane klucze w data:
        creditor_name, cr_street, cr_city, cr_zip, cr_bank,
        debtor_name, d_street, d_city, d_zip,
        total_compensation_pln, assigned_to,
        invoice_numbers (list[str]), invoice_tiers (set[str])

    Raises:
        ValueError: jeśli brakuje wymaganych kluczy lub strategia jest nieznana
        FileNotFoundError: jeśli template nie istnieje
    """
    # Walidacja template
    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    # Walidacja wymaganych pól
    missing = [f for f in REQUIRED_FIELDS if f not in data or data[f] is None]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    case = data
    invoices = [
        {"invoice_number": n, "compensation_tier": None}
        for n in data.get("invoice_numbers", [])
    ]
    # Nadpisz tiers jeśli podane
    tiers_override = data.get("invoice_tiers")

    variant = DEMAND_VARIANTS.get(strategy)
    if not variant:
        raise ValueError(f"Unknown strategy: {strategy}. Available: {', '.join(DEMAND_VARIANTS.keys())}")

    principal_pln = Decimal(str(data.get("total_principal_pln", 0)))
    total_pln = Decimal(str(data.get("total_compensation_pln", 0)))
    interest_pln = Decimal(str(data.get("total_interest_pln", 0)))
    combined_pln = principal_pln + total_pln + interest_pln

    # Walidacja: przynajmniej jedna kwota musi być > 0
    if principal_pln == 0 and total_pln == 0 and interest_pln == 0:
        raise ValueError("Brak kwot do wezwania (total_principal_pln, total_compensation_pln, total_interest_pln wszystkie = 0)")

    tiers = tiers_override or set()
    invoice_numbers = data.get("invoice_numbers", [])

    placeholders = {
        "{{DATA}}": f"Leszno, dnia {_format_date(date.today())}",
        "{{WIERZYCIEL_NAZWA}}": normalize_entity_name(data["creditor_name"]) if data.get("creditor_name") else "___",
        "{{WIERZYCIEL_ADRES}}": _join_address(
            data.get("cr_street"), data.get("cr_zip"), data.get("cr_city")
        ),
        "{{RADCA_IMIE_NAZWISKO}}": data.get("assigned_to", "Bartłomieja Przyniczkę"),
        "{{DLUZNIK_NAZWA}}": normalize_entity_name(data["debtor_name"]) if data.get("debtor_name") else "___",
        "{{DLUZNIK_ADRES_ULICA}}": data.get("d_street", "___"),
        "{{DLUZNIK_ADRES_MIASTO}}": _join_zip_city(
            data.get("d_zip"), data.get("d_city")
        ),
        "{{KWOTA_LACZNIE_PLN}}": format_pln(combined_pln),
        "{{KWOTA_GLOWNA_PLN}}": format_pln(principal_pln) if principal_pln > 0 else "0,00",
        "{{KWOTA_REKOMPENSATY_PLN}}": format_pln(total_pln),
        "{{KWOTA_ODSETKI_PLN}}": format_pln(interest_pln),
        "{{LISTA_FAKTUR}}": ", ".join(invoice_numbers) + "." if invoice_numbers else "___",
        "{{NUMER_RACHUNKU}}": data.get("cr_bank", "___"),
        "{{TERMIN_DNI}}": str(variant["deadline_days"]),
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        unpack_dir = tmpdir / "unpacked"

        with zipfile.ZipFile(template_path, "r") as zf:
            zf.extractall(unpack_dir)

        doc_xml_path = unpack_dir / "word" / "document.xml"
        with open(doc_xml_path, "rb") as f:
            content = f.read()

        for placeholder, value in placeholders.items():
            content = content.replace(
                placeholder.encode("utf-8"),
                _xml_escape(_sanitize_placeholder_value(value)).encode("utf-8"),
            )

        if strategy != "standard_collect":
            content = content.replace(
                TEMPLATE_THREAT.encode("utf-8"),
                _xml_escape(variant["threat_paragraph"]).encode("utf-8"),
            )
            content = content.replace(
                TEMPLATE_CLOSING.encode("utf-8"),
                _xml_escape(variant["closing_paragraph"]).encode("utf-8"),
            )

        with open(doc_xml_path, "wb") as f:
            f.write(content)

        _repack_docx(unpack_dir, output_path, template_path)

    return output_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_date(d: date) -> str:
    """Formatuje datę po polsku: '19 marca 2026 r.'"""
    months = {
        1: "stycznia", 2: "lutego", 3: "marca", 4: "kwietnia",
        5: "maja", 6: "czerwca", 7: "lipca", 8: "sierpnia",
        9: "września", 10: "października", 11: "listopada", 12: "grudnia",
    }
    return f"{d.day} {months[d.month]} {d.year} r."


def _join_address(street, zip_code, city):
    """Łączy adres: 'ul. Skarbowa 2/5, 64-100 Leszno'"""
    parts = []
    if street:
        parts.append(street)
    if zip_code and city:
        parts.append(f"{zip_code} {city}")
    elif city:
        parts.append(city)
    return ", ".join(parts) if parts else "___"


def _join_zip_city(zip_code, city):
    """Łączy kod + miasto: '64-100 Leszno'"""
    if zip_code and city:
        return f"{zip_code} {city}"
    return city or zip_code or "___"


def _sanitize_placeholder_value(text: str) -> str:
    """Usuwa sekwencje {{ i }} z wartości wejściowych, zapobiegając kolizji z placeholderami."""
    return text.replace("{{", "").replace("}}", "")


def _xml_escape(text: str) -> str:
    """Escapuje znaki specjalne XML w tekście."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _repack_docx(unpack_dir: Path, output_path: Path, original_path: Path):
    """
    Pakuje rozpakowany katalog z powrotem do .docx.
    Zachowuje kolejność plików z oryginału (Word jest wrażliwy na to).
    """
    # Pobierz oryginalną kolejność plików
    with zipfile.ZipFile(original_path, "r") as orig_zf:
        original_names = orig_zf.namelist()

    # Zbierz wszystkie pliki w katalogu
    all_files = set()
    for root, dirs, files in os.walk(unpack_dir):
        for fname in files:
            full_path = Path(root) / fname
            rel_path = full_path.relative_to(unpack_dir)
            all_files.add(str(rel_path))

    # Zapisz w oryginalnej kolejności + ewentualne nowe pliki
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Najpierw oryginalna kolejność
        for name in original_names:
            file_path = unpack_dir / name
            if file_path.exists():
                zf.write(file_path, name)
                all_files.discard(name)

        # Potem ewentualne nowe pliki
        for name in sorted(all_files):
            file_path = unpack_dir / name
            if file_path.exists():
                zf.write(file_path, name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generator wezwań do zapłaty — Rekompensa.pl (standalone)"
    )
    parser.add_argument(
        "--json", "-j",
        type=str,
        required=True,
        help="Ścieżka do pliku JSON z danymi sprawy",
    )
    parser.add_argument(
        "--template", "-t",
        type=str,
        default=None,
        help="Ścieżka do template .docx (domyślnie: bundled wezwanie_template.docx)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="wezwanie_output.docx",
        help="Ścieżka wyjściowa .docx (domyślnie: wezwanie_output.docx)",
    )
    parser.add_argument(
        "--strategy", "-s",
        choices=list(DEMAND_VARIANTS.keys()),
        default="standard_collect",
        help="Wariant tonalny (domyślnie: standard_collect)",
    )

    args = parser.parse_args()

    # Wczytaj JSON
    json_path = Path(args.json)
    if not json_path.exists():
        print(f"ERROR: JSON file not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    from demand_generator import DEFAULT_TEMPLATE
    template = Path(args.template) if args.template else DEFAULT_TEMPLATE
    output = Path(args.output)

    try:
        fill_template_from_dict(template, output, data, strategy=args.strategy)
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Podsumowanie
    principal = Decimal(str(data.get("total_principal_pln", 0)))
    comp = Decimal(str(data.get("total_compensation_pln", 0)))
    interest = Decimal(str(data.get("total_interest_pln", 0)))
    total = principal + comp + interest
    print(f"Wezwanie wygenerowane: {output}")
    print(f"  Wierzyciel: {data.get('creditor_name', '?')}")
    print(f"  Dłużnik: {data.get('debtor_name', '?')}")
    if principal > 0:
        print(f"  Należność główna: {format_pln(principal)} PLN")
    print(f"  Rekompensaty: {format_pln(comp)} PLN")
    print(f"  Odsetki: {format_pln(interest)} PLN")
    print(f"  Łącznie: {format_pln(total)} PLN")
    print(f"  Strategia: {args.strategy}")
    print(f"  Termin: {DEMAND_VARIANTS[args.strategy]['deadline_days']} dni")


if __name__ == "__main__":
    main()
