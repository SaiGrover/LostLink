"""Curated lexicon behind LostLink's fixed-vocabulary text model.

This module is the *human-edited source*. The matcher never reads it at run
time: ``python manage.py build_text_model --release <name>`` compiles it into
a versioned JSON release under ``matching/text_model/`` and only that file is
loaded when reports are scored. Editing this file therefore changes nothing
until a new release is built, selected in settings and matches are rebuilt,
so scores can never drift silently (see docs/EVALUATION.md, "Stable text
model").

What the lexicon does
---------------------
* **Concept normalisation.** Surface words and phrases that name the same
  thing map to one canonical token: ``mobile``, ``cellphone``, ``smartphone``
  and ``cell phone`` all become ``phone``; ``cracked``, ``shattered`` and
  ``damaged`` become ``damaged``; ``display`` becomes ``screen``.
* **Hierarchy.** Specific objects also emit their parent concept, so
  ``backpack`` vs ``bag`` share ``bag`` while ``backpack`` vs ``rucksack``
  share both tokens. Brand-bearing product names emit the brand too
  (``iphone`` -> ``iphone apple phone``).
* **Spelling variants and abbreviations.** ``gray``/``grey``,
  ``color``/``colour``, ``aluminum``/``aluminium``, ``blk`` -> ``black``.
* **Term classes.** Each canonical token has a class whose fixed weight says
  how informative the class is for telling two belongings apart. Tokens not
  in the lexicon (rare words, model numbers, names) keep their surface form;
  identifiers such as ``a2338`` or ``sn4471`` get the highest weight so exact
  codes keep their lexical strength.

The class weights are transparent heuristics chosen by the project, not
statistics estimated from data. They replace corpus IDF, which had to be
fitted on whatever reports happened to be in the pool and therefore changed a
pair's score whenever an unrelated report was added.
"""

# Weight per term class (multiplies the sublinear term frequency).
CLASS_WEIGHTS = {
    'identifier': 2.5,   # model / serial numbers, codes with digits
    'brand': 2.0,
    'object': 2.0,       # what the belonging is
    'feature': 1.5,      # distinguishing marks: damage, stickers, cases, straps
    'material': 1.2,
    'colour': 1.0,
    'other': 1.0,        # words outside the lexicon
    'descriptor': 0.6,   # size / age / small numbers
    'place': 0.5,        # campus places: location is scored separately
}

DEFAULT_CLASS = 'other'

# Words that carry no information about *which* object it is.
STOPWORDS = {
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'in', 'on', 'at', 'with', 'for', 'is', 'was',
    'were', 'be', 'been', 'it', 'its', 'this', 'that', 'these', 'those', 'my', 'mine', 'i', 'me',
    'we', 'our', 'you', 'your', 'he', 'she', 'his', 'her', 'they', 'them', 'their', 'near',
    'from', 'by', 'has', 'have', 'had', 'lost', 'found', 'missing', 'misplaced', 'item', 'items',
    'please', 'help', 'locate', 'somewhere', 'someone', 'anyone', 'left', 'some', 'any', 'one',
    'thing', 'stuff', 'very', 'really', 'also', 'just', 'about', 'around', 'maybe', 'probably',
    'approx', 'approximately', 'think', 'believe', 'contact', 'reward', 'if', 'so', 'as', 'are',
    'got', 'get', 'kept', 'keep', 'lying', 'there', 'here', 'today', 'yesterday', 'morning',
    'afternoon', 'evening', 'night', 'pm', 'am', 'reported', 'report', 'please', 'into', 'onto',
    'under', 'over', 'inside', 'outside', 'which', 'who', 'what', 'when', 'where', 'while',
    'colour', 'color', 'coloured', 'colored', 'type', 'kind', 'like', 'look', 'looks',
    'looking', 'made', 'seen', 'belongs', 'belonging', 'owner', 'whoever',
}

# Spelling variants and common misspellings -> one spelling.
SPELLING = {
    'gray': 'grey', 'grayish': 'grey', 'greyish': 'grey', 'colour': 'colour', 'color': 'colour',
    'aluminum': 'aluminium', 'jewelry': 'jewellery', 'jewlery': 'jewellery',
    'favorite': 'favourite', 'center': 'centre', 'theater': 'theatre', 'license': 'licence',
    'walet': 'wallet', 'wallett': 'wallet', 'umbrela': 'umbrella', 'umbrellla': 'umbrella',
    'spectacle': 'spectacles', 'specticles': 'spectacles', 'calculater': 'calculator',
    'bottel': 'bottle', 'headfones': 'headphones', 'earfones': 'earphones', 'laptp': 'laptop',
    'keychian': 'keychain', 'backpak': 'backpack', 'charcher': 'charger', 'chager': 'charger',
    'airpod': 'airpods', 'earpod': 'earpods', 'earbud': 'earbuds', 'mobil': 'mobile',
}

