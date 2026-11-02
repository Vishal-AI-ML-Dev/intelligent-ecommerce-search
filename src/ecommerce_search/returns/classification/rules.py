"""`return-baseline-rules-1`: the deterministic maps and the bounded keyword lexicon.

Every rule names the §19 categories it supports. A rule that cannot separate two categories
names both (for example `DEFECTIVE_OR_NOT_WORKING` -> product-quality defect or manufacturing
defect); the baseline then reports an unresolved set instead of choosing.

* **Reason codes** (`return-reason-codes-1`) are customer statements. `OTHER` and `NOT_PROVIDED`
  map to nothing, so text decides.
* **Complaint topics** map only where the topic names one category (`damaged`, `wrong_item`).
* **Inspection conditions** never create a prediction on their own: the return reason is the
  customer's, and an observation can only support or contradict what a customer said.
* **Keywords** are fixed phrases in English, romanized Hinglish and Devanagari. Lone words that
  often mislead ("small", "colour", "wrong", "late") are not rules; only phrases are. Matching
  is greedy, longest phrase first, and a token belongs to at most one match.
* **Misspellings** count only with nearby product context (`FUZZY_CONTEXT_WORDS`).
* **Negation:** a match of a `negatable` phrase is suppressed when a negation word that is not
  part of another match appears up to `NEGATION_BEFORE` tokens before it, or a Hinglish
  negation word up to `NEGATION_AFTER` tokens after it ("toota nahi hai"), with no clause break
  (punctuation or "but"/"lekin"/"magar") in between. Phrases that contain their own negation
  ("not working", "fit nahi") are not negatable.

The lexicon is hand-written from general knowledge of how such complaints are phrased. It was
not derived from, or tuned on, the M18 demo dataset or its generator scenarios.
"""

from dataclasses import dataclass

from ecommerce_search.returns.classification.taxonomy import ReturnCategory
from ecommerce_search.returns.classification.text import tokenize
from ecommerce_search.returns.contracts import ComplaintTopic, InspectionCondition, ReasonCode

C = ReturnCategory
TOO_SMALL = "too_small"
TOO_LARGE = "too_large"

DEFECT = frozenset({C.PRODUCT_QUALITY_DEFECT, C.MANUFACTURING_DEFECT})
DESCRIPTION = frozenset({C.EXPECTATION_MISMATCH, C.INCORRECT_SPECIFICATION})


@dataclass(frozen=True, slots=True)
class StructuredRule:
    categories: frozenset[ReturnCategory]
    aspect: str | None = None


REASON_CODE_MAP: dict[ReasonCode, StructuredRule | None] = {
    ReasonCode.SIZE_TOO_SMALL: StructuredRule(frozenset({C.SIZING_FIT}), TOO_SMALL),
    ReasonCode.SIZE_TOO_LARGE: StructuredRule(frozenset({C.SIZING_FIT}), TOO_LARGE),
    ReasonCode.NOT_AS_DESCRIBED: StructuredRule(DESCRIPTION),
    ReasonCode.LOOKS_DIFFERENT_FROM_IMAGE: StructuredRule(frozenset({C.COLOUR_IMAGE_MISMATCH})),
    ReasonCode.SPECIFICATION_DIFFERENT: StructuredRule(frozenset({C.INCORRECT_SPECIFICATION})),
    ReasonCode.DEFECTIVE_OR_NOT_WORKING: StructuredRule(DEFECT),
    ReasonCode.ARRIVED_DAMAGED: StructuredRule(frozenset({C.DAMAGED_PRODUCT})),
    ReasonCode.WRONG_ITEM_RECEIVED: StructuredRule(frozenset({C.WRONG_ITEM_DELIVERED})),
    ReasonCode.ARRIVED_LATE: StructuredRule(frozenset({C.LOGISTICS_DELAY})),
    ReasonCode.NO_LONGER_NEEDED: StructuredRule(frozenset({C.CHANGE_OF_MIND})),
    ReasonCode.OTHER: None,
    ReasonCode.NOT_PROVIDED: None,
}

