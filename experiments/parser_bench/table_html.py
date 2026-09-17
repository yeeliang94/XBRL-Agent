"""Convert a single HTML table to an explicit grid, retaining merged-cell spans."""
from html.parser import HTMLParser


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.row = -1
        self.cells = {}
        self.spans = []
        self.active = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row += 1
        elif tag in ("td", "th"):
            if self.row < 0 or self.active is not None:
                raise ValueError("Malformed table cell")
            attrs = dict(attrs)
            rs, cs = int(attrs.get("rowspan", 1)), int(attrs.get("colspan", 1))
            if not (1 <= rs <= 1000 and 1 <= cs <= 1000):
                raise ValueError("Invalid table span")
            self.active = [rs, cs, []]
        elif tag == "br" and self.active:
            self.active[2].append("\n")

    def handle_data(self, data):
        if self.active:
            self.active[2].append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.active:
            rs, cs, text = self.active
            col = 0
            while (self.row, col) in self.cells:
                col += 1
            for r in range(self.row, self.row + rs):
                for c in range(col, col + cs):
                    if (r, c) in self.cells:
                        raise ValueError("Overlapping table spans")
                    self.cells[r, c] = "".join(text).strip()
            self.spans.append({"row": self.row, "column": col, "rowspan": rs, "colspan": cs})
            self.active = None


def grid(markup):
    parser = TableParser()
    parser.feed(markup)
    if not parser.cells or parser.active:
        raise ValueError("Incomplete or empty table HTML")
    height = max(r for r, c in parser.cells) + 1
    width = max(c for r, c in parser.cells) + 1
    return {"rows": [[parser.cells.get((r, c)) for c in range(width)] for r in range(height)],
            "spans": parser.spans}
