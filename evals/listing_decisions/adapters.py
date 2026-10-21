"""Source adapters `la-1` and label mapping `lm-1` for the M14 external listing set (offline).

Each adapter turns one source CSV row into either an `Adapted` row (the seller fields M13 reads,
its labels, its stratum and its duplicate identities) or an `Excluded` row with a fixed reason.
Rules are frozen here, before any prediction exists; changing one needs a new version.

**Field transformations (`la-1`).** A value is copied unchanged unless stated otherwise. A
field with no supported transformation is omitted, never guessed. Missing seller claims
(`availability` everywhere, `currency` everywhere, `brand` for phones and laptops and for many
Flipkart rows) are left missing: no source documents a currency or availability, and nothing is
fabricated. M13 reports each as `missing_required`, so every such listing routes to review.

* all sources: `seller_id` is a fixed per-source constant; `seller_listing_ref` is the Flipkart
  `pid`, the Laptop Price `Unnamed: 0` value or the Smartphones `row-<n>` (1-based data row);
  `category` and `subcategory` are the `lm-1` label values (decision 1: labels are the seller
  fields M13 reads; `jl-1` never shows them to the model).
* Flipkart: `title` = `product_name` and `description` (omitted when blank), unchanged; `brand`
  and `price` (= `discounted_price`) unchanged, only when not blank; `color`, `material` (shoes)
  and `gender` (shoes) from spec `Color`, `Outer Material` and `Ideal For`, with the spec text's
  backslash escapes removed; `wireless` (headphones) from spec `Wired/Wireless`: exactly
  `Wireless` -> true, exactly `Wired` -> false, otherwise omitted.
* Smartphones: `title` = `model` and `price`, unchanged (a bare model name; there is no
  description); `processor` = the first comma-separated segment of `processor`, whitespace
  collapsed, only when it names a chip family (Snapdragon, Dimensity, Helio, Exynos, Bionic,
  Tiger, Unisoc, Kirin, Tensor); `operating_system` = `os`, unchanged, only when it starts with
  Android or iOS.
* Laptop Price: `title` = `Model` and `price` = `Price`, unchanged; `processor` = the processor
  model matched inside `Generation` (Intel Core i3/i5/i7/i9, AMD Ryzen 3/5/7/9 or AMD Athlon with
  a model number, Intel Celeron N + number, Apple M1-M4 [Pro/Max]), whitespace collapsed, and
  omitted when none matches; `storage_type` only when `SSD` names exactly one medium (`SSD` ->
  `SSD`, `Hard Disk`/`HDD` -> `HDD`; the capacity is dropped), otherwise omitted; `gpu` =
  `Graphics` and `operating_system` = `OS`, unchanged.

Every other column (category tree, `Occasion`, `Headset Design`, `Headphone Type`, ratings,
URLs, images and the remaining specs) is left out. The label sources among them never reach a
listing.

**Labels (`lm-1`).** Category: Flipkart category path (`mapped`); dataset membership for
Smartphones and Laptop Price (`source`). Subcategory only from unambiguous source evidence:

* shoes: path leaf and `Occasion` must agree (`Casual Shoes`+`Casual` -> casual, `Formal
  Shoes`+`Formal` -> formal, `Sports Shoes`+`Sports` -> sports, excluded from exact accuracy);
  any other combination keeps the category label only;
* headphones: `Headset Design` Earbud/Canalphone with `Headphone Type` absent or `In the Ear` ->
  in-ear; `Headphone Type` `Over the Ear` with `Headset Design` absent or `Over the Head` ->
  over-ear. `Over the Head` alone does not separate on-ear from over-ear, and conflicts are not
  resolved: both keep the category label only.

Non-products (`none`) need two pieces of evidence: the category path classifies the row as an
accessory of exactly one project category and nothing else, and the title has an accessory word.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from evals.listing_decisions.sources import MAPPING_VERSION

TREE = re.compile(r'^\["(.*)"\]$', re.S)
_STR = r'"((?:[^"\\]|\\.)*)"'
PAIR = re.compile(r'\{\s*(?:"key"=>' + _STR + r",\s*)?" + r'"value"=>' + _STR + r"\s*\}")
_ESCAPE = re.compile(r"\\(.)", re.S)
ITM = re.compile(r"/p/(itm[0-9a-z]+)")

TITLE_WORDS = {
    "laptop": re.compile(r"\b(laptop|notebook pc|ultrabook|chromebook|macbook)\b", re.I),
    "phone": re.compile(r"\b(smartphone|mobile phone|mobile|cell phone)\b", re.I),
    "headphones": re.compile(r"\b(headphones?|earphones?|earbuds?|headsets?|in-ear)\b", re.I),
    "shoes": re.compile(
        r"\b(shoes?|sneakers?|boots?|sandals?|slippers?|loafers?|heels|flats|wedges|"
        r"ballerinas?|floaters|flip flops?|moccasins?|clogs|mojaris?|juttis?)\b",
        re.I,
    ),
}
ACCESSORY = re.compile(
    r"\b(case|cover|pouch|sleeve|bag|backpack|skin|stand|holder|mount|table|tray|charger|"
    r"cable|adapter|adaptor|battery|batteries|protector|screen guard|tempered|organi[sz]er|"
    r"splitter|cushions?|pads?|laces|shoelaces?|polish|insoles?|rack|cleaner|keyboard|mouse|"
    r"stickers?|decals?|cooling|dock|hub|lens|stylus|selfie stick|otg|panel)\b",
    re.I,
)
HP_LEAF = re.compile(r"headphone|earphone|headset", re.I)
HP_ACC_LEAF = re.compile(r"organi[sz]er|splitter|accessor|cushion|cable", re.I)
FOOTWEAR_ROOTS = {"Men's Footwear", "Women's Footwear", "Kids' & Infant Footwear"}
SHOE_SUBCATEGORIES = {
    ("Casual Shoes", "Casual"): ("casual", "exact"),
    ("Formal Shoes", "Formal"): ("formal", "exact"),
    ("Sports Shoes", "Sports"): ("sports", "excluded_ambiguous_mapping"),
}

# Smartphones and Laptop Price row checks (M14-0 rules; the laptop storage check is dropped so
# that otherwise-valid HDD-only or blank-storage rows are kept).
PHONE_SHAPES = {
    "price": re.compile(r"^₹[\d,]+$"),
    "rating": re.compile(r"^(\d{1,3}(\.\d+)?)?$"),
    "sim": re.compile(r"sim", re.I),
    "processor": re.compile(r"\S"),
    "ram": re.compile(r"RAM|inbuilt", re.I),
    "battery": re.compile(r"mAh", re.I),
    "display": re.compile(r"inch", re.I),
    "camera": re.compile(r"MP|camera", re.I),
    "card": re.compile(r"memory card", re.I),
    "os": re.compile(
        r"^(android|ios|harmony|hongmeng|pragati|eui|blackberry|tizen|kai|windows|symbian|rtos|s30)",
        re.I,
    ),
}
LAPTOP_SHAPES = {
    "Price": re.compile(r"^₹[\d,]+$"),
    "Ram": re.compile(r"RAM", re.I),
    "Display": re.compile(r"inch", re.I),
    "OS": re.compile(r"OS|windows|mac|chrome|dos|ubuntu", re.I),
    "Core": re.compile(r"core|thread", re.I),
}
NON_PHONE = re.compile(r"\b(tab|tablet|pad|ipad|watch|band|gear|fit|mediapad|matepad)\b", re.I)
INCH = re.compile(r"(\d+(?:\.\d+)?)\s*inch", re.I)
LAPTOP_WORD = re.compile(r"\b(laptop|notebook|chromebook|macbook|book)\b", re.I)
LAPTOP_ACCESSORY = re.compile(
    r"\b(case|cover|charger|adapter|bag|sleeve|stand|cable|keyboard|mouse)\b", re.I
)
FAMILY_DROP = re.compile(r"\(.*?\)|\b(5g|4g|lte|\d+\s?gb|\d+\s?tb)\b", re.I)
PHONE_CHIP = re.compile(
    r"^(snapdragon|dimensity|helio|exynos|bionic|tiger|unisoc|kirin|tensor|google tensor)\b",
    re.I,
)
PHONE_OS = re.compile(r"^(android|ios)\b", re.I)
LAPTOP_CPU = re.compile(
    r"\b(intel core i[3579] [0-9]{4,5}[a-z]{0,2}[0-9]?|amd ryzen [3579] [0-9]{4}[a-z]{0,2}|"
    r"amd athlon [0-9a-z]+|intel celeron n[0-9]{4}|apple m[1-4](?: pro| max)?)\b",
    re.I,
)
_SPACE = re.compile(r"[\s​]+")


@dataclass(frozen=True)
class Adapted:
    source_id: str
    source_row_id: str
    fields: dict[str, object]  # the raw listing, in this key order
    labels: tuple[dict, ...]  # TaskLabel JSON bodies
    stratum: str
    identities: tuple[str, ...]  # duplicate identities: records sharing one share a group
    omitted: tuple[str, ...] = field(default=())  # fields whose transformation did not apply


@dataclass(frozen=True)
class Excluded:
    source_id: str
    source_row_id: str
    reason: str


def norm(text: str) -> str:
    return " ".join(text.casefold().split())


def collapse(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def family(name: str) -> str:
    """Model family (`lg-1`): the first two tokens after dropping parenthesized text and
    network/capacity tokens. Deliberately coarse; see `selection`."""
    return norm(" ".join(FAMILY_DROP.sub(" ", name).split()[:2]))


def path_of(tree: str) -> list[str]:
    m = TREE.match(tree.strip())
    raw = m.group(1) if m else tree
    return [p.strip() for p in raw.split(">>")]


def specs_of(text: str) -> dict[str, str]:
    """Keyed spec pairs parsed by regex from the Ruby-hash text (never evaluated). The first
    value of a repeated key is kept."""
    specs: dict[str, str] = {}
    for key, value in PAIR.findall(text or ""):
        if key:
            specs.setdefault(_ESCAPE.sub(r"\1", key), _ESCAPE.sub(r"\1", value))
    return specs


def classify(path: list[str], title: str) -> dict[str, str]:
    """category -> actual | accessory | uncertain, for every category the row touches (M14-0)."""
    out: dict[str, str] = {}
    p2 = path[1] if len(path) > 1 else ""
    p3 = path[2] if len(path) > 2 else ""
    acc_title = bool(ACCESSORY.search(title))
    if path[0] == "Computers" and p2 == "Laptops":
        out["laptop"] = "uncertain" if acc_title else "actual"
    elif any(re.search(r"Laptop (Accessories|Bags|Tables)", s) for s in path):
        out["laptop"] = "accessory"
    if path[0] == "Mobiles & Accessories" and p2 == "Mobiles":
        out["phone"] = "uncertain" if acc_title else "actual"
    elif (
        path[0] == "Mobiles & Accessories" and p2 == "Mobile Accessories" and not HP_LEAF.search(p3)
    ):
        out["phone"] = "accessory"
    if HP_LEAF.search(p3):
        if HP_ACC_LEAF.search(p3):
            out["headphones"] = "accessory"
        else:
            out["headphones"] = "uncertain" if acc_title else "actual"
    if path[0] == "Footwear" and p2 in FOOTWEAR_ROOTS:
        if p3 == "Shoe Care":
            out["shoes"] = "accessory"
        else:
            out["shoes"] = "uncertain" if acc_title else "actual"
    elif any(s in ("Shoe Care", "Shoe Racks") for s in path):
        out["shoes"] = "accessory"
    elif path[0] == "Footwear":
        out["shoes"] = "uncertain"
    for cat, pat in TITLE_WORDS.items():
        if cat not in out and pat.search(title) and not acc_title:
            out[cat] = "uncertain"
    return out


def _label(task: str, value: str, origin: str, *, mapped: bool, scoring: str | None = None):
    body: dict = {"task": task, "value": value, "origin_field": origin}
    body |= (
        {"label_kind": "mapped", "mapping_version": MAPPING_VERSION}
        if mapped
        else {"label_kind": "source"}
    )
    if scoring is not None:
        body["scoring"] = scoring
    return body


def _headphone_subcategory(specs: Mapping[str, str]) -> str | None:
    design, kind = specs.get("Headset Design"), specs.get("Headphone Type")
    if design in ("Earbud", "Canalphone") and kind in (None, "In the Ear"):
        return "in-ear"
    if kind == "Over the Ear" and design in (None, "Over the Head"):
        return "over-ear"
    return None


def adapt_flipkart(row: Mapping[str, str]) -> Adapted | Excluded:
    pid = row["pid"]
    path, title = path_of(row["product_category_tree"]), row["product_name"]
    classes = classify(path, title)
    specs = specs_of(row["product_specifications"])
    if not title.strip():
        return Excluded("flipkart", pid, "blank_title")
    actual = [c for c, v in classes.items() if v == "actual"]
    accessory = [c for c, v in classes.items() if v == "accessory"]
    if len(classes) == 1 and actual and actual[0] in ("shoes", "headphones"):
        category = actual[0]
    elif len(classes) == 1 and accessory and ACCESSORY.search(title):
        category = "none"
    elif len(classes) == 1 and accessory:
        return Excluded("flipkart", pid, "accessory_path_without_accessory_title_word")
    elif len(actual) == 1 and len(classes) == 1:
        return Excluded("flipkart", pid, f"{actual[0]}_from_another_source")
    elif not classes:
        return Excluded("flipkart", pid, "out_of_scope")
    else:
        return Excluded("flipkart", pid, "uncertain_or_multiple_categories")

    labels = [_label("category", category, "product_category_tree", mapped=True)]
    subcategory = scoring = None
    if category == "shoes":
        leaf = path[2] if len(path) > 2 else ""
        subcategory, scoring = SHOE_SUBCATEGORIES.get((leaf, specs.get("Occasion")), (None, None))
        stratum = f"shoes:{subcategory or 'other'}"
        if subcategory is not None:
            labels.append(
                _label(
                    "subcategory",
                    subcategory,
                    "product_category_tree+Occasion",
                    mapped=True,
                    scoring=scoring,
                )
            )
    elif category == "headphones":
        subcategory = _headphone_subcategory(specs)
        stratum = f"headphones:{subcategory or 'unlabelled'}"
        if subcategory is not None:
            labels.append(
                _label(
                    "subcategory",
                    subcategory,
                    "Headset Design+Headphone Type",
                    mapped=True,
                    scoring="exact",
                )
            )
    else:
        stratum = f"none:{accessory[0]}_accessory"

    fields: dict[str, object] = {
        "seller_id": "flipkart-sample",
        "seller_listing_ref": pid,
        "title": title,
    }
    if (row.get("description") or "").strip():
        fields["description"] = row["description"]
    if (row.get("brand") or "").strip():
        fields["brand"] = row["brand"]
    if (row.get("discounted_price") or "").strip():
        fields["price"] = row["discounted_price"]
    if category != "none":
        fields["category"] = category
    if subcategory is not None:
        fields["subcategory"] = subcategory
    omitted = []
    if specs.get("Color"):
        fields["color"] = specs["Color"]
    if category == "shoes":
        for key, name in (("Outer Material", "material"), ("Ideal For", "gender")):
            if specs.get(key):
                fields[name] = specs[key]
    if category == "headphones" and specs.get("Wired/Wireless") is not None:
        wired = {"Wireless": True, "Wired": False}.get(specs["Wired/Wireless"])
        if wired is None:
            omitted.append("wireless")
        else:
            fields["wireless"] = wired
    itm = ITM.search(row.get("product_url") or "")
    identities = [f"fk-title:{norm(title)}"]
    if itm:
        identities.insert(0, f"fk-itm:{itm.group(1)}")
    return Adapted(
        "flipkart", pid, fields, tuple(labels), stratum, tuple(identities), tuple(omitted)
    )


def adapt_smartphone(row: Mapping[str, str], number: int) -> Adapted | Excluded:
    ref = f"row-{number}"
    bad = sorted(k for k, pat in PHONE_SHAPES.items() if not pat.search(row.get(k) or ""))
    if bad:
        return Excluded("smartphones", ref, "column_shape_failed")
    inch = INCH.search(row.get("display") or "")
    if NON_PHONE.search(row["model"]) or (inch is not None and float(inch.group(1)) >= 7.0):
        return Excluded("smartphones", ref, "non_phone_or_display_ge_7in")
    fields: dict[str, object] = {
        "seller_id": "smartphones-kaggle",
        "seller_listing_ref": ref,
        "title": row["model"],
        "price": row["price"],
        "category": "phone",
    }
    omitted = []
    chip = collapse(row["processor"].split(",")[0])
    if PHONE_CHIP.search(chip):
        fields["processor"] = chip
    else:
        omitted.append("processor")
    if PHONE_OS.search(row["os"]):
        fields["operating_system"] = row["os"]
    else:
        omitted.append("operating_system")
    label = _label("category", "phone", "dataset_membership", mapped=False)
    return Adapted(
        "smartphones",
        ref,
        fields,
        (label,),
        "phone",
        (f"sp-name:{norm(row['model'])}", f"sp-family:{family(row['model'])}"),
        tuple(omitted),
    )


def adapt_laptop(row: Mapping[str, str]) -> Adapted | Excluded:
    ref = row["Unnamed: 0"]
    if not LAPTOP_WORD.search(row["Model"]) or LAPTOP_ACCESSORY.search(row["Model"]):
        return Excluded("laptop_price", ref, "title_rule_not_a_laptop")
    if any(not pat.search(row.get(k) or "") for k, pat in LAPTOP_SHAPES.items()):
        return Excluded("laptop_price", ref, "column_shape_failed")
    fields: dict[str, object] = {
        "seller_id": "laptop-price-kaggle",
        "seller_listing_ref": ref,
        "title": row["Model"],
        "price": row["Price"],
        "category": "laptop",
    }
    omitted = []
    cpu = LAPTOP_CPU.search(collapse(row.get("Generation") or ""))
    if cpu:
        fields["processor"] = cpu.group(1)
    else:
        omitted.append("processor")
    storage = row.get("SSD") or ""
    media = {
        name
        for name, pat in (("SSD", r"\bSSD\b"), ("HDD", r"\b(HDD|Hard Disk)\b"))
        if re.search(pat, storage, re.I)
    }
    if len(media) == 1:
        fields["storage_type"] = media.pop()
    else:
        omitted.append("storage_type")
    for key, name in (("Graphics", "gpu"), ("OS", "operating_system")):
        if (row.get(key) or "").strip():
            fields[name] = row[key]
    label = _label("category", "laptop", "dataset_membership", mapped=False)
    return Adapted(
        "laptop_price",
        ref,
        fields,
        (label,),
        "laptop",
        (f"lp-name:{norm(row['Model'])}", f"lp-family:{family(row['Model'])}"),
        tuple(omitted),
    )
