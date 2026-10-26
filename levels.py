"""Haftalık EM seviyeleri: her seviye PWC + k * EM."""
import re
from dataclasses import dataclass

# (k, etiket, tür)
#   rev  : dönüş bölgesi -> dokunuş + 5m/15m kırılım takibi
#   quad : quadrant -> kâr al / ara hedef
#   pwc  : haftalık pivot
LEVELS = [
    (3.0, "3X TOP", "rev"),
    (2.5, "Q+2.5", "quad"),
    (2.0, "2X TOP", "rev"),
    (1.5, "Q+1.5", "quad"),
    (1.0, "EM TOP", "rev"),
    (0.5, "Q+0.5", "quad"),
    (0.0, "PWC", "pwc"),
    (-0.5, "Q-0.5", "quad"),
    (-1.0, "EM BOTTOM", "rev"),
    (-1.5, "Q-1.5", "quad"),
    (-2.0, "2X BOTTOM", "rev"),
    (-2.5, "Q-2.5", "quad"),
    (-3.0, "3X BOTTOM", "rev"),
]
LABEL = {k: label for k, label, _ in LEVELS}

# Yahoo'da farklı yazılan semboller. Listede olmayan sembol aynen kullanılır.
YAHOO = {"SPX": "^GSPC", "NDX": "^NDX"}


@dataclass
class Symbol:
    name: str
    pwc: float
    em: float

    @property
    def ticker(self) -> str:
        return YAHOO.get(self.name, self.name)

    def level(self, k: float) -> float:
        return self.pwc + k * self.em

    def z(self, price: float) -> float:
        return (price - self.pwc) / self.em


def target(k: float) -> float:
    """Dönüş seviyesinden sonraki ilk quadrant: +1 -> +0.5, -2 -> -1.5."""
    return k - 0.5 if k > 0 else k + 0.5


def fmt(x: float) -> str:
    return f"{x:,.2f}"


_SYMBOL = re.compile(r"[A-Za-z]{1,6}")
_FIELD = re.compile(r"(pwc|em(?:\s*value)?|top|bottom)\s*:\s*([\d.,]+)", re.I)


def parse_week(text: str) -> tuple[list[Symbol], list[str]]:
    """Yapıştırılan haftalık veriyi okur. (tamamlananlar, eksik kalan semboller)"""
    found: dict[str, dict] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("/"):
            continue
        m = _FIELD.match(line)
        if m:
            key = m.group(1).lower()
            # TOP/BOTTOM yok sayılır, PWC ± EM'den hesaplanır
            if current and key not in ("top", "bottom"):
                found[current]["pwc" if key == "pwc" else "em"] = float(m.group(2).replace(",", ""))
            continue
        if _SYMBOL.fullmatch(line):
            current = line.upper()
            found[current] = {}
    done = [Symbol(n, v["pwc"], v["em"]) for n, v in found.items() if "pwc" in v and v.get("em")]
    missing = [n for n, v in found.items() if not ("pwc" in v and v.get("em"))]
    return done, missing
