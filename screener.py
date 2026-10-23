#!/usr/bin/env python3
"""Screener "Mid Candle Breakout" (small caps) - version consolidation.

Setup :
  1. J1 : gros jour (ou 2 jours) avec >= 100 M d'actions et un gros mouvement.
  2. Consolidation : 1 à 5 jours, volume au plus égal à (volume J1 / 10),
     clôtures dans la zone médiane/haute du J1 (jamais sous 40 % de sa plage).
  3. Cassure : clôture au-dessus du plus haut du J1 après la consolidation.

La page est mise à jour chaque soir après la clôture. La cassure / retest sur
30 minutes reste à ton oeil : la page te dit quels titres surveiller demain.
"""
import html, json, sys
from datetime import datetime, timezone
from pathlib import Path
import yfinance as yf
from yfinance import EquityQuery

# ============================ CRITÈRES (modifiables) ============================
MIN_MOVE_PCT      = 50          # J1 : hausse minimale en %
MAX_MARKET_CAP    = 300_000_000 # small cap : capitalisation max ($)
MIN_VOLUME_J1     = 100_000_000 # J1 : volume minimum (actions)
VOLUME_RATIO      = 10          # consolidation : volume <= volume J1 / 10
RUN_MAX_DAYS      = 2           # le "J1" peut compter 1 ou 2 jours de gros volume
CONSOL_MAX_DAYS   = 5           # consolidation de 1 à 5 jours maximum
CONSOL_MIN_LEVEL  = 0.40        # clôture >= 40 % de la plage (bas->haut) du J1
KEEP_DAYS         = 14          # jours calendaires de suivi

# Titres à suivre à la main : {"TICKER": "date du J1 AAAA-MM-JJ"}
MANUAL_WATCH = {
    "XLAB": "2026-10-16",
}
# ================================================================================

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
OUT = ROOT / "docs" / "index.html"


def find_j1_today():
    """Small caps du jour qui respectent les critères J1 (screener Yahoo)."""
    q = EquityQuery("and", [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("gt", ["percentchange", MIN_MOVE_PCT]),
        EquityQuery("lt", ["intradaymarketcap", MAX_MARKET_CAP]),
        EquityQuery("gt", ["dayvolume", MIN_VOLUME_J1]),
    ])
    res = yf.screen(q, size=250, sortField="percentchange", sortAsc=False)
    return [r["symbol"] for r in res.get("quotes", [])]


def bars(symbol):
    h = yf.Ticker(symbol).history(period="2mo", interval="1d", auto_adjust=False)
    return h.dropna(subset=["Close"])


def analyze(h, added):
    """Analyse un titre à partir de son historique journalier.
    Retourne un dict avec 'status' parmi : run, consol, cassure, invalide."""
    dates = [d.strftime("%Y-%m-%d") for d in h.index]
    rows = list(h.itertuples(index=False))
    n = len(rows)
    eligible = [i for i, d in enumerate(dates) if d <= added]
    if not eligible or eligible[-1] == 0:
        return None
    ia = eligible[-1]
    a, prev = rows[ia], rows[ia - 1]
    move = (a.Close / prev.Close - 1) * 100
    res = {"anchor_date": dates[ia], "move": move, "j1_vol": int(a.Volume),
           "status": "invalide", "why": "", "consol_days": 0}
    if move < MIN_MOVE_PCT or a.Volume < MIN_VOLUME_J1:
        res["why"] = "J1 ne respecte pas les critères (move / volume)"
        return res

    # --- le "run" : J1 + éventuellement un 2e jour vert à gros volume ---
    run, maxvol, i = [ia], a.Volume, ia + 1
    while len(run) < RUN_MAX_DAYS and i < n:
        r = rows[i]
        if r.Close > r.Open and r.Volume > maxvol / VOLUME_RATIO:
            run.append(i)
            maxvol = max(maxvol, r.Volume)
            i += 1
        else:
            break
    low = min(rows[j].Low for j in run)
    high = max(rows[j].High for j in run)
    thr = maxvol / VOLUME_RATIO
    floor = low + CONSOL_MIN_LEVEL * (high - low)
    res.update({"run_days": len(run), "run_end": dates[run[-1]], "j1_vol": int(maxvol),
                "run_low": float(low), "run_high": float(high),
                "mid": float((low + high) / 2), "floor": float(floor)})

    # --- jours suivants ---
    consol = []
    for j in range(i, n):
        r = rows[j]
        if r.Volume <= thr and floor <= r.Close <= high:
            consol.append(j)
            if len(consol) > CONSOL_MAX_DAYS:
                res["why"] = f"consolidation trop longue (> {CONSOL_MAX_DAYS} jours)"
                return res
            continue
        if r.Close > high and consol:
            res.update(status="cassure", break_date=dates[j], break_vol=int(r.Volume),
                       break_close=float(r.Close), consol_days=len(consol),
                       consol_high=float(max(rows[k].High for k in consol)),
                       consol_vol=int(max(rows[k].Volume for k in consol)))
            return res
        if r.Volume > thr:
            res["why"] = f"{dates[j]} : volume {r.Volume/1e6:.1f} M > J1/{VOLUME_RATIO} ({thr/1e6:.1f} M)"
        elif r.Close < floor:
            res["why"] = f"{dates[j]} : clôture {r.Close:.2f} $ sous {CONSOL_MIN_LEVEL:.0%} de la plage J1 ({floor:.2f} $)"
        else:
            res["why"] = f"{dates[j]} : cassure sans consolidation"
        return res

    if consol:
        last = rows[consol[-1]]
        res.update(status="consol", consol_days=len(consol),
                   consol_high=float(max(rows[k].High for k in consol)),
                   consol_vol=int(max(rows[k].Volume for k in consol)),
                   last_close=float(last.Close), last_date=dates[consol[-1]])
    else:
        res.update(status="run", last_close=float(rows[run[-1]].Close), last_date=dates[run[-1]])
    return res


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"watch": []}


