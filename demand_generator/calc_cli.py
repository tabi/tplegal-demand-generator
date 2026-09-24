#!/usr/bin/env python3
"""
rekompensa.pl — CLI do kalkulatora rekompensat i odsetek handlowych.

Użycie:
    calc-rekompensa --json invoices.json
    calc-rekompensa --json invoices.json --lawsuit-date 2026-04-15
    calc-rekompensa --json invoices.json --debtor-type private   (status od radcy)

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
        "lawsuit_date": "2026-04-15",   // opcjonalne
        "debtor_type": "private"        // opcjonalne; brak = private + ostrzeżenie
    }
"""

import json
import sys
import argparse
from datetime import date
from decimal import Decimal
from pathlib import Path

from demand_generator import __version__
from demand_generator.calc import DebtorType, calculate_batch
from demand_generator.check_rates import warn_if_stale
from demand_generator.utils import (
    DEBTOR_TYPE_KEY,
    find_misspelled_debtor_type_keys,
    nested_debtor_type_values,
)

# Status dłużnika rozstrzyga PODSTAWĘ PRAWNĄ (art. 7 ust. 1 vs art. 8 ust. 1)
# i STAWKĘ (+10 p.p. z art. 4 pkt 3 lit. b vs +8 p.p. z lit. a dla publicznego
# podmiotu leczniczego). Do wersji 0.4.0 CLI go w ogóle nie przyjmowało: leciała
# domyślna wartość PRIVATE, więc szpital liczył się po 13,75% zamiast 11,75%,
# a bramka reżimu art. 8 w calc.py była nieosiągalna z linii komend.
DEBTOR_TYPE_CHOICES = [t.value for t in DebtorType]

# Brak statusu NIE jest błędem: cały dotychczasowy portfel jest prywatny, a twardy
# stop unieważniłby każdy istniejący JSON. Ale cisza była właściwą przyczyną tej
# klasy defektu, więc domyślna wartość jest OGŁASZANA. Na stderr, bo stdout to
# JSON wyniku — tak samo robi warn_if_stale().
NO_DEBTOR_TYPE_WARNING = (
    "UWAGA: nie podano statusu dłużnika — przyjęto dłużnika PRYWATNEGO "
    "(art. 7 ust. 1, stawka +10 p.p. z art. 4 pkt 3 lit. b). Dla podmiotu "
    "publicznego, w tym leczniczego, podaj --debtor-type albo klucz "
    '"debtor_type" w JSON-ie. Klasyfikacja wg art. 4 pkt 3 to ocena prawna '
    "radcy, nie domysł z nazwy."
)


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


def _require_readable_debtor_type_keys(payload) -> None:
    """Status podany w miejscu/pod nazwą, których parser nie czyta = błąd.

    Cicho zignorowana kwalifikacja z art. 4 pkt 3 jest gorsza niż jej brak: brak
    dostaje ostrzeżenie, a zignorowany klucz wygląda jak sukces. Dlatego
    `debtorType`, `debtor-type` czy `debtor_type` wewnątrz pozycji `invoices`
    kończą pracę, zamiast schodzić na wartość domyślną.
    """
    misspelled = find_misspelled_debtor_type_keys(payload)
    if misspelled:
        print(
            f"ERROR: status dłużnika podany pod nieczytanym kluczem: "
            f"{', '.join(repr(k) for k in misspelled)}. Kalkulator czyta wyłącznie "
            f'"{DEBTOR_TYPE_KEY}" na najwyższym poziomie JSON-a — popraw nazwę '
            "klucza, żeby kwalifikacja nie została po cichu pominięta.",
            file=sys.stderr,
        )
        sys.exit(1)

    nested = nested_debtor_type_values(payload)
    if nested:
        print(
            f'ERROR: "{DEBTOR_TYPE_KEY}" znaleziony w zagnieżdżeniu '
            f"(wartości: {', '.join(repr(v) for v in nested)}), a czytany jest "
            "tylko klucz najwyższego poziomu. Przenieś go obok \"invoices\" — "
            "status dłużnika dotyczy całej sprawy, nie pojedynczej faktury.",
            file=sys.stderr,
        )
        sys.exit(1)


