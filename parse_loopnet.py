"""Parse the copy-pasted LoopNet results ('all loopnet addresses.txt', 6 pages)
into Site Scout bulk-import lines:  address | price/yr | sqft | source
Anchors on each 'City, CA #####' line; the street is the line above it
(skipping a 'NN,NNN SF Available' line when present); sqft = largest SF figure
in the block; price left blank (per-SF rents don't map cleanly to annual)."""
import re, os

SRC = os.path.join(os.path.dirname(__file__), "all loopnet addresses.txt")

with open(SRC, encoding="utf-8") as f:
    lines = [ln.rstrip("\n") for ln in f]

# strip leading "N\t" line-number artifacts if present, and trim
clean = []
for ln in lines:
    clean.append(ln.strip())
lines = clean

city_re = re.compile(r"^(.+?),\s*CA\s*(\d{5})$")
sf_re = re.compile(r"([\d,]+)\s*SF\b", re.I)
avail_re = re.compile(r"SF\s+(Office/Medical\s+)?Available", re.I)
skip_street = re.compile(r"(Virtual Tour|Star \|)", re.I)

out = []
seen = set()
for i, ln in enumerate(lines):
    m = city_re.match(ln)
    if not m:
        continue
    city, zip5 = m.group(1).strip(), m.group(2)
    # street = nearest non-empty line above, skipping a 'SF Available' line
    j = i - 1
    street = None
    while j >= 0 and j >= i - 4:
        cand = lines[j].strip()
        if not cand or avail_re.search(cand) or cand.endswith("Available"):
            j -= 1
            continue
        street = cand
        break
    if not street:
        continue
    # street must look like an address (starts with number, or 'Corner')
    if not (re.match(r"^\d", street) or street.lower().startswith("corner")):
        continue
    # sqft = largest SF figure within next ~8 lines
    sqft = ""
    best = 0
    for k in range(i + 1, min(i + 9, len(lines))):
        nxt = lines[k]
        if city_re.match(nxt) or "of 138" in nxt:
            break
        for g in sf_re.findall(nxt):
            try:
                v = int(g.replace(",", ""))
                if v > best:
                    best = v
            except ValueError:
                pass
    if best:
        sqft = str(best)
    full = f"{street}, {city} CA {zip5}"
    key = re.sub(r"[^a-z0-9]", "", full.lower())
    if key in seen:
        continue
    seen.add(key)
    out.append(f"{full} | | {sqft} | LoopNet")

dest = os.path.join(os.path.dirname(__file__), "loopnet_import.txt")
with open(dest, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print(f"Parsed {len(out)} unique listings -> {dest}")
for line in out:
    print(line)
