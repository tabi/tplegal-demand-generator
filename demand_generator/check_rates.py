#!/usr/bin/env python3
"""check-rates — czy tabele stawek są jeszcze aktualne.

Do wpięcia w cron / Watchdog. Kody wyjścia:
    0 — obie tabele w porządku,
    1 — do końca horyzontu którejś tabeli zostało mniej niż HORIZON_DAYS,
    2 — horyzont którejś tabeli już minął.

Sprawdzane są DWIE tabele o różnej semantyce — i to jest tu najważniejsze:

  * INTEREST_RATES (odsetki handlowe, art. 7 ust. 1) — okresy są DOMKNIĘTE
    przez art. 11b (stawka zamrożona na półrocze), więc "horyzont" to po prostu
    ostatnie "to" w tabeli. Po tej dacie naliczanie rzuca UnknownRatePeriodError,
    czyli błąd jest głośny.

  * CIVIL_INTEREST_RATES (odsetki KC, art. 481 § 2) — tabela ZDARZENIOWA, nie ma
    końca. Brak nowego wpisu znaczy "stopa referencyjna NBP się nie zmieniła",
    więc niesienie ostatniej stawki w przyszłość jest poprawne. Dlatego tu nie ma
    wyjątku, a horyzontem jest LAST_VERIFIED_DATE + STALENESS_WARNING_DAYS —
    moment, od którego nie wiemy już, czy brak wpisu to "bez zmian", czy
    "nikt tego nie sprawdził".
"""

import sys
from datetime import date, timedelta
from typing import Optional

from demand_generator.calc import INTEREST_RATES
from demand_generator.civil_interest import (
    CIVIL_INTEREST_RATES,
    LAST_VERIFIED_DATE,
    STALENESS_WARNING_DAYS,
)

# Ile dni przed horyzontem zaczynamy ostrzegać.
HORIZON_DAYS = 30

EXIT_OK = 0
EXIT_WARN = 1
EXIT_EXPIRED = 2

_INSTRUKCJA = "PROJECT_INSTRUCTIONS.md, sekcja 'Aktualizacja stawek'"


def _commercial_entry() -> dict:
    last = INTEREST_RATES[-1]
    return {
        "tabela": "INTEREST_RATES (odsetki handlowe, art. 7 ust. 1)",
        "plik": "demand_generator/calc.py",
        "ostatni_wpis": f"{last['from']} .. {last['to']}",
        "stawka": f"{last['rate']:.2f}%",
        "horyzont": last["to_d"],
        "zrodlo": "obwieszczenie ministra wł. ds. gospodarki (art. 11c), M.P.",
    }


def _civil_entry() -> dict:
    effective_date, rate = CIVIL_INTEREST_RATES[-1]
    return {
        "tabela": "CIVIL_INTEREST_RATES (odsetki KC, art. 481 § 2)",
        "plik": "demand_generator/civil_interest.py",
        "ostatni_wpis": f"od {effective_date.isoformat()} (tabela zdarzeniowa, bez końca)",
        "stawka": f"{rate}%",
        "horyzont": LAST_VERIFIED_DATE + timedelta(days=STALENESS_WARNING_DAYS),
        "zrodlo": "obwieszczenie Ministra Sprawiedliwości (art. 481 § 2(4) KC), M.P.",
    }


def status(today: Optional[date] = None) -> dict:
    """Stan obu tabel na dany dzień (domyślnie dziś)."""
    today = today or date.today()
    result = {"today": today, "tabele": [], "exit_code": EXIT_OK}

    for entry in (_commercial_entry(), _civil_entry()):
        days_left = (entry["horyzont"] - today).days
        entry["dni_do_konca"] = days_left
        if days_left < 0:
            entry["stan"] = "PO TERMINIE"
            result["exit_code"] = max(result["exit_code"], EXIT_EXPIRED)
        elif days_left < HORIZON_DAYS:
            entry["stan"] = "UWAGA"
            result["exit_code"] = max(result["exit_code"], EXIT_WARN)
        else:
            entry["stan"] = "OK"
        result["tabele"].append(entry)

    return result


def warn_if_stale(today: Optional[date] = None, stream=None) -> int:
    """Krótkie ostrzeżenie na stderr — wołane przy KAŻDYM uruchomieniu kalkulatora.

    To jest ważniejsze niż osobna komenda check-rates: nikt nie odpali
    check-rates z własnej woli, a kalkulator odpala się przy każdym wezwaniu.
    Zwraca kod stanu (jak status()["exit_code"]), żeby dało się go przetestować.
    """
    stream = sys.stderr if stream is None else stream
    st = status(today)

    for entry in st["tabele"]:
        if entry["stan"] == "OK":
            continue
        if entry["stan"] == "PO TERMINIE":
            print(
                f"UWAGA: {entry['tabela']} jest PRZETERMINOWANA — horyzont "
                f"{entry['horyzont'].isoformat()} minął "
                f"{abs(entry['dni_do_konca'])} dni temu. "
                f"Uzupełnij {entry['plik']} ({_INSTRUKCJA}).",
                file=stream,
            )
        else:
            print(
                f"UWAGA: {entry['tabela']} kończy się "
                f"{entry['horyzont'].isoformat()} — zostało "
                f"{entry['dni_do_konca']} dni. Przygotuj aktualizację "
                f"{entry['plik']} ({_INSTRUKCJA}).",
                file=stream,
            )

    return st["exit_code"]


def main() -> int:
    st = status()
    print(f"check-rates — stan na {st['today'].isoformat()}")
    for entry in st["tabele"]:
        print()
        print(f"  {entry['tabela']}")
        print(f"    plik:          {entry['plik']}")
        print(f"    ostatni wpis:  {entry['ostatni_wpis']}  ({entry['stawka']})")
        print(f"    horyzont:      {entry['horyzont'].isoformat()}")
        print(f"    dni do końca:  {entry['dni_do_konca']}")
        print(f"    stan:          {entry['stan']}")
        print(f"    źródło:        {entry['zrodlo']}")

    print()
    print(
        f"exit={st['exit_code']} "
        f"(0 = ok, 1 = mniej niż {HORIZON_DAYS} dni do końca, 2 = po terminie)"
    )
    if st["exit_code"] != EXIT_OK:
        print()
        print(f"Co zrobić: {_INSTRUKCJA}.")

    return st["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
