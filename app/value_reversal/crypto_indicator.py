"""Python translation of LuxAlgo's Value Area Reversion Signals (MPL-2.0).

Source: sources/value-area-reversion-official.pine, retrieved 2026-10-08.
Profile and reclaim logic retain the published algorithm, including its radial
value-area expansion. Crypto extension: asset-specific tick and an 18:00 New York 24/7 adapter.
The QQQ translation and frozen books remain unchanged.
This file is subject to the Mozilla Public License 2.0: https://mozilla.org/MPL/2.0/.
"""
import math

def pine_precision(value):
    # PineTS uses ten-decimal assignment/array precision (and skips inexact large round trips).
    scale=10**10
    if abs(value)*scale>=2**53:return value
    scaled=value*scale;base=math.floor(scaled)
    return (base+(scaled-base>=.5))/scale

def profile(bars,cfg):
    if not bars:return None
    low=pine_precision(min(b[3] for b in bars));high=pine_precision(max(b[2] for b in bars))
    width=pine_precision(max((high-low)/cfg.rows,cfg.tick));vol=[0.]*cfg.rows
    # Source assigns equal volume to each inclusive touched bin, including edge bins.
    for _,o,h,l,c,v in bars:
        first=max(0,min(cfg.rows-1,math.floor((l-low)/width)))
        last=max(0,min(cfg.rows-1,math.floor((h-low)/width)))
        each=v/(last-first+1)
        for i in range(first,last+1):vol[i]=pine_precision(vol[i]+each)
    total=0.
    for v in vol:total=pine_precision(total+v)
    if total<=0:return None
    poc=max(range(cfg.rows),key=lambda i:vol[i]);left=right=poc;captured=vol[poc]
    needed=total*(cfg.value_fraction*100)/100
    # Deliberately mirrors the source's distance-from-POC expansion, not a substituted VA formula.
    for distance in range(1,cfg.rows+1):
        # PineTS/Pine comparisons treat differences below 1e-10 as equal.
        if captured>=needed or abs(captured-needed)<1e-10:break
        a,z=poc-distance,poc+distance
        below=vol[a] if a>=0 else 0.;above=vol[z] if z<cfg.rows else 0.
        if a>=0 and (z>=cfg.rows or below>=above):left=a;captured=pine_precision(captured+below)
        elif z<cfg.rows:right=z;captured=pine_precision(captured+above)
    return {'low':low+left*width,'high':low+(right+1)*width,'poc':low+(poc+.5)*width,
            'row_low':low,'row_width':width,'volume':vol,'volume_total':total,
            'actual_band_volume_pct':sum(vol[left:right+1])/total*100}

def advance(setup,bar,previous,levels,max_bars,tick=.01):
    """One closed-candle update of the published directional-volume state machine.

    Returns state and a reclaim candidate. Tracks directional volume across intervening
    opposite/doji candles; no prior-inside requirement; all outside bars count toward expiry.
    """
    if not levels:return None,None
    ts,o,h,l,c,v=bar
    side=1 if c<levels['low'] else -1 if c>levels['high'] else 0
    if side:
        if not setup or setup['side']!=side:
            setup={'side':side,'bars':0,'last_volume':None,'slowing':False,'extreme':l if side>0 else h}
        setup['bars']+=1
        setup['extreme']=min(setup['extreme'],l) if side>0 else max(setup['extreme'],h)
        # The source counts dojis as up volume, while engulfing still requires a strict body.
        directional=(c<o) if side>0 else (c>=o)
        if directional and v>0:
            if setup['last_volume'] is not None and v<setup['last_volume']:setup['slowing']=True
            setup['last_volume']=v
        return setup,None
    candidate=None
    if setup and previous:
        side=setup['side'];po,pc=previous[1],previous[4]
        engulf=(c>o and pc<po and o<=pc and c>=po) if side>0 else (c<o and pc>po and o>=pc and c<=po)
        if engulf and setup['bars']<=max_bars and setup['slowing'] and v>(setup['last_volume'] or 0):
            extreme=min(setup['extreme'],l) if side>0 else max(setup['extreme'],h)
            candidate={'ts':ts,'side':side,'stop':(math.floor(extreme/tick+1e-7)-1)*tick if side>0 else (math.ceil(extreme/tick-1e-7)+1)*tick,
                       'target':levels['high'] if side>0 else levels['low'],
                       'value_low':levels['low'],'value_high':levels['high'],
                       'outside_bars':setup['bars'],'last_directional_volume':setup['last_volume'],
                       'reclaim_volume':v}
    return None,candidate
