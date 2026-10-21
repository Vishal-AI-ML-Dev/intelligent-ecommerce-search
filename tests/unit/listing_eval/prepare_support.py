"""Hand-written synthetic source CSVs for the M14-6 preparation tests (no real rows)."""

import csv
import hashlib
from pathlib import Path

from evals.listing_decisions.contracts import SourceFile
from evals.listing_decisions.sources import SOURCES

FLIPKART_HEADER = [
    "uniq_id",
    "crawl_timestamp",
    "product_url",
    "product_name",
    "product_category_tree",
    "pid",
    "retail_price",
    "discounted_price",
    "image",
    "is_FK_Advantage_product",
    "description",
    "product_rating",
    "overall_rating",
    "brand",
    "product_specifications",
]
PHONE_HEADER = [
    "model", "price", "rating", "sim", "processor", "ram", "battery", "display", "camera", "card",
    "os",
]  # fmt: skip
LAPTOP_HEADER = [
    "Unnamed: 0", "Model", "Price", "Rating", "Generation", "Core", "Ram", "SSD", "Display",
    "Graphics", "OS", "Warranty",
]  # fmt: skip


def tree(*segments: str) -> str:
    return '["' + " >> ".join(segments) + '"]'


def _key(name: str) -> str:
    return "Wired/Wireless" if name == "Wired_Wireless" else name.replace("_", " ")


def specs(**pairs: str) -> str:
    body = ", ".join('{"key"=>"' + _key(k) + '", "value"=>"' + v + '"}' for k, v in pairs.items())
    return '{"product_specification"=>[' + body + "]}"


def fk(pid: str, itm: str, title: str, path: str, spec: str = "", **extra: str) -> dict:
    row = dict.fromkeys(FLIPKART_HEADER, "")
    row |= {
        "uniq_id": f"u-{pid}",
        "product_url": f"http://www.flipkart.com/x/p/{itm}?pid={pid}",
        "product_name": title,
        "product_category_tree": path,
        "pid": pid,
        "discounted_price": "999",
        "product_specifications": spec,
    }
    return row | extra


SHOE = ("Footwear", "Men's Footwear")
HEADPHONES = ("Mobiles & Accessories", "Mobile Accessories", "Headphones", "X")
FLIPKART_ROWS = [
    fk(
        "SHOE1",
        "itmaaa1",
        "Nike Men's Casual Loafers",
        tree(*SHOE, "Casual Shoes", "Nike"),
        specs(Occasion="Casual", Color="Brown", Outer_Material="Leather", Ideal_For="Men"),
        description="Brown leather loafers by Nike.",
        brand="Nike",
    ),
    fk(  # a size variant of SHOE1's listing: excluded as a duplicate variant
        "SHOE2",
        "itmaaa1",
        "Nike Men's Casual Loafers",
        tree(*SHOE, "Casual Shoes", "Nike"),
        specs(Occasion="Casual", Color="Brown"),
        description="Brown leather loafers by Nike.",
        brand="Nike",
    ),
    fk(  # another listing with the same normalized title: same group as SHOE1
        "SHOE3",
        "itmbbb1",
        "nike  men's casual loafers",
        tree(*SHOE, "Casual Shoes", "Nike"),
        specs(Occasion="Casual", Color="Tan"),
        description="Tan loafers.",
        brand="Nike",
    ),
    fk(
        "SHOE4",
        "itmccc1",
        "Puma Party Loafers",
        tree(*SHOE, "Casual Shoes", "Puma"),
        specs(Occasion="Party"),
        description="Loafers for parties.",
    ),
    fk(
        "SHOE5",
        "itmddd1",
        "Adidas Oxford Lace-Ups",
        tree(*SHOE, "Formal Shoes", "Adidas"),
        specs(Occasion="Formal", Color="Black"),
        description="Black oxford lace-ups.",
        brand="Adidas",
    ),
    fk(
        "SHOE6",
        "itmeee1",
        "Puma Mesh Trainers",
        tree(*SHOE, "Sports Shoes", "Puma"),
        specs(Occasion="Sports", Color="Blue", Outer_Material="Mesh"),
        description="Blue mesh trainers.",
        brand="Puma",
    ),
    fk(
        "SHOE7",
        "itmfff1",
        "Catwalk Women's Wedges",
        tree("Footwear", "Women's Footwear", "Wedges", "Catwalk"),
        specs(Occasion="Party"),
        description="Wedges.",
    ),
    fk(  # an accessory word in an actual footwear row: uncertain, excluded
        "SHOE8",
        "itmggg1",
        "Shoe Laces Pack",
        tree(*SHOE, "Casual Shoes", "Laces"),
    ),
    fk(
        "HP1",
        "itmhhh1",
        "Sony Earbud Headphones",
        tree(*HEADPHONES),
        specs(Headset_Design="Earbud", Wired_Wireless="Wireless", Color="Black"),
        description="Black wireless earbuds.",
        brand="Sony",
    ),
    fk(
        "HP2",
        "itmiii1",
        "Boat Rockerz Headphones",
        tree(*HEADPHONES),
        specs(Headset_Design="Over the Head", Wired_Wireless="Wired"),
        description="Wired headphones.",
    ),
    fk(
        "HP3",
        "itmjjj1",
        "JBL Over Ear Headphones",
        tree(*HEADPHONES),
        specs(Headset_Design="Over the Head", Headphone_Type="Over the Ear"),
        description="Over-ear headphones.",
    ),
    fk(
        "HP4",
        "itmkkk1",
        "Zebronics Canal Headphones",
        tree(*HEADPHONES),
        specs(Headset_Design="Canalphone", Headphone_Type="Over the Ear", Wired_Wireless="Both"),
        description="Headphones.",
    ),
    fk(
        "ACC1",
        "itmlll1",
        "Back Cover for Redmi Note 12",
        tree("Mobiles & Accessories", "Mobile Accessories", "Cases & Covers", "X"),
        description="A soft back cover.",
    ),
    fk(
        "ACC2",
        "itmmmm1",
        "Redmi Note Guard Film",
        tree("Mobiles & Accessories", "Mobile Accessories", "Screen Guards", "X"),
    ),
    fk(
        "ACC3",
        "itmnnn1",
        "Laptop Bag 15 inch",
        tree("Computers", "Laptop Accessories", "Laptop Bags", "X"),
        description="A padded bag.",
    ),
    fk("CL1", "itmooo1", "Alisha Cycling Shorts", tree("Clothing", "Women's Clothing", "Shorts")),
    fk("MOB1", "itmppp1", "Redmi Note 12", tree("Mobiles & Accessories", "Mobiles", "Redmi")),
]