def run():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = load_state()
    known = {w["symbol"] for w in state["watch"]}

    for s, d in MANUAL_WATCH.items():
        if s not in known:
            state["watch"].append({"symbol": s, "added": d})
            known.add(s)
    try:
        for s in find_j1_today():
            if s not in known:
                state["watch"].append({"symbol": s, "added": today})
                known.add(s)
    except Exception as ex:
        print("Screener Yahoo indisponible :", ex, file=sys.stderr)

    groups = {"cassure": [], "consol": [], "run": [], "invalide": []}
    keep = []
    for w in state["watch"]:
        age = (datetime.fromisoformat(today) - datetime.fromisoformat(w["added"])).days
        if age > KEEP_DAYS:
            continue
        keep.append(w)
        try:
            e = analyze(bars(w["symbol"]), w["added"])
        except Exception as ex:
            print("Erreur", w["symbol"], ex, file=sys.stderr)
            continue
        if e:
            e["symbol"] = w["symbol"]
            groups[e["status"]].append(e)
    state["watch"] = keep
    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE.write_text(json.dumps(state, indent=1))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(render(groups, state["updated"]))
    print({k: len(v) for k, v in groups.items()})


# ------------------------------- HTML -------------------------------
def vol(v):
    return f"{v/1e6:,.1f} M"


def link(sym):
    s = html.escape(sym)
    return f'<a href="https://finance.yahoo.com/quote/{s}" target="_blank" rel="noopener">{s}</a>'


def table(head, rows_html, ncol):
    body = "".join(rows_html) or f'<tr><td colspan="{ncol}" class="empty">Aucun titre</td></tr>'
    th = "".join(f"<th>{h}</th>" for h in head)
    return f'<div class="wrap"><table><tr>{th}</tr>{body}</table></div>'


def render(g, updated):
    upd = datetime.fromisoformat(updated).strftime("%Y-%m-%d %H:%M UTC")

    cass = [f"<tr class='sig'><td>{link(e['symbol'])}</td><td>{e['anchor_date']}</td>"
            f"<td class='up'>{e['move']:+.0f} %</td><td>{vol(e['j1_vol'])}</td>"
            f"<td>{e['consol_days']}</td><td>{vol(e['consol_vol'])}</td>"
            f"<td>{e['break_date']}</td><td>${e['break_close']:.2f}</td><td>{vol(e['break_vol'])}</td>"
            f"<td>${e['consol_high']:.2f} / ${e['run_high']:.2f}</td></tr>"
            for e in sorted(g["cassure"], key=lambda x: x["break_date"], reverse=True)]

    cons = [f"<tr><td>{link(e['symbol'])}</td><td>{e['anchor_date']}</td>"
            f"<td class='up'>{e['move']:+.0f} %</td><td>{vol(e['j1_vol'])}</td>"
            f"<td>{e['consol_days']}</td><td>{vol(e['consol_vol'])} ({e['j1_vol']/max(e['consol_vol'],1):.0f}x moins)</td>"
            f"<td>${e['last_close']:.2f}</td><td>${e['mid']:.2f}</td>"
            f"<td>${e['consol_high']:.2f}</td><td>${e['run_high']:.2f}</td></tr>"
            for e in sorted(g["consol"], key=lambda x: x["anchor_date"], reverse=True)]

    runs = [f"<tr><td>{link(e['symbol'])}</td><td>{e['anchor_date']}</td>"
            f"<td class='up'>{e['move']:+.0f} %</td><td>{vol(e['j1_vol'])}</td>"
            f"<td>{e['run_days']}</td><td>${e['last_close']:.2f}</td>"
            f"<td>${e['mid']:.2f}</td><td>${e['run_high']:.2f}</td></tr>"
            for e in sorted(g["run"], key=lambda x: x["anchor_date"], reverse=True)]

    inv = [f"<tr><td>{link(e['symbol'])}</td><td>{e['anchor_date']}</td>"
           f"<td class='why'>{html.escape(e['why'])}</td></tr>"
           for e in sorted(g["invalide"], key=lambda x: x["anchor_date"], reverse=True)]

    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Screener Mid Candle Breakout</title>