# Short forms students type -> full word (applied before concept lookup).
ABBREVIATIONS = {
    'blk': 'black', 'wht': 'white', 'gry': 'grey', 'grn': 'green', 'blu': 'blue',
    'lib': 'library', 'caf': 'cafeteria', 'audi': 'auditorium', 'hstl': 'hostel',
    'specs': 'spectacles', 'bt': 'bluetooth', 'hp': 'hp', 'pb': 'powerbank',
    'phn': 'phone', 'mob': 'mobile', 'lappy': 'laptop', 'lap': 'laptop', 'btl': 'bottle',
    'umb': 'umbrella', 'pkt': 'pocket', 'w': 'with', 'wo': 'without',
}

# Shade modifiers are dropped when they qualify a colour ("dark blue" -> blue).
SHADE_WORDS = {'dark', 'light', 'pale', 'deep', 'bright', 'matte', 'matt', 'glossy', 'metallic',
               'shiny', 'faded'}

# Canonical token -> (class, single-word surface forms).
CONCEPTS = {
    # objects
    'phone': ('object', ['phone', 'mobile', 'cellphone', 'smartphone', 'handset', 'telephone']),
    'laptop': ('object', ['laptop', 'chromebook', 'ultrabook']),
    'tablet': ('object', ['tablet']),
    'charger': ('object', ['charger', 'adapter', 'adaptor', 'charging']),
    'cable': ('object', ['cable', 'cord', 'wire', 'lead']),
    'earphones': ('object', ['earphones', 'earphone', 'earbuds', 'earpods', 'headset', 'tws',
                             'earpiece']),
    'headphones': ('object', ['headphones', 'headphone']),
    'powerbank': ('object', ['powerbank']),
    'usbdrive': ('object', ['pendrive', 'usb', 'flashdrive', 'thumbdrive']),
    'calculator': ('object', ['calculator']),
    'mouse': ('object', ['mouse']),
    'watch': ('object', ['watch', 'wristwatch']),
    'wallet': ('object', ['wallet', 'purse', 'billfold', 'cardholder', 'moneybag']),
    'bag': ('object', ['bag', 'satchel', 'tote', 'handbag', 'sling', 'duffel', 'duffle',
                       'pouch', 'schoolbag', 'laptopbag', 'messenger']),
    'bottle': ('object', ['bottle', 'flask', 'sipper', 'tumbler', 'thermos']),
    'key': ('object', ['key', 'keys']),
    'keychain': ('object', ['keychain', 'keyring', 'lanyard', 'fob']),
    'idcard': ('object', ['idcard', 'icard']),
    'card': ('object', ['card']),
    'glasses': ('object', ['glasses', 'spectacles', 'eyeglasses', 'eyewear', 'goggles']),
    'umbrella': ('object', ['umbrella', 'brolly']),
    'jacket': ('object', ['jacket', 'coat', 'blazer', 'windcheater', 'raincoat', 'parka']),
    'sweater': ('object', ['sweater', 'jumper', 'pullover', 'cardigan', 'sweatshirt']),
    'hoodie': ('object', ['hoodie', 'hoody']),
    'cap': ('object', ['cap', 'hat', 'beanie']),
    'shoes': ('object', ['shoes', 'shoe', 'sneakers', 'sneaker', 'trainers', 'footwear', 'slippers',
                         'sandals', 'chappals']),
    'book': ('object', ['book', 'textbook', 'novel', 'paperback', 'hardcover']),
    'notebook': ('object', ['notebook', 'diary', 'journal', 'register', 'notepad', 'copy']),
    'pen': ('object', ['pen', 'pencil', 'marker', 'stylus']),
    'pencilcase': ('object', ['pencilcase', 'geometrybox']),
    'ring': ('object', ['ring']),
    'earring': ('object', ['earring', 'earrings', 'stud', 'studs']),
    'necklace': ('object', ['necklace', 'chain', 'pendant', 'locket']),
    'bracelet': ('object', ['bracelet', 'bangle', 'band', 'kada']),
    'lunchbox': ('object', ['lunchbox', 'tiffin']),
    'helmet': ('object', ['helmet']),
    'scarf': ('object', ['scarf', 'stole', 'muffler', 'dupatta']),
    'gloves': ('object', ['gloves', 'glove', 'mittens']),
    'camera': ('object', ['camera']),
    'speaker': ('object', ['speaker']),
    'keyboard': ('object', ['keyboard']),
    'documents': ('object', ['documents', 'document', 'certificate', 'papers', 'file', 'folder']),
    'cash': ('object', ['cash', 'money', 'currency', 'rupees']),
    # distinguishing features / condition
    'damaged': ('feature', ['cracked', 'crack', 'broken', 'shattered', 'damaged', 'damage',
                            'smashed', 'chipped', 'busted', 'split']),
    'scratched': ('feature', ['scratched', 'scratch', 'scratches', 'scuffed', 'worn']),
    'dented': ('feature', ['dented', 'dent', 'dents', 'bent']),
    'screen': ('feature', ['screen', 'display', 'touchscreen']),
    'case': ('feature', ['case', 'cover', 'sleeve', 'casing', 'skin']),
    'logo': ('feature', ['logo', 'branding', 'emblem', 'badge', 'insignia']),
    'sticker': ('feature', ['sticker', 'stickers', 'decal', 'decals']),
    'tag': ('feature', ['tag', 'label', 'nametag']),
    'strap': ('feature', ['strap', 'straps', 'belt']),
    'zip': ('feature', ['zip', 'zipper', 'zips']),
    'pocket': ('feature', ['pocket', 'pockets', 'compartment']),
    'name': ('feature', ['name', 'initials', 'engraved', 'engraving', 'written']),
    'photo': ('feature', ['photo', 'picture', 'photograph']),
    'stripe': ('feature', ['stripe', 'stripes', 'striped']),
    'print': ('feature', ['print', 'printed', 'pattern', 'patterned', 'floral', 'checked']),
    # materials
    'leather': ('material', ['leather', 'leatherette', 'faux']),
    'metal': ('material', ['metal', 'metallic', 'aluminium', 'iron', 'tin']),
    'steel': ('material', ['steel', 'stainless']),
    'plastic': ('material', ['plastic']),
    'fabric': ('material', ['fabric', 'cloth', 'canvas', 'nylon', 'polyester', 'cotton', 'denim',
                            'wool', 'woollen']),
    'glass': ('material', ['glass']),
    'rubber': ('material', ['rubber', 'silicone']),
    'wooden': ('material', ['wooden', 'wood']),
    'gold': ('colour', ['gold', 'golden']),
    'silver': ('colour', ['silver', 'silvery']),
    # colours (text-side; brand/colour fields are compared separately)
    'black': ('colour', ['black', 'blackish']),
    'white': ('colour', ['white', 'whitish', 'offwhite']),
    'grey': ('colour', ['grey', 'charcoal']),
    'red': ('colour', ['red', 'reddish', 'maroon', 'burgundy', 'crimson', 'scarlet']),
    'blue': ('colour', ['blue', 'bluish', 'blueish', 'azure', 'cobalt']),
    'navy': ('colour', ['navy']),
    'green': ('colour', ['green', 'greenish', 'olive', 'lime', 'mint']),
    'yellow': ('colour', ['yellow', 'mustard']),
    'orange': ('colour', ['orange']),
    'pink': ('colour', ['pink', 'rose', 'magenta']),
    'purple': ('colour', ['purple', 'violet', 'lilac', 'lavender']),
    'brown': ('colour', ['brown', 'tan', 'chocolate', 'coffee']),
    'beige': ('colour', ['beige', 'cream', 'khaki', 'ivory']),
    'teal': ('colour', ['teal', 'turquoise', 'aqua', 'cyan']),
    'dark': ('colour', ['dark']),
    'multicolour': ('colour', ['multicolour', 'multicolor', 'multicoloured', 'multicolored',
                               'colourful', 'colorful', 'rainbow']),
    'transparent': ('colour', ['transparent', 'clear', 'translucent']),
    # brands
    'apple': ('brand', ['apple']),
    'samsung': ('brand', ['samsung']),
    'xiaomi': ('brand', ['xiaomi', 'redmi', 'mi', 'poco']),
    'oneplus': ('brand', ['oneplus']),
    'oppo': ('brand', ['oppo']), 'vivo': ('brand', ['vivo']), 'realme': ('brand', ['realme']),
    'motorola': ('brand', ['motorola', 'moto']), 'nokia': ('brand', ['nokia']),
    'google': ('brand', ['google', 'pixel']),
    'hp': ('brand', ['hp']), 'dell': ('brand', ['dell']), 'lenovo': ('brand', ['lenovo']),
    'asus': ('brand', ['asus']), 'acer': ('brand', ['acer']),
    'sony': ('brand', ['sony']), 'jbl': ('brand', ['jbl']), 'boat': ('brand', ['boat']),
    'bose': ('brand', ['bose']), 'casio': ('brand', ['casio']), 'titan': ('brand', ['titan']),
    'fastrack': ('brand', ['fastrack']), 'fossil': ('brand', ['fossil']),
    'milton': ('brand', ['milton']), 'cello': ('brand', ['cello']),
    'hydroflask': ('brand', ['hydroflask']), 'wildcraft': ('brand', ['wildcraft']),
    'skybags': ('brand', ['skybags']), 'american tourister': ('brand', []),
    'nike': ('brand', ['nike']), 'adidas': ('brand', ['adidas']), 'puma': ('brand', ['puma']),
    'rayban': ('brand', ['rayban']), 'lenskart': ('brand', ['lenskart']),
    'parker': ('brand', ['parker']), 'classmate': ('brand', ['classmate']),
    # product names (also identifiers of a sort)
    'iphone': ('brand', ['iphone']), 'airpods': ('brand', ['airpods']),
    'macbook': ('brand', ['macbook']), 'ipad': ('brand', ['ipad']),
    'galaxy': ('brand', ['galaxy']), 'thinkpad': ('brand', ['thinkpad']),
    # campus places (descriptions often repeat the zone; location is scored separately)
    'library': ('place', ['library']),
    'cafeteria': ('place', ['cafeteria', 'canteen', 'cafe', 'mess', 'foodcourt']),
    'hostel': ('place', ['hostel', 'dorm', 'dormitory', 'residence']),
    'classroom': ('place', ['classroom', 'class', 'lecture', 'lecturehall', 'tutorial']),
    'lab': ('place', ['lab', 'laboratory', 'labs']),
    'auditorium': ('place', ['auditorium', 'hall', 'seminar']),
    'gym': ('place', ['gym', 'sports', 'court', 'ground', 'field', 'stadium']),
    'parking': ('place', ['parking', 'carpark']),
    'gate': ('place', ['gate', 'entrance']),
    'bench': ('place', ['bench', 'desk', 'table', 'seat', 'chair']),
    'campus': ('place', ['campus', 'college', 'university', 'institute', 'school', 'block',
                         'building', 'floor', 'corridor', 'room', 'washroom', 'toilet']),
    'student': ('descriptor', ['student', 'students']),
    # descriptors (weak)
    'small': ('descriptor', ['small', 'tiny', 'mini', 'little', 'compact']),
    'large': ('descriptor', ['large', 'big', 'huge', 'bulky']),
    'old': ('descriptor', ['old', 'used', 'vintage']),
    'new': ('descriptor', ['new']),
    'size': ('descriptor', ['size']),
}

