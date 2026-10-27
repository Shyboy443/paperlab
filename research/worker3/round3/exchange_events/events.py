"""Build the exchange-event table (type, announcement ms, ticker) from cached public announcement lists.
Binance: CMS catalogs 161 (Delisting), 48 (New listings), 49 (Latest news). Upbit: 'trade' notices.
Output: data/w3r3_exchange_events/events.json"""
import json, re, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
D = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r3_exchange_events")
MKT = {"KRW", "BTC", "USDT", "ETH", "USDC", "BUSD", "FDUSD", "TRY", "EUR", "USD"}


def body(code):
    f = D / "bodies" / f"{code}.json"
    if f.exists():
        return f.read_text(encoding="utf-8")
    f.parent.mkdir(exist_ok=True)
    u = f"https://www.binance.com/bapi/apex/v1/public/cms/article/detail/query?articleCode={code}"
    for k in range(6):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
            b = json.loads(r)["data"]["body"]; f.write_text(b, encoding="utf-8"); time.sleep(2); return b
        except Exception as e:
            print("retry", code, e); time.sleep(15 * (k + 1))
    return ""


def toks(s):
    out = []
    for t in re.split(r",|&| and ", s):
        t = t.strip().split(" ")[-1].strip("()") if t.strip() else ""
        if re.fullmatch(r"[A-Z0-9]{2,15}", t) and t not in MKT:
            out.append(t)
    return out


def binance():
    arts = {x["id"]: x for c in (161, 48, 49) for x in json.loads((D / f"list_{c}.json").read_text(encoding="utf-8"))}.values()
    ev = []
    for a in arts:
        t, ts = a["title"], a["releaseDate"]
        if re.search(r"Margin|Staking|P2P|Option|Earn|Convert|Loan|Leveraged Token|Trading Pair|Grid|Alpha|Stablecoin|Notice|Updates?|Postpone|Cancel", t):
            low = True
        else:
            low = False
        m = re.match(r"Binance (?:Announced the .*? and )?Will Delist (.+?) on \d{4}-\d{2}-\d{2}", t) or \
            re.search(r"Results and Will Delist (.+?) on \d{4}", t)
        if m and not low:
            ev += [("BN_SPOT_DELIST", ts, k, t) for k in toks(m.group(1))]
            continue
        if "Monitoring Tag" in t and "Introducing" not in t:
            m = re.search(r"Monitoring Tag (?:to Include|to) (.+?)(?:,? and Remove|, Remove| on \d{4}|$)", t)
            if m:
                ev += [("BN_MON_ADD", ts, k, t) for k in toks(m.group(1))]
            m = re.search(r"Remove the Monitoring Tag for (.+?)(?:,? and Remove|, Remove|, and| on \d{4}|$)", t)
            if m:
                ev += [("BN_MON_REMOVE", ts, k, t) for k in toks(m.group(1))]
            continue
        if t.startswith("Binance Futures Will Delist") and "COIN-M" not in t.split("Perpetual")[0].replace("USDⓈ-M", "") or \
                (t.startswith("Binance Futures Will Delist") and "USD" in t):
            s = t
            if "Multiple" in t:
                s += " " + body(a["code"])
            syms = sorted(set(re.findall(r"\b([A-Z0-9]{2,20})USDT\b", s)))
            ev += [("BN_FUT_DELIST", ts, k, t) for k in syms]
            continue
        if re.match(r"Binance Will List ", t) or re.search(r"Launchpool|HODLer Airdrops|Megadrop", t):
            ev += [("BN_SPOT_LIST", ts, k, t) for k in re.findall(r"\(([A-Z0-9]{2,15})\)", t) if k not in MKT]
    return ev


def upbit():
    ev = []
    for x in json.loads((D / "upbit_trade.json").read_text(encoding="utf-8")):
        t = x["title"]
        ts = int(datetime.fromisoformat(x["first_listed_at"]).timestamp() * 1000)
        if any(w in t for w in ("정정", "연기", "변경 안내")) and "신규" not in t and "추가" not in t:
            pass
        main = re.split(r"\) \(", t)[0]  # drop trailing '(update ...)' clauses
        ks = []
        for g in re.findall(r"\(([^()]*)\)", main):
            ks += toks(g)
        ks = list(dict.fromkeys(ks))
        if not ks:
            continue
        if ("KRW" in main or "원화" in main) and any(w in main for w in ("추가", "신규", "상장")) and not any(w in main for w in ("종료", "제거", "유의")):
            typ = "UP_KRW_LIST"
        elif any(w in main for w in ("추가", "신규")) and "마켓" in main and not any(w in main for w in ("종료", "유의")):
            typ = "UP_OTHER_LIST"
        elif "유의 종목 지정" in main and "해제" not in main and "연장" not in main:
            typ = "UP_WARN"
        elif "유의 촉구" in main:
            typ = "UP_CAUTION"
        elif "거래지원 종료" in main or "거래 지원 종료" in main:
            typ = "UP_DELIST"
        else:
            continue
        ev += [(typ, ts, k, t) for k in ks]
    return ev


if __name__ == "__main__":
    ev = binance() + upbit()
    ev.sort(key=lambda e: e[1])
    (D / "events.json").write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
    import collections
    c = collections.Counter((e[0], datetime.fromtimestamp(e[1] / 1000, timezone.utc).year) for e in ev)
    for k in sorted(c): print(k, c[k])