COMPLAINT_TOPIC_MAP: dict[ComplaintTopic, StructuredRule | None] = {
    ComplaintTopic.DAMAGED: StructuredRule(frozenset({C.DAMAGED_PRODUCT})),
    ComplaintTopic.WRONG_ITEM: StructuredRule(frozenset({C.WRONG_ITEM_DELIVERED})),
    ComplaintTopic.DELIVERY: None,  # a delay or a failed delivery: not separable
    ComplaintTopic.PRODUCT_ISSUE: None,  # any product category
    ComplaintTopic.RETURN_PROCESS: None,
    ComplaintTopic.OTHER: None,
}


@dataclass(frozen=True, slots=True)
class InspectionRule:
    supports: frozenset[ReturnCategory] = frozenset()
    contradicts: frozenset[ReturnCategory] = frozenset()


INSPECTION_MAP: dict[InspectionCondition, InspectionRule] = {
    InspectionCondition.AS_NEW: InspectionRule(contradicts=frozenset({C.DAMAGED_PRODUCT})),
    InspectionCondition.USED_GOOD: InspectionRule(contradicts=frozenset({C.DAMAGED_PRODUCT})),
    InspectionCondition.DAMAGED: InspectionRule(supports=frozenset({C.DAMAGED_PRODUCT})),
    InspectionCondition.FUNCTIONAL_FAULT_CONFIRMED: InspectionRule(supports=DEFECT),
    InspectionCondition.NO_FAULT_FOUND: InspectionRule(contradicts=DEFECT),
    InspectionCondition.ITEM_DIFFERS_FROM_ORDER: InspectionRule(
        supports=frozenset({C.WRONG_ITEM_DELIVERED})
    ),
    InspectionCondition.PARTS_MISSING: InspectionRule(),
    InspectionCondition.NOT_ASSESSABLE: InspectionRule(),
}

# Pairs of conditions that cannot both describe one returned unit.
CONFLICTING_INSPECTIONS: frozenset[frozenset[InspectionCondition]] = frozenset(
    {
        frozenset({InspectionCondition.AS_NEW, InspectionCondition.DAMAGED}),
        frozenset({InspectionCondition.USED_GOOD, InspectionCondition.DAMAGED}),
        frozenset(
            {InspectionCondition.NO_FAULT_FOUND, InspectionCondition.FUNCTIONAL_FAULT_CONFIRMED}
        ),
    }
)

# Coexisting customer reasons are compatible: "no longer needed" with "it arrived broken", or
# "too small" with "arrived late", can both be true. The only statement contradictions are
# opposite size directions (`TOO_SMALL`/`TOO_LARGE`) and a negated mention against the same
# category stated in another record (`baseline.py`).

# ---- keywords ------------------------------------------------------------------------------

NEGATION_BEFORE = 3
NEGATION_AFTER = 2
NEGATORS = frozenset(
    {"not", "no", "never", "without", "dont", "doesnt", "didnt", "isnt", "wasnt", "arent"}
    | {"werent", "hasnt", "havent", "wont", "cant"}
    | {"nahi", "nahin", "nhi", "nai", "नहीं", "नही"}
)
NEGATORS_AFTER = frozenset({"nahi", "nahin", "nhi", "nai", "नहीं", "नही"})
CLAUSE_BREAK_WORDS = frozenset({"but", "however", "lekin", "magar", "लेकिन"})
CLAUSE_BREAK_CHARS = frozenset(",.;:!?|\u0964\u0965")  # including the Devanagari dandas

# Bounded context for misspellings: a fuzzy match whose phrase has no exactly matched token
# counts only with a product-context word up to `FUZZY_CONTEXT_WINDOW` tokens away in the same
# clause ("screen came damagd"). Otherwise it is suppressed as `fuzzy_without_context` ("my
# broker called"), so a lone look-alike word abstains instead of classifying.
FUZZY_CONTEXT_WINDOW = 3
FUZZY_CONTEXT_WORDS = frozenset(
    {"item", "product", "parcel", "package", "packaging", "packing", "box", "screen", "display"}
    | {"phone", "mobile", "laptop", "shoe", "shoes", "headphone", "headphones", "earphones"}
    | {"case", "cover", "body", "glass", "strap", "sole", "charger", "arrived", "came"}
    | {"saman", "samaan", "maal", "aaya", "aayi", "mila", "mili"}
)