<style>
:root{{--bg:#fff;--card:#f6f7f9;--tx:#16181d;--mut:#6b7280;--bd:#e3e6ea;--up:#0a8f4d;--dn:#d23b3b;--ac:#2563eb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f1115;--card:#171a21;--tx:#e8eaee;--mut:#8b93a1;--bd:#262b35;--up:#3ecf8e;--dn:#f26d6d;--ac:#6ea0ff}}}}
body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,sans-serif;padding:16px;max-width:1100px;margin:auto}}
h1{{font-size:22px;margin:8px 0 2px}}h2{{font-size:16px;margin:28px 0 4px}}
.sub{{color:var(--mut);font-size:13px}}.hint{{color:var(--mut);font-size:13px;margin:0 0 8px}}
.crit{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px 14px;margin:14px 0;font-size:13px;color:var(--mut)}}
.crit b{{color:var(--tx)}}
.wrap{{overflow-x:auto;border:1px solid var(--bd);border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:14px;white-space:nowrap}}
th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid var(--bd)}}
th{{background:var(--card);font-weight:600;font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.03em}}
tr:last-child td{{border-bottom:0}}
a{{color:var(--ac);font-weight:600;text-decoration:none}}
.up{{color:var(--up)}}.empty{{color:var(--mut);text-align:center;padding:18px}}
tr.sig{{background:color-mix(in srgb,var(--up) 10%,transparent)}}.why{{color:var(--mut);white-space:normal}}
</style></head><body>
<h1>Screener Mid Candle Breakout</h1>
<div class="sub">Dernière mise à jour : {upd}</div>
<div class="crit"><b>J1</b> : small cap &lt; {MAX_MARKET_CAP/1e6:g} M$, hausse ≥ {MIN_MOVE_PCT} %, volume ≥ {MIN_VOLUME_J1/1e6:g} M (1 à {RUN_MAX_DAYS} jours) &nbsp;·&nbsp;
<b>Consolidation</b> : 1 à {CONSOL_MAX_DAYS} jours, volume ≤ J1 / {VOLUME_RATIO}, clôtures entre {CONSOL_MIN_LEVEL:.0%} de la plage du J1 et son plus haut &nbsp;·&nbsp;
<b>Cassure</b> : clôture au-dessus du plus haut du J1</div>

<h2>En consolidation : à surveiller demain</h2>
<p class="hint">C'est ici que tu prépares ta cassure + retest sur 30 minutes. Niveaux : plafond de consolidation, puis plus haut du J1.</p>
{table(["Ticker","J1","Move J1","Vol J1","Jours conso","Vol conso max","Dernière clôture","Milieu J1","Plafond conso","Haut J1"], cons, 10)}

<h2>Cassures récentes (clôture au-dessus du haut du J1)</h2>
{table(["Ticker","J1","Move J1","Vol J1","Jours conso","Vol conso max","Date cassure","Clôture","Vol cassure","Plafond conso / Haut J1"], cass, 10)}

<h2>Nouveaux J1 (en attente de consolidation)</h2>
{table(["Ticker","J1","Move J1","Vol J1","Jours gros vol","Dernière clôture","Milieu J1","Haut J1"], runs, 8)}

<h2>Invalidés</h2>
{table(["Ticker","J1","Raison"], inv, 3)}
</body></html>"""


if __name__ == "__main__":
    run()