def _parse_debtor_type(value, source: str) -> DebtorType:
    """Wartość → DebtorType. Niepoprawna kończy pracę, nie schodzi na domyślną.

    Literówka w statusie prawnym nie ma prawa cicho zejść na `private` — a bywa
    złośliwa: „publiczny" znaczy dokładnie odwrotność wartości domyślnej.
    """
    try:
        return DebtorType(value)
    except (ValueError, TypeError):
        print(
            f"ERROR: nieznany debtor_type ({source}): {value!r}. Dozwolone: "
            f"{', '.join(DEBTOR_TYPE_CHOICES)}.",
            file=sys.stderr,
        )
        sys.exit(1)


def _resolve_debtor_type(flag_value, json_value) -> DebtorType:
    """Status dłużnika z flagi, a jeśli jej nie ma — z klucza w JSON-ie.

    Fallback jak przy lawsuit_date, ale z jedną różnicą: przy SPRZECZNOŚCI obu
    źródeł nie ma zwycięzcy, jest twardy stop. Skoro wartości się różnią, to co
    najmniej jedna z nich deklaruje podmiot publiczny — a „flaga wygrywa"
    znaczyłoby wtedy, że `--debtor-type private` na JSON-ie ze statusem
    `public_medical` wystawia pełną kwotę po +10 p.p. Dokładnie to obejście
    zakazuje komunikat bramki, więc CLI nie może go samo oferować. Rozstrzygnięcie,
    które ze źródeł mówi prawdę o kwalifikacji z art. 4 pkt 3, jest oceną prawną
    i nie należy do narzędzia.

    Brak wartości → PRIVATE plus ostrzeżenie na stderr. Świadome odstępstwo od
    toru A (tplegal-tools), gdzie brak jest twardym stopem: tam prawdę podaje
    kolumna debtor_entities.debtor_type, a tutaj nie ma jej skąd wziąć, więc
    stop unieważniłby każdy istniejący JSON, nie dając w zamian żadnej wiedzy.
    """
    flag = flag_value.strip() if isinstance(flag_value, str) else flag_value
    from_json = json_value.strip() if isinstance(json_value, str) else json_value

    flag_type = _parse_debtor_type(flag, "flaga --debtor-type") if flag else None
    json_type = (
        _parse_debtor_type(from_json, 'klucz "debtor_type" w JSON-ie')
        if from_json
        else None
    )

    if flag_type and json_type and flag_type != json_type:
        print(
            f"⛔ Sprzeczny status dłużnika: flaga --debtor-type={flag_type.value} "
            f'vs "debtor_type": "{json_type.value}" w JSON-ie. Skoro wartości się '
            "różnią, co najmniej jedna deklaruje podmiot publiczny — a która mówi "
            "prawdę o kwalifikacji z art. 4 pkt 3, jest oceną prawną, nie wyborem "
            "narzędzia.",
            file=sys.stderr,
        )
        print(
            "   Zostaw JEDNO źródło: popraw wartość w JSON-ie albo pomiń flagę.",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved = flag_type or json_type
    if resolved is None:
        print(NO_DEBTOR_TYPE_WARNING, file=sys.stderr)
        return DebtorType.PRIVATE
    return resolved


def _parse_payments(raw_payments, i: int) -> list[dict]:
    """Lista wpłat częściowych z JSON-a → [{"date": date, "amount": Decimal}]."""
    if not isinstance(raw_payments, list):
        print(f"ERROR: Invoice #{i+1}: payments musi być listą", file=sys.stderr)
        sys.exit(1)
    payments = []
    for p in raw_payments:
        try:
            payments.append({
                "date": date.fromisoformat(p["date"]),
                "amount": Decimal(str(p["amount"])),
            })
        except (KeyError, ValueError, TypeError, ArithmeticError):
            print(
                f"ERROR: Invoice #{i+1} invalid payment: {p!r} "
                '(oczekiwane {"date": "RRRR-MM-DD", "amount": kwota})',
                file=sys.stderr,
            )
            sys.exit(1)
    return payments


def main():
    parser = argparse.ArgumentParser(
        description="Kalkulator rekompensat i odsetek handlowych — Rekompensa.pl"
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s (tplegal-demand-generator {__version__})",
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
    parser.add_argument(
        "--debtor-type", "-d",
        type=str,
        choices=DEBTOR_TYPE_CHOICES,
        default=None,
        help=(
            "Status dłużnika wg art. 4 pkt 3 ustawy z 8.03.2013. Brak wartości = "
            "private (z ostrzeżeniem na stderr). Wartości publiczne są dziś "
            "ZABLOKOWANE — reżim art. 8 jest niekompletny"
        ),
    )

    args = parser.parse_args()

    # Kontrola aktualności tabel stawek — na stderr, żeby nie zaśmiecić JSON-a
    # na stdout. Świadomie przy KAŻDYM uruchomieniu: osobnej komendy check-rates
    # nikt nie odpali z własnej woli, a to jest ostatni moment, w którym da się
    # zauważyć, że wezwanie zaraz policzy się po nieaktualnej stawce.
    warn_if_stale()

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
        if errors:
            print(f"ERROR: Invoice #{i+1} missing fields: {', '.join(errors)}", file=sys.stderr)
            sys.exit(1)

        try:
            due_date = date.fromisoformat(inv["due_date"])
        except (ValueError, TypeError):
            print(f"ERROR: Invoice #{i+1} invalid due_date: {inv['due_date']}", file=sys.stderr)
            sys.exit(1)

        # payment_date = zapłata CAŁOŚCI; payments = wpłaty częściowe. Oba naraz
        # to dwa źródła prawdy o zapłacie — nie zgadujemy, które jest dobre.
        raw_payment = inv.get("payment_date")
        raw_payments = inv.get("payments")
        payments = None
        if raw_payments is not None and raw_payment is not None:
            print(
                f"ERROR: Invoice #{i+1}: podano i payment_date, i payments. "
                "payment_date = zapłata całości jednego dnia, payments = wpłaty "
                "częściowe — zostaw jedno.",
                file=sys.stderr,
            )
            sys.exit(1)
        if raw_payment is None:
            # Faktura niezapłacona albo zapłacona częściowo: reszta niesie
            # odsetki do dziś i jest należnością główną.
            payment_date = date.today()
            payments = _parse_payments(raw_payments or [], i)
        else:
            try:
                payment_date = date.fromisoformat(raw_payment)
            except (ValueError, TypeError):
                print(f"ERROR: Invoice #{i+1} invalid payment_date: {raw_payment}", file=sys.stderr)
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
            "payments": payments,
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

    _require_readable_debtor_type_keys(raw)
    debtor_type = _resolve_debtor_type(args.debtor_type, raw.get(DEBTOR_TYPE_KEY))

    # Kalkulacja. NotImplementedError podnosi bramka reżimu art. 8 w calc.py
    # (_require_implemented_debtor_type) — CLI jej nie duplikuje, tylko zamienia
    # traceback na komunikat, z którego widać, co dalej.
    try:
        result = calculate_batch(
            invoices, lawsuit_date=lawsuit_date, debtor_type=debtor_type
        )
    except NotImplementedError as e:
        print(f"⛔ Dłużnik publiczny ({debtor_type.value}): {e}", file=sys.stderr)
        print(
            "   Nie licz odsetek ręcznie i NIE zmieniaj statusu na 'private', "
            "żeby obejść blokadę — kwota policzona bez limitu terminu z art. 8 "
            "ust. 2/4a byłaby zawyżona, a pismo powoływałoby art. 7 zamiast "
            "art. 8 ust. 1. Zgłoś sprawę radcy.",
            file=sys.stderr,
        )
        sys.exit(1)
    except ValueError as e:
        # Wpłaty niespójne z fakturą (nadpłata, kwota ≤ 0) — nie ma bezpiecznej
        # kwoty do wezwania, więc bez wyniku na stdout.
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Output JSON
    print(json.dumps(result, cls=_DecimalEncoder, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
