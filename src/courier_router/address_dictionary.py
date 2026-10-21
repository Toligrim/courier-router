"""Read-only, per-route GAR snapshot and conservative street spelling correction."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from contextlib import closing
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sqlite3

SCHEMA_VERSION = 1
NORMALIZER_VERSION = 2
STREET_TYPES = {
    "ул": "улица", "улица": "улица", "пр": "проспект", "пр-кт": "проспект",
    "просп": "проспект", "проспект": "проспект", "пер": "переулок", "переулок": "переулок",
    "ш": "шоссе", "шоссе": "шоссе", "наб": "набережная", "набережная": "набережная",
    "б-р": "бульвар", "бульвар": "бульвар", "линия": "линия", "лн": "линия",
    "проезд": "проезд", "пр-д": "проезд", "аллея": "аллея", "туп": "тупик",
    "тупик": "тупик", "дорога": "дорога", "пл": "площадь", "площадь": "площадь",
}
_MARK = re.compile(r"(?<!\w)(" + "|".join(re.escape(x) for x in sorted(STREET_TYPES, key=len, reverse=True))
                   + r")\.?(?=\s|,|$)", re.I)
_HOUSE = re.compile(r"(?<!\w)(?:д|дом)\.?\s*\d", re.I)
_NUMBER = r"\d+(?:/\d+)?[а-яa-z]?"
_BUILDING = (rf"(?:(?:к|корп|корпус|стр|строение)\.?\s*{_NUMBER}"
             r"|(?:лит|литера)\.?\s*[а-яa-z0-9]+)")
_HOUSE_CONTEXT = re.compile(
    rf"(?:(?:(?:д|дом)\.?\s*)?{_NUMBER}(?:\s*{_BUILDING})*"
    rf"|{_BUILDING}|(?:кв|офис)\.?\s*{_NUMBER})", re.I)
_PROTECTED = {"большая", "малая", "большой", "малый", "верхняя", "нижняя", "верхний", "нижний",
              "северная", "южная", "восточная", "западная", "старая", "новая"}


def text_key(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"(?<=[\w])-(?=[\w])", "", text)
    return " ".join(re.sub(r"[^а-яa-z0-9]+", " ", text).split())


def street_type(text: str) -> str:
    return STREET_TYPES.get(text.lower().strip(" ."), text.lower().strip(" ."))


def street_key(name: str, kind: str) -> str:
    """Canonical comparison key for GAR and prepared street names.

    GAR uses ``15-я В.О.`` for Vasilievsky Island lines, while address input
    normalization expands the same suffix to ``15-я Васильевского острова``.
    Keep the line number and street type outside this alias so they remain hard
    distinctions in exact and fuzzy matching.
    """
    key = text_key(name)
    if kind == "линия":
        key = re.sub(r"\s+васильевского острова$", " во", key)
        key = re.sub(r"\s+в\s*о$", " во", key)
    return key


def _display_street_name(name: str, kind: str) -> str:
    """Render GAR's compact V.O. spelling as a useful Yandex search query."""
    if kind == "линия":
        name = re.sub(r"\s+в\.\s*о\.?$", " Васильевского острова", name, flags=re.I)
        name = re.sub(r"\s+во$", " Васильевского острова", name, flags=re.I)
    return name


@dataclass(frozen=True)
class StreetPart:
    start: int
    end: int
    kind: str
    name: str


def street_part(address: str) -> StreetPart | None:
    """Only parse an explicitly marked street. Keep house/building suffix byte-for-byte."""
    matches = list(_MARK.finditer(address))
    if len(matches) != 1:
        return None
    marker = matches[0]
    left = address.rfind(",", 0, marker.start()) + 1
    right = address.find(",", marker.end())
    right = len(address) if right < 0 else right
    house = _HOUSE.search(address, marker.end(), right)
    if house:
        right = house.start()
    # "улица Ленина 10к2"; the isolated house suffix is not part of a street name.
    tail = re.search(r"\s+\d+(?:/\d+)?[а-яa-z]?(?:\s*(?:к|корп|стр)\.?\s*\d+)?\s*$",
                     address[marker.end():right], re.I)
    if tail and address[marker.end():right][:tail.start()].strip():
        right = marker.end() + tail.start()
    before = address[left:marker.start()].strip()
    after = address[marker.end():right].strip()
    # Without comma boundaries we cannot safely separate an unmarked locality.
    if before and after and street_type(marker.group(1)) != "линия":
        return None
    name = " ".join(x for x in (before, after) if x)
    if not name or len(name) > 160:
        return None
    while left < marker.start() and address[left].isspace():
        left += 1
    while right > marker.end() and address[right - 1].isspace():
        right -= 1
    return StreetPart(left, right, street_type(marker.group(1)), name)


