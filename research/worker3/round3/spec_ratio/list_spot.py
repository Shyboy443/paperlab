"""List all spot symbols in data.binance.vision monthly klines (S3 listing), cache to data/w3r3_spec_ratio/spot_symbols.json."""
import json, re, urllib.request
from pathlib import Path
OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r3_spec_ratio")
base = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/spot/monthly/klines/"
syms, marker = [], ""
while True:
    url = base + ("&marker=" + marker if marker else "")
    x = urllib.request.urlopen(url, timeout=60).read().decode()
    ps = re.findall(r"<Prefix>data/spot/monthly/klines/([^<]+)/</Prefix>", x)
    syms += ps
    if "<IsTruncated>true</IsTruncated>" not in x or not ps:
        break
    marker = "data/spot/monthly/klines/" + ps[-1] + "/"
print(len(syms))
(OUT / "spot_symbols.json").write_text(json.dumps(sorted(set(syms))))