def phone(model: str, processor: str = "Snapdragon 695, Octa Core", os: str = "Android v12", **kw):
    row = {
        "model": model,
        "price": "₹19,999",
        "rating": "81",
        "sim": "Dual Sim, 5G",
        "processor": processor,
        "ram": "6 GB RAM, 128 GB inbuilt",
        "battery": "5000 mAh Battery",
        "display": "6.59 inches, 1080 x 2412 px",
        "camera": "64 MP Rear Camera",
        "card": "Memory Card Supported",
        "os": os,
    }
    return row | kw


PHONE_ROWS = [
    phone("Samsung Galaxy A54 5G"),
    phone("Samsung Galaxy S23 5G", processor="Octa Core Processor, 3 GHz"),  # processor omitted
    phone("Samsung Galaxy Tab S8", display="11 inches, 1600 x 2560 px"),  # not a phone
    phone("Nokia G42", os="No FM Radio"),  # shifted row: column shape fails
    phone("OnePlus Nord CE 3"),
]


def laptop(index: str, model: str, generation: str, ssd: str = "512 GB SSD", **kw) -> dict:
    row = {
        "Unnamed: 0": index,
        "Model": model,
        "Price": "₹50,399",
        "Rating": "70.0",
        "Generation": generation,
        "Core": "Hexa Core, 12 Threads",
        "Ram": "8 GB DDR4 RAM",
        "SSD": ssd,
        "Display": "15.6 inches, 1920 x 1080 pixels",
        "Graphics": "Intel Iris Xe Graphics",
        "OS": "Windows 11 OS",
        "Warranty": "1 Year Warranty",
    }
    return row | kw


LAPTOP_ROWS = [
    laptop("0", "HP Victus 15 Gaming Laptop", "5th Gen AMD Ryzen 5 5600H"),
    laptop("1", "Lenovo IdeaPad 3 Laptop", "12th Gen Intel Core i5 1235U", ssd="1 TB Hard Disk"),
    laptop("2", "Acer Aspire 7 Laptop", "Intel Core Ultra 7 Series 1 155H", ssd=""),
    laptop("3", "HP Laptop Bag", "N/A"),  # title rule: not a laptop
    laptop("4", "Dell Inspiron Laptop", "12th Gen Intel Core i3 1215U", OS="1 Year Warranty"),
]


def _write(path: Path, header: list[str], rows: list[dict]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_sources(root: Path, flipkart=None, phones=None, laptops=None):
    """Write the three synthetic CSVs; return (inputs, pinned sources for exactly these files)."""
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "flipkart": _write(
            root / "flipkart.csv", FLIPKART_HEADER, FLIPKART_ROWS if flipkart is None else flipkart
        ),
        "smartphones": _write(
            root / "phones.csv", PHONE_HEADER, PHONE_ROWS if phones is None else phones
        ),
        "laptop_price": _write(
            root / "laptops.csv", LAPTOP_HEADER, LAPTOP_ROWS if laptops is None else laptops
        ),
    }
    sources = []
    for source in SOURCES:
        data = paths[source.source_id].read_bytes()
        csv_entry = SourceFile(
            name=paths[source.source_id].name,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
        sources.append(source.model_copy(update={"files": (csv_entry,)}))
    return paths, tuple(sources)
