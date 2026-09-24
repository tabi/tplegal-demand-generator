"""
rekompensa — shared utilities.
"""

import re

# ---------------------------------------------------------------------------
# Normalizacja nazw podmiotów
# ---------------------------------------------------------------------------

POLISH_STOPWORDS = {'z', 'w', 'i', 'o', 'do', 'na', 'od', 'we', 'ze', 'za',
                    'po', 'pod', 'przy', 'nad', 'ku', 'u', 'dla'}

LOWERCASE_LEGAL = {'ograniczoną', 'odpowiedzialnością', 'jawna', 'jawną',
                   'komandytowa', 'komandytową', 'partnerska', 'partnerską',
                   'cywilna', 'cywilną', 'komandytowo-akcyjna', 'komandytowo-akcyjną'}

KNOWN_ABBREVIATIONS = {'SP.', 'SP', 'O.O.', 'S.A.', 'S.K.A.', 'PHU', 'FHU',
                       'PPHU', 'ZPH', 'P.W.', 'PW', 'NIP', 'KRS', 'WRI', 'ZPO',
                       'S.C.', 'S.J.', 'S.K.', 'P.S.A.', 'SP.J.', 'SP.K.'}

# Post-processing: poprawne skróty form prawnych
# Order matters: specific (no-space) patterns AFTER general (with-space) patterns
LEGAL_FORM_FIXES = [
    (re.compile(r'\bSP\.\s*[zZ]\s*O\.O\.', re.IGNORECASE), 'Sp. z o.o.'),
    (re.compile(r'\bSP\.\s+K\.', re.IGNORECASE), 'Sp. k.'),   # SP. K. (with space)
    (re.compile(r'\bSP\.K\.', re.IGNORECASE), 'Sp.k.'),        # SP.K. (no space)
    (re.compile(r'\bSP\.\s+J\.', re.IGNORECASE), 'Sp. j.'),   # SP. J. (with space)
    (re.compile(r'\bSP\.J\.', re.IGNORECASE), 'Sp.j.'),        # SP.J. (no space)
    (re.compile(r'\bS\.A\.'), 'S.A.'),
    (re.compile(r'\bS\.C\.', re.IGNORECASE), 's.c.'),
]


def normalize_entity_name(name: str) -> str:
    """
    Normalizuje nazwę podmiotu z ERP/GUS.

    Reguły (w kolejności priorytetu):
    1. Znany skrót (KNOWN_ABBREVIATIONS) -> kapitaliki (zawsze)
    2. Przyimek/spójnik (POLISH_STOPWORDS), nie na początku -> z małej
    3. Słowo formy prawnej (LOWERCASE_LEGAL), nie na początku -> z małej
    4. Wszystko inne -> capitalize()
    5. Post-processing: poprawne skróty form prawnych (Sp. z o.o., Sp.k., etc.)
    """
    words = name.split()
    result = []
    for idx, word in enumerate(words):
        word_lower = word.lower().rstrip('.')

        # 1. Known abbreviation -> uppercase
        if word.upper() in KNOWN_ABBREVIATIONS or word.upper().rstrip('.') + '.' in KNOWN_ABBREVIATIONS:
            result.append(word.upper())
        # 2. Stopword (not first) -> lowercase
        elif idx > 0 and word_lower in POLISH_STOPWORDS:
            result.append(word.lower())
        # 3. Legal form word (not first) -> lowercase
        elif idx > 0 and word_lower in LOWERCASE_LEGAL:
            result.append(word.lower())
        # 4. Everything else -> capitalize
        else:
            result.append(word.capitalize())

    name = ' '.join(result)

    # 5. Post-processing: fix legal form abbreviations
    for pattern, replacement in LEGAL_FORM_FIXES:
        name = pattern.sub(replacement, name)

    return name


# ---------------------------------------------------------------------------
# Status dłużnika w JSON-ie — wykrywanie klucza zapisanego inaczej
# ---------------------------------------------------------------------------