# Specific words that also emit a parent concept or brand.
EXPANSIONS = {
    'iphone': ['iphone', 'apple', 'phone'],
    'airpods': ['airpods', 'apple', 'earphones'],
    'macbook': ['macbook', 'apple', 'laptop'],
    'ipad': ['ipad', 'apple', 'tablet'],
    'galaxy': ['galaxy', 'samsung'],
    'pixel': ['google', 'phone'],
    'thinkpad': ['thinkpad', 'lenovo', 'laptop'],
    'backpack': ['backpack', 'bag'],
    'rucksack': ['backpack', 'bag'],
    'knapsack': ['backpack', 'bag'],
    'sunglasses': ['sunglasses', 'glasses'],
    'shades': ['sunglasses', 'glasses'],
    'smartwatch': ['smartwatch', 'watch'],
    'smartband': ['smartwatch', 'watch'],
    'fitbit': ['smartwatch', 'watch'],
    'black': ['black', 'dark'],
    'navy': ['navy', 'blue'],
    'steel': ['steel', 'metal'],
    'stainless': ['steel', 'metal'],
    'aluminium': ['metal'],
    'idcard': ['idcard', 'card'],
    'icard': ['idcard', 'card'],
    'keychain': ['keychain', 'key'],
    'keyring': ['keychain', 'key'],
    'diary': ['notebook'],
    'textbook': ['book'],
}

