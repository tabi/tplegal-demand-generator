"""
rekompensa — shared utilities.
"""

# ---------------------------------------------------------------------------
# Normalizacja nazw podmiotów
# ---------------------------------------------------------------------------

POLISH_STOPWORDS = {'z', 'w', 'i', 'o', 'do', 'na', 'od', 'we', 'ze', 'za',
                    'po', 'pod', 'przy', 'nad', 'ku', 'u', 'dla'}

LOWERCASE_LEGAL = {'ograniczoną', 'odpowiedzialnością', 'jawna', 'jawną',
                   'komandytowa', 'komandytową', 'partnerska', 'partnerską',
                   'cywilna', 'cywilną', 'komandytowo-akcyjna', 'komandytowo-akcyjną'}

KNOWN_ABBREVIATIONS = {'SP.', 'SP', 'O.O.', 'S.A.', 'S.K.A.', 'PHU', 'FHU',
                       'PPHU', 'ZPH', 'P.W.', 'PW', 'NIP', 'KRS', 'WRI', 'ZPO'}


def normalize_entity_name(name: str) -> str:
    """
    Normalizuje nazwę podmiotu z ERP/GUS.

    Reguły (w kolejności priorytetu):
    1. Znany skrót (KNOWN_ABBREVIATIONS) -> kapitaliki (zawsze)
    2. Przyimek/spójnik (POLISH_STOPWORDS), nie na początku -> z małej
    3. Słowo formy prawnej (LOWERCASE_LEGAL), nie na początku -> z małej
    4. Wszystko inne -> capitalize()
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
    return ' '.join(result)