# Allowlisted for the bounded misspelling match (`text.py`).
FUZZY_TOKENS = frozenset(
    {"damaged", "broken", "cracked", "scratched", "defective", "faulty", "working", "quality"}
    | {"colour", "different", "delivered", "delivery", "delayed", "specification"}
    | {"specifications", "described", "expected", "received", "ordered"}
)


@dataclass(frozen=True, slots=True)
class KeywordGroup:
    group_id: str
    categories: frozenset[ReturnCategory]
    phrases: tuple[str, ...]
    aspect: str | None = None
    negatable: bool = True


KEYWORD_GROUPS: tuple[KeywordGroup, ...] = (
    KeywordGroup(
        "sizing_small",
        frozenset({C.SIZING_FIT}),
        (
            "too small",
            "too tight",
            "very tight",
            "very small",
            "runs small",
            "size small",
            "smaller size",
            "chhota hai",
            "chhota tha",
            "chhota hua",
            "chota hai",
            "chota tha",
            "chhoti hai",
            "choti hai",
            "size chhota",
            "size chota",
            "tight hai",
            "tight tha",
            "छोटा है",
            "छोटी है",
        ),
        aspect=TOO_SMALL,
    ),
    KeywordGroup(
        "sizing_large",
        frozenset({C.SIZING_FIT}),
        (
            "too big",
            "too large",
            "too loose",
            "very loose",
            "very big",
            "runs large",
            "size big",
            "bigger size",
            "bada hai",
            "bada tha",
            "bara hai",
            "badi hai",
            "size bada",
            "dheela hai",
            "dhila hai",
            "loose hai",
            "बड़ा है",
            "ढीला है",
        ),
        aspect=TOO_LARGE,
    ),
    KeywordGroup(
        "sizing_unspecified",
        frozenset({C.SIZING_FIT}),
        (
            "doesn't fit",
            "does not fit",
            "didn't fit",
            "did not fit",
            "not fitting",
            "fit nahi",
            "fit nhi",
            "fitting nahi",
            "size nahi aaya",
            "size sahi nahi",
        ),
        negatable=False,
    ),
    KeywordGroup(
        "sizing_issue",
        frozenset({C.SIZING_FIT}),
        ("size issue", "size problem", "fitting issue", "fitting problem"),
    ),
    KeywordGroup(
        "size_or_wrong_item",
        frozenset({C.SIZING_FIT, C.WRONG_ITEM_DELIVERED}),
        ("wrong size", "size galat", "galat size"),
    ),
    KeywordGroup(
        "expectation",
        frozenset({C.EXPECTATION_MISMATCH}),
        ("not as expected", "not what i expected"),
        negatable=False,
    ),
    KeywordGroup(
        "expectation_affirmative",
        frozenset({C.EXPECTATION_MISMATCH}),
        ("expected better", "umeed se kam", "ummeed se kam", "expectation se kam"),
    ),
    KeywordGroup(
        "description",
        DESCRIPTION,
        ("not as described",),
        negatable=False,
    ),
    KeywordGroup(
        "description_affirmative",
        DESCRIPTION,
        ("different from description", "description se alag", "description galat"),
    ),
    KeywordGroup(
        "colour_image",
        frozenset({C.COLOUR_IMAGE_MISMATCH}),
        (
            "different colour",
            "different color",
            "colour different",
            "color different",
            "colour mismatch",
            "color mismatch",
            "colour is different",
            "color is different",
            "looks different",
            "different from image",
            "different from picture",
            "different from photo",
            "rang alag",
            "rang different",
            "colour alag",
            "color alag",
            "photo se alag",
            "image se alag",
            "picture se alag",
            "रंग अलग",
        ),
    ),
    KeywordGroup(
        "colour_image_negative_form",
        frozenset({C.COLOUR_IMAGE_MISMATCH}),
        ("not like the picture", "not like the photo", "not like the image", "photo jaisa nahi"),
        negatable=False,
    ),
    KeywordGroup(
        "colour_or_wrong_item",
        frozenset({C.COLOUR_IMAGE_MISMATCH, C.WRONG_ITEM_DELIVERED}),
        ("wrong colour", "wrong color", "galat colour", "galat color", "galat rang"),
    ),
    KeywordGroup(
        "specification",
        frozenset({C.INCORRECT_SPECIFICATION}),
        (
            "specs different",
            "spec different",
            "specification different",
            "specifications different",
            "different specification",
            "different specifications",
            "wrong specification",
            "wrong specifications",
            "wrong specs",
            "specs galat",
            "specification galat",
            "less ram",
            "ram kam",
            "ram is less",
            "less storage",
            "storage kam",
            "storage is less",
            "different processor",
            "processor different",
            "processor alag",
        ),
    ),
    KeywordGroup(
        "defect",
        DEFECT,
        (
            "defective",
            "defect",
            "faulty",
            "malfunction",
            "malfunctioning",
            "dead on arrival",
            "stopped working",
            "overheating",
            "kharab hai",
            "kharab nikla",
            "kharab tha",
            "kharab ho gaya",
            "band ho gaya",
            "poor quality",
            "bad quality",
            "low quality",
            "cheap quality",
            "quality kharab",
            "quality kharaab",
            "खराब है",
        ),
    ),
    KeywordGroup(
        "defect_negative_form",
        DEFECT,
        (
            "not working",
            "doesn't work",
            "does not work",
            "didn't work",
            "not turning on",
            "won't turn on",
            "does not turn on",
            "no sound",
            "kaam nahi",
            "kaam nhi",
            "chal nahi",
            "chalta nahi",
            "nahi chalta",
            "quality achhi nahi",
            "quality acchi nahi",
            "काम नहीं",
        ),
        negatable=False,
    ),
    KeywordGroup(
        "damaged",
        frozenset({C.DAMAGED_PRODUCT}),
        (
            "damaged",
            "damage",
            "broken",
            "cracked",
            "crack",
            "shattered",
            "dented",
            "scratched",
            "torn",
            "arrived damaged",
            "box damaged",
            "packaging damaged",
            "packing damaged",
            "packing kharab",
            "packing tooti",
            "toota",
            "tuta",
            "tooti",
            "tuti",
            "toot gaya",
            "toot gayi",
            "phata",
            "phata hua",
            "phati",
            "टूटा",
            "टूटी",
            "टूट गया",
        ),
    ),
    KeywordGroup(
        "wrong_item",
        frozenset({C.WRONG_ITEM_DELIVERED}),
        (
            "wrong item",
            "wrong product",
            "different item",
            "different product",
            "received something else",
            "galat item",
            "galat product",
            "galat saman",
            "galat samaan",
            "galat maal",
            "dusra product",
            "doosra product",
            "dusra item",
            "doosra item",
            "गलत सामान",
            "गलत प्रोडक्ट",
        ),
    ),
    KeywordGroup(
        "wrong_item_negative_form",
        frozenset({C.WRONG_ITEM_DELIVERED}),
        ("not what i ordered", "not the item i ordered", "not the product i ordered"),
        negatable=False,
    ),
    KeywordGroup(
        "delay",
        frozenset({C.LOGISTICS_DELAY}),
        (
            "arrived late",
            "delivered late",
            "late delivery",
            "delivery late",
            "delivery was late",
            "delayed",
            "delay",
            "took too long",
            "very late",
            "der se",
            "late aaya",
            "late mila",
            "bahut late",
            "देर से",
        ),
    ),
    KeywordGroup(
        "change_of_mind",
        frozenset({C.CHANGE_OF_MIND}),
        (
            "no longer needed",
            "no longer need",
            "no longer required",
            "don't need",
            "do not need",
            "don't want",
            "do not want",
            "not needed anymore",
            "nahi chahiye",
            "zarurat nahi",
            "zaroorat nahi",
            "jarurat nahi",
            "नहीं चाहिए",
        ),
        negatable=False,
    ),
    KeywordGroup(
        "change_of_mind_affirmative",
        frozenset({C.CHANGE_OF_MIND}),
        (
            "changed my mind",
            "change of mind",
            "ordered by mistake",
            "found cheaper",
            "mann badal",
            "man badal",
            "galti se order",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class Phrase:
    rule_id: str
    tokens: tuple[str, ...]
    group: KeywordGroup


def _compile() -> tuple[Phrase, ...]:
    phrases = []
    for group in KEYWORD_GROUPS:
        for phrase in group.phrases:
            tokens = tuple(t.norm for t in tokenize(phrase))
            phrases.append(Phrase(f"text:{group.group_id}:{' '.join(tokens)}", tokens, group))
    return tuple(phrases)


PHRASES = _compile()