# Extra concept classes for expansion targets that are not concepts above.
EXTRA_CLASSES = {
    'backpack': 'object', 'sunglasses': 'object', 'smartwatch': 'object',
}

# Multi-word phrases (normalised tokens joined by single spaces) -> canonical
# tokens. Matched longest-first before single words.
PHRASES = {
    'cell phone': ['phone'], 'mobile phone': ['phone'], 'smart phone': ['phone'],
    'phone cover': ['phone', 'case'], 'back cover': ['case'],
    'id card': ['idcard', 'card'], 'identity card': ['idcard', 'card'],
    'identification card': ['idcard', 'card'], 'student id': ['idcard', 'card'],
    'student id card': ['idcard', 'card'], 'college id': ['idcard', 'card'],
    'college id card': ['idcard', 'card'], 'i card': ['idcard', 'card'],
    'library card': ['idcard', 'card'], 'access card': ['idcard', 'card'],
    'debit card': ['bankcard', 'card'], 'credit card': ['bankcard', 'card'],
    'atm card': ['bankcard', 'card'], 'bank card': ['bankcard', 'card'],
    'power bank': ['powerbank'], 'pen drive': ['usbdrive'], 'flash drive': ['usbdrive'],
    'thumb drive': ['usbdrive'], 'usb stick': ['usbdrive'], 'usb drive': ['usbdrive'],
    'hard drive': ['harddrive'], 'hard disk': ['harddrive'],
    'water bottle': ['bottle'], 'key chain': ['keychain', 'key'], 'key ring': ['keychain', 'key'],
    'bunch of keys': ['key'], 'set of keys': ['key'],
    'ear phones': ['earphones'], 'ear buds': ['earphones'], 'ear pods': ['earphones'],
    'air pods': ['airpods', 'apple', 'earphones'], 'head phones': ['headphones'],
    'hydro flask': ['hydroflask'], 'pencil case': ['pencilcase'], 'pencil box': ['pencilcase'],
    'geometry box': ['pencilcase'], 'lunch box': ['lunchbox'], 'tiffin box': ['lunchbox'],
    'sports complex': ['gym'], 'lecture hall': ['classroom'], 'lecture theatre': ['classroom'],
    'computer lab': ['lab'], 'computer labs': ['lab'], 'reading room': ['library'],
    'food court': ['cafeteria'], 'car park': ['parking'], 'parking lot': ['parking'],
    'main gate': ['gate'], 'notebook computer': ['laptop'], 'laptop bag': ['laptop', 'bag'],
    'laptop sleeve': ['laptop', 'case'], 'school bag': ['backpack', 'bag'],
    'sling bag': ['bag'], 'hand bag': ['bag'], 'tote bag': ['bag'], 'duffel bag': ['bag'],
    'smart watch': ['smartwatch', 'watch'], 'fitness band': ['smartwatch', 'watch'],
    'reading glasses': ['glasses'], 'sun glasses': ['sunglasses', 'glasses'],
    'multi colour': ['multicolour'], 'multi coloured': ['multicolour'],
    'multi color': ['multicolour'], 'multi colored': ['multicolour'],
    'off white': ['white'], 'sky blue': ['blue'], 'royal blue': ['blue'],
    'wine red': ['red'], 'rose gold': ['gold', 'pink'],
    'american tourister': ['american tourister'], 'ray ban': ['rayban'],
    'one plus': ['oneplus'], 'apple phone': ['apple', 'phone'],
    'tempered glass': ['screen'], 'screen guard': ['screen'], 'screen protector': ['screen'],
    'name tag': ['tag', 'name'], 'name written': ['name'], 'brand new': ['new'],
}

# Extra classes for phrase targets that are not concepts above.
PHRASE_CLASSES = {'bankcard': 'object', 'harddrive': 'object'}

RELEASE_NOTES = (
    'Fixed-vocabulary lexical model: concept normalisation (synonyms, paraphrases, '
    'object hierarchy, brands, campus terms), spelling variants and abbreviations, '
    'conservative plural folding, and fixed per-class term weights. Weights are '
    'heuristics shipped with the release, not statistics fitted on the candidate pool.'
)