DEBTOR_TYPE_KEY = "debtor_type"


BANK_ACCOUNT_PLACEHOLDER = "___"


def format_bank_account(raw) -> str:
    """Numer rachunku do pisma: zawsze bez „PL", grupy 2+4×6, suma kontrolna sprawdzona.

    Format jak w stopce kancelarii („60 1140 2004 0000 3802 7707 5123"), żeby
    pismo nie miało dwóch zapisów rachunku. Brak numeru → placeholder do
    uzupełnienia w Wordzie. Numer z błędną sumą kontrolną (IBAN mod 97,
    ISO 13616) → ValueError: literówka kieruje wpłatę dłużnika na zły rachunek.
    Tylko rachunki polskie (26 cyfr NRB) — zagraniczny IBAN też jest błędem.
    """
    if raw is None:
        return BANK_ACCOUNT_PLACEHOLDER
    compact = re.sub(r"[\s\-]", "", str(raw)).upper()
    if not compact or set(compact) == {"_"}:
        return BANK_ACCOUNT_PLACEHOLDER
    if compact.startswith("PL"):
        compact = compact[2:]
    if not re.fullmatch(r"\d{26}", compact):
        raise ValueError(
            f"nieprawidłowy numer rachunku {raw!r}: oczekiwane 26 cyfr polskiego "
            "rachunku (NRB), opcjonalnie z prefiksem PL. Rachunek zagraniczny "
            "wpisz ręcznie w Wordzie."
        )
    # IBAN mod 97: 4 znaki z przodu (PL + cyfry kontrolne) na koniec, PL → 2521.
    if int(compact[2:] + "2521" + compact[:2]) % 97 != 1:
        raise ValueError(
            f"numer rachunku {raw!r} ma błędną sumę kontrolną — literówka albo "
            "przestawione cyfry. Sprawdź numer u źródła (faktura, umowa)."
        )
    return compact[:2] + " " + " ".join(compact[i:i + 4] for i in range(2, 26, 4))


def _normalized_key(key: str) -> str:
    """Klucz bez znaków nieliterowych, małymi literami: 'debtorType' -> 'debtortype'."""
    return re.sub(r"[^a-z0-9]", "", key.lower())


def find_misspelled_debtor_type_keys(payload) -> list[str]:
    """Klucze, które ZNACZĄ status dłużnika, ale nie nazywają się `debtor_type`.

    Model budujący JSON pisze czasem `debtorType`, `debtor-type` albo
    `"debtor_type "` ze spacją — a taki klucz zostałby po cichu zignorowany
    i kwalifikacja prawna podana przez radcę wyparowałaby bez śladu. Skoro
    zasadą tej ścieżki jest „brak statusu wolno przyjąć, ale nie po cichu",
    to status PODANY i nieprzeczytany musi być błędem.

    Szuka też w zagnieżdżeniach (pozycje `invoices`, obiekt `debtor`), bo tam
    klucz jest równie niewidoczny dla parsera jak literówka.

    Zwraca posortowane, unikalne nazwy kluczy — pusta lista znaczy „czysto".
    """
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and _normalized_key(key) == "debtortype":
                    if key != DEBTOR_TYPE_KEY:
                        found.add(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return sorted(found)


def nested_debtor_type_values(payload) -> list[str]:
    """Wartości `debtor_type` schowane GŁĘBIEJ niż top-level JSON-a.

    `{"invoices": [{..., "debtor_type": "public_medical"}]}` parsuje się bez
    błędu, a status jest ignorowany — kalkulator czyta wyłącznie klucz
    najwyższego poziomu. Cicho zignorowana kwalifikacja prawna to ta sama klasa
    defektu co cichy fallback na `private`.
    """
    found: list[str] = []

    def walk(node, top: bool) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == DEBTOR_TYPE_KEY and not top:
                    found.append(str(value))
                else:
                    walk(value, False)
        elif isinstance(node, list):
            for item in node:
                walk(item, False)

    walk(payload, True)
    return found
