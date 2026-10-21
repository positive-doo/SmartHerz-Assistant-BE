"""Select basic cards from the CSV catalogue actually used for this answer."""
import re
import unicodedata


def _words(text: str) -> str:
    # Voice input and answers may use Serbian Cyrillic as well as Latin.
    text = text.casefold().translate(str.maketrans(dict(zip(
        'абвгдђежзијклљмнњопрстћуфхцчџш',
        ['a','b','v','g','d','dj','e','z','z','i','j','k','l','lj','m','n','nj','o','p','r','s','t','c','u','f','h','c','c','dz','s'],
    ))))
    text = ''.join(char for char in unicodedata.normalize('NFD', text) if not unicodedata.combining(char))
    return ' ' + ' '.join(re.findall(r'\w+', text.replace('đ', 'dj'))) + ' '


def csv_recommendations(answer: str, catalogue: dict) -> list[dict] | None:
    if not catalogue:
        return None
    if catalogue.get('error'):
        return []
    text = _words(answer)
    cards = []
    for category, records in [('poi', catalogue.get('places', [])), ('thematic_routes', catalogue.get('routes', []))]:
        for record in records:
            if _words(record['name']) in text:
                card = {'id': record['id'], 'name': record['name'], 'category': category}
                if category == 'poi':
                    card['municipality'] = record['municipality']
                cards.append(card)
    return cards