def street_conflict(query: str, title: str) -> bool:
    """Hard distinctions must survive fuzzy matching and Yandex candidate ranking."""
    a, b = street_part(query), street_part(title)
    if not a or not b:
        return False
    if a.kind != b.kind:
        return True
    ak, bk = street_key(a.name, a.kind), street_key(b.name, b.kind)
    return (re.findall(r"\d+", ak) != re.findall(r"\d+", bk)
            or (set(ak.split()) & _PROTECTED) != (set(bk.split()) & _PROTECTED))


@dataclass
class Normalization:
    original: str
    query: str
    status: str = "unchanged"
    dictionary_version: str | None = None
    normalizer_version: int = NORMALIZER_VERSION
    object_id: int | None = None
    original_street: str | None = None
    corrected_street: str | None = None
    candidates: list[dict] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def _locality_key(part: str) -> str:
    part = re.sub(r"^\s*(?:г|город|д|дер|деревня|п|пос|поселок|посёлок|село|тер|территория)\.?\s+",
                  "", part, flags=re.I)
    part = re.sub(r"\s+(?:обл(?:асть)?|район|р-н)\.?\s*$", "", part, flags=re.I)
    return text_key(part)


def _edits(a: str, b: str) -> int:
    """Damerau-Levenshtein with adjacent transpositions (short street names only)."""
    previous_previous = None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            value = min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                value = min(value, previous_previous[j - 2] + 1)
            current.append(value)
        previous_previous, previous = previous, current
    return previous[-1]


class AddressDictionary:
    """Load a complete immutable generation into memory; rotation cannot mix route versions."""
    def __init__(self, database: Path):
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as con:
            meta = dict(con.execute("SELECT key,value FROM metadata"))
            if int(meta.get("schema_version", "0")) != SCHEMA_VERSION:
                raise ValueError("Неподдерживаемая схема адресного справочника")
            self.version = meta["version"]
            self.streets = [dict(zip(("id", "name", "kind", "region", "contexts"), row))
                            for row in con.execute("SELECT object_id,name,kind,region,contexts FROM streets")]
        for row in self.streets:
            row["key"] = street_key(row["name"], row["kind"])
            row["contexts"] = set(json.loads(row["contexts"]))
        self.contexts = set().union(*(row["contexts"] for row in self.streets)) if self.streets else set()

    @classmethod
    def open_current(cls, root: str | Path) -> AddressDictionary | None:
        root = Path(root)
        # The updater holds this lock exclusively only during publication/retention.
        # Readers release it once the snapshot has been loaded into memory.
        import fcntl
        if not root.exists():
            return None
        with (root / ".publish.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            path = root / "current" / "streets.sqlite"
            if (root / "current").is_symlink() and not path.exists():
                raise ValueError("Активный адресный справочник повреждён")
            if not path.exists():
                return None
            return cls(path)

    def normalize(self, address: str) -> Normalization:
        result = Normalization(address, address, dictionary_version=self.version)
        part = street_part(address)
        if part is None:
            result.status = "unparsed"
            return result
        source = street_key(part.name, part.kind)
        context_parts = (address[:part.start] + "," + address[part.end:]).split(",")
        scopes = set()
        for context in context_parts:
            context = context.strip()
            # "д Пески" and "1-е Новое" are localities, not house suffixes.
            if not context or _HOUSE_CONTEXT.fullmatch(context):
                continue
            key = _locality_key(context)
            if key in {"россия", "рф"}:
                continue
            if key not in self.contexts:
                result.status = "unknown_locality"
                return result
            scopes.add(key)
        if not scopes:
            result.status = "missing_locality"
            return result
        candidates = [s for s in self.streets if s["kind"] == part.kind and scopes <= s["contexts"]]
        exact = [s for s in candidates if s["key"] == source]
        if exact:
            result.status = "exact"
            result.object_id = exact[0]["id"] if len(exact) == 1 else None
            return result
        if len(source) < 5:
            result.status = "not_found"
            return result
        scored = []
        for candidate in candidates:
            key = candidate["key"]
            if (re.findall(r"\d+", source) != re.findall(r"\d+", key)
                    or (set(source.split()) & _PROTECTED) != (set(key.split()) & _PROTECTED)):
                continue
            score = SequenceMatcher(None, source, key).ratio()
            if score >= 0.78:
                scored.append((score, candidate))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        result.candidates = [{"object_id": s["id"], "name": s["name"], "type": s["kind"],
                              "score": round(score, 3)} for score, s in scored[:3]]
        if not scored:
            result.status = "not_found"
            return result
        best_score, best = scored[0]
        distance = _edits(source, best["key"])
        if (best_score < 0.88 or distance > (2 if len(source) >= 10 else 1)
                or (len(scored) > 1 and best_score - scored[1][0] < 0.08)):
            result.status = "ambiguous"
            return result
        replacement = best["kind"] + " " + _display_street_name(best["name"], best["kind"])
        result.query = address[:part.start] + replacement + address[part.end:]
        result.status = "corrected"
        result.object_id = best["id"]
        result.original_street = address[part.start:part.end]
        result.corrected_street = replacement
        return result
