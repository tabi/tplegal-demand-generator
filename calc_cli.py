#!/usr/bin/env python3
"""
rekompensa.pl — CLI do kalkulatora rekompensat i odsetek handlowych.

Użycie:
    calc-rekompensa --json invoices.json
    calc-rekompensa --json invoices.json --lawsuit-date 2026-04-15

Schemat JSON input:
    {
        "invoices": [
            {
                "invoice_number": "FV/2024/001",
                "gross": 12500.00,
                "due_date": "2024-03-15",
                "payment_date": "2024-06-20"
            }
        ],
        "lawsuit_date": "2026-04-15"  // opcjonalne
    }
"""

import json
import sys
import argparse
from datetime import date
from decimal import Decimal
from pathlib import Path

from demand_calc import calculate_batch


class _DecimalEncoder(json.JSONEncoder):
    """JSON encoder obsługujący Decimal, date i set."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, set):
            return sorted(obj)
        return super().default(obj)


def main():
    parser = argparse.ArgumentParser(
        description="Kalkulator rekompensat i odsetek handlowych — Rekompensa.pl"
    )
    parser.add_argument(
        "--json", "-j",
        type=str,
        required=True,
        help="Ścieżka do pliku JSON z fakturami",
    )
    parser.add_argument(
        "--lawsuit-date", "-l",
        type=str,
        default=None,
        help="Data pozwu (YYYY-MM-DD) — do filtra przedawnienia",
    )

    args = parser.parse_args()

    # Wczytaj JSON
    json_path = Path(args.json)
    if not json_path.exists():
        print(f"ERROR: JSON file not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Parsuj faktury
    invoices_raw = raw.get("invoices", [])
    if not invoices_raw:
        print("ERROR: No invoices found in JSON (expected 'invoices' array)", file=sys.stderr)
        sys.exit(1)

    invoices = []
    for i, inv in enumerate(invoices_raw):
        errors = []
        if "gross" not in inv:
            errors.append("gross")
        if "due_date" not in inv:
            errors.append("due_date")
        if "payment_date" not in inv:
            errors.append("payment_date")
        if errors:
            print(f"ERROR: Invoice #{i+1} missing fields: {', '.join(errors)}", file=sys.stderr)
            sys.exit(1)

        try:
            due_date = date.fromisoformat(inv["due_date"])
        except (ValueError, TypeError):
            print(f"ERROR: Invoice #{i+1} invalid due_date: {inv['due_date']}", file=sys.stderr)
            sys.exit(1)

        try:
            payment_date = date.fromisoformat(inv["payment_date"])
        except (ValueError, TypeError):
            print(f"ERROR: Invoice #{i+1} invalid payment_date: {inv['payment_date']}", file=sys.stderr)
            sys.exit(1)

        try:
            gross = Decimal(str(inv["gross"]))
        except Exception:
            print(f"ERROR: Invoice #{i+1} invalid gross: {inv['gross']}", file=sys.stderr)
            sys.exit(1)

        invoices.append({
            "invoice_number": inv.get("invoice_number", f"#{i+1}"),
            "gross": gross,
            "due_date": due_date,
            "payment_date": payment_date,
        })

    # Parsuj lawsuit_date
    lawsuit_date = None
    lawsuit_date_str = args.lawsuit_date or raw.get("lawsuit_date")
    if lawsuit_date_str:
        try:
            lawsuit_date = date.fromisoformat(lawsuit_date_str)
        except (ValueError, TypeError):
            print(f"ERROR: Invalid lawsuit_date: {lawsuit_date_str}", file=sys.stderr)
            sys.exit(1)

    # Kalkulacja
    result = calculate_batch(invoices, lawsuit_date=lawsuit_date)

    # Output JSON
    print(json.dumps(result, cls=_DecimalEncoder, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
