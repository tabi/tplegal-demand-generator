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
