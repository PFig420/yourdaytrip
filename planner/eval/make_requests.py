"""One-off generator for eval/requests.jsonl (Planner.md 11). The output file is kept fixed; do not regenerate it
once runs exist, or results stop being comparable."""
import json
import random
import sys

R = random.Random(20261023)
ORIGINS = ["porto-campanha", "porto-sao-bento", "aveiro-station"]
AUTUMN = ["2026-10-24", "2026-10-25", "2026-10-27", "2026-10-28", "2026-12-08"]
WINDOWS = [("08:00", "21:00"), ("09:00", "19:00"), ("10:00", "18:00"), ("09:30", "22:30"), ("08:30", "17:30"), ("11:00", "20:00")]
INTERESTS = [["history"], ["maritime", "history"], ["art-nouveau", "architecture"], ["nature", "birdwatching"], ["beach"],
             ["azulejos", "religious"], ["local-life", "street-art"], ["family", "kids"], ["salt", "ria"], ["porcelain", "art"], []]
TEXTS = ["quiet places away from crowds", "I love seafood and a view of the water", "something for kids on a rainy-ish day",
         "old churches and tiles", "eels and the lagoon", "a relaxed walk and good coffee", "ships, fishing and the sea",
         "art nouveau houses", "birds and wetlands", "a place to sit with a pastry"]
reqs = []
for i in range(42):
    o = ORIGINS[i % 3]
    reqs.append({"origin": o, "date": AUTUMN[i % 5], "window": WINDOWS[R.randrange(len(WINDOWS))]})
for i in range(8):
    reqs.append({"origin": "aveiro-station", "date": "2027-07-17", "window": WINDOWS[R.randrange(len(WINDOWS))]})
R.shuffle(reqs)
rain = set(R.sample(range(50), 5))
texts = set(R.sample(range(50), 10))
out = []
for i, r in enumerate(reqs):
    out.append({"id": f"r{i + 1:02d}", "origin": r["origin"], "date": r["date"], "leave_after": r["window"][0],
                "back_by": r["window"][1], "interests": R.choice(INTERESTS), "pace": "relaxed" if i % 2 == 0 else "packed",
                "weather": "rain" if i in rain else "any", "include_food": True,
                "text": R.choice(TEXTS) if i in texts else ""})
with open(sys.argv[1] if len(sys.argv) > 1 else "eval/requests.jsonl", "w", encoding="utf-8") as f:
    for r in out:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(len(out), "requests")
