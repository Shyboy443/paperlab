"""Publish all fixed cases, cost attribution, charts and a one-page summary."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xml.sax.saxutils import escape

COLORS={'combined':'#007F86','combined_no_regime':'#D67A21','momentum_regime':'#315DA8',
        'momentum_no_regime':'#BE4053','funding_only':'#7556A4','BTC_buy_hold':'#8A929E','USDT_cash':'#A6AFBA',
        'combined_gross':'#397957','combined_without_funding':'#527AB8'}
LABELS={'combined':'Combined + regime, net','combined_no_regime':'Combined without regime, net',
        'momentum_regime':'Momentum + regime','momentum_no_regime':'Momentum without regime',
        'funding_only':'Funding only','BTC_buy_hold':'BTC buy & hold','USDT_cash':'USDT cash',
        'combined_gross':'Combined gross, real funding','combined_without_funding':'Combined net, no funding cashflows'}

def pct(x):return f'{float(x)*100:+.2f}%'
def num(x):return '--' if pd.isna(x) else f'{float(x):.2f}'
def table(headers,rows):
    def cell(v):return '--' if v is None or (isinstance(v,(float,np.floating)) and pd.isna(v)) else str(v)
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |',
                      *['| '+' | '.join(cell(v) for v in row)+' |' for row in rows]])

def series(run,case,period):
    f=pd.read_csv(run/(case+'_'+period)/'daily_equity.csv',index_col=0).iloc[:,0]
    f.index=pd.to_datetime(f.index,utc=True);return f

def chart_style():
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False,
                         'axes.edgecolor':'#C1C8D0','axes.labelcolor':'#263548','xtick.color':'#536171','ytick.color':'#536171',
                         'grid.color':'#DFE4EA','grid.alpha':.75,'figure.facecolor':'white','savefig.facecolor':'white'})

def charts(run,summary,monthly):
    out=run/'charts';out.mkdir(exist_ok=True);chart_style()
    for filename,cases,title,drawdown in [
        ('equity_IS_OOS',['combined','combined_no_regime','BTC_buy_hold','USDT_cash'],'Independent equity books',False),
        ('regime_AB',['momentum_regime','momentum_no_regime'],'Pure momentum A/B - identical risk and costs',False),
        ('drawdown',['combined','combined_no_regime','momentum_regime','momentum_no_regime'],'Drawdown at hourly closing valuations',True)]:
        fig,axes=plt.subplots(1,2,figsize=(15,5.5),constrained_layout=True)
        for ax,period in zip(axes,['IS','OOS']):
            for name in cases:
                if drawdown:
                    h=pd.read_csv(run/(name+'_'+period)/'history.csv.gz',usecols=['timestamp','equity'])
                    s=pd.Series(h.equity.to_numpy(),index=pd.to_datetime(h.timestamp,utc=True))
                else:s=series(run,name,period)
                if drawdown:s=s/np.maximum.accumulate(np.r_[100000,s.to_numpy()])[1:]-1
                else:s=s/100000
                ax.plot(s.index,s.values,label=LABELS[name],color=COLORS[name],lw=2 if name=='combined' else 1.5)
            ax.set_title('IS 2019-2021' if period=='IS' else 'OOS 2022-2025',loc='left',fontweight='bold')
            ax.grid(True);ax.set_ylabel('Drawdown' if drawdown else 'Equity / starting equity')
            if drawdown:ax.yaxis.set_major_formatter(PercentFormatter(1))
            ax.tick_params(axis='x',rotation=25)
        handles,labels=axes[0].get_legend_handles_labels();fig.legend(handles,labels,loc='outside lower center',ncol=2,frameon=False)
        fig.suptitle(title,fontweight='bold',fontsize=17);fig.savefig(out/(filename+'.png'),dpi=180);plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,5),constrained_layout=True)
    for name in ['combined','combined_gross','combined_without_funding']:
        s=series(run,name,'OOS');ax.plot(s.index,s/100000,label=LABELS[name],color=COLORS[name],lw=2)
    ax.set_title('Costs and funding counterfactuals - OOS',loc='left',fontweight='bold',fontsize=16)
    ax.set_ylabel('Equity / starting equity');ax.grid(True);ax.legend(frameon=False);fig.savefig(out/'costs_and_funding.png',dpi=180);plt.close(fig)
    f=monthly[monthly.case=='combined'].copy();f['year']=f.month.str[:4].astype(int);f['m']=f.month.str[5:].astype(int)
    pivot=f.pivot(index='year',columns='m',values='return').reindex(index=range(2019,2026),columns=range(1,13))
    values=pivot.to_numpy()*100;limit=max(5,float(np.nanmax(np.abs(values))))
    fig,ax=plt.subplots(figsize=(14,5.2),constrained_layout=True)
    image=ax.imshow(values,cmap='RdYlGn',vmin=-limit,vmax=limit,aspect='auto')
    for i in range(7):
        for j in range(12):ax.text(j,i,f'{values[i,j]:+.2f}%',ha='center',va='center',fontsize=9,color='#1A2230')
    ax.set_xticks(range(12),['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])
    ax.set_yticks(range(7),range(2019,2026));ax.axhline(2.5,color='#23354B',lw=2)
    ax.set_title('Monthly NET returns - combined regime strategy\nIS above divider / OOS below; cash months included',loc='left',fontsize=15,fontweight='bold')
    fig.colorbar(image,ax=ax,label='Monthly return (%)',fraction=.026,pad=.02);fig.savefig(out/'monthly_return_table.png',dpi=180);plt.close(fig)
    grid=summary[(summary.period=='OOS')&summary.case.str.startswith('sensitivity_')].copy()
    grid['lookback']=grid.case.str.extract(r'_L(\d+)')[0].astype(int);grid['breadth']=grid.case.str.extract(r'_B(\d+)')[0].astype(int)
    pivot=grid.pivot(index='lookback',columns='breadth',values='monthly_average_net_return')*100
    fig,ax=plt.subplots(figsize=(7.5,4),constrained_layout=True);vals=pivot.to_numpy();limit=max(1,float(np.abs(vals).max()))
    ax.imshow(vals,cmap='RdYlGn',vmin=-limit,vmax=limit,aspect='auto')
    for i in range(3):
        for j in range(2):ax.text(j,i,f'{vals[i,j]:+.3f}% / month',ha='center',va='center',fontsize=12)
    ax.set_xticks(range(len(pivot.columns)),[f'Breadth > {b}%' for b in pivot.columns]);ax.set_yticks([0,1,2],['42 days','63 days','90 days'])
    ax.set_title('Fixed OOS sensitivity grid - no best-cell selection',loc='left',fontweight='bold')
    fig.savefig(out/'sensitivity.png',dpi=180);plt.close(fig)

def attribution(root,run,case,period):
    path=run/(case+'_'+period);o=pd.read_csv(path/'orders.csv.gz');h=pd.read_csv(path/'history.csv.gz')
    start=1546300800000 if period=='IS' else 1640995200000;end=1640995200000 if period=='IS' else 1767225600000
    output=[]
    for (symbol,kind),g in o.groupby(['symbol','kind']):
        momentum=kind=='momentum'
        price_pnl=float(-(g.delta_contract_units*g.perp_reference).sum()) if momentum else float((g.delta_contract_units*(g.perp_reference-g.spot_reference_per_contract_unit)).sum())
        funding=pd.read_csv(root/'funding'/(symbol+'.csv'));funding=funding[(funding.timestamp>=start)&(funding.timestamp<end)]
        orders=g.groupby('hour').delta_contract_units.sum().sort_index();cumulative=orders.cumsum().to_numpy()
        fi=((funding.timestamp.to_numpy(dtype='int64')-1546300800000)//3600000)
        ix=np.searchsorted(orders.index.to_numpy(),fi,side='left')-1
        qty=np.where(ix>=0,cumulative[np.maximum(ix,0)],0);held=np.abs(qty)>1e-12
        marks=pd.read_csv(root/'mark_hourly'/(symbol+'.csv'),usecols=['timestamp','open']).set_index('timestamp').open
        prices=marks.reindex(1546300800000+fi[held]*3600000).to_numpy()
        if np.isnan(prices).any():raise AssertionError('Attribution missing funding marks '+symbol)
        cash=float(np.sum(qty[held]*prices*funding.rate.to_numpy()[held]))*(-1 if momentum else 1)
        fees=float(g.fee.sum());slippage=float(g.slippage.sum())
        output.append({'symbol':symbol,'sleeve':kind,'price_or_basis_pnl':price_pnl,'funding_cash':cash,
                       'fees':fees,'slippage':slippage,'net_before_transfer_fees':price_pnl+cash-fees-slippage})
    f=pd.DataFrame(output)
    if abs(f.funding_cash.sum()-h.funding_cash.iloc[-1])>1e-5:raise AssertionError('Funding attribution reconciliation failed')
    if abs(f.net_before_transfer_fees.sum()-h.transfer_fees.iloc[-1]-(h.equity.iloc[-1]-100000))>1e-5:raise AssertionError('Sleeve reconciliation failed')
    f.to_csv(run/(f'attribution_{case}_{period}.csv'),index=False)
    return f.groupby('sleeve')[['price_or_basis_pnl','funding_cash','fees','slippage','net_before_transfer_fees']].sum()

def one_page(run,summary,acceptance,verdict,risk):
    out=run/'pdf';out.mkdir(exist_ok=True);path=out/'Regime-Strategy-Summary.pdf'
    regular=Path('C:/Windows/Fonts/segoeui.ttf');bold=Path('C:/Windows/Fonts/seguisb.ttf')
    font='Helvetica';strong='Helvetica-Bold'
    if regular.exists() and bold.exists():
        pdfmetrics.registerFont(TTFont('UI',str(regular)));pdfmetrics.registerFont(TTFont('UI-Bold',str(bold)));font='UI';strong='UI-Bold'
    c=canvas.Canvas(str(path),pagesize=(595.28,841.89));c.setTitle('Regime-filtered crypto - validated research summary')
    c.setFillColor(HexColor('#14283D'));c.rect(0,737,596,105,fill=1,stroke=0)
    c.setFillColor(HexColor('#FFFFFF'));c.setFont(strong,23);c.drawString(38,797,'Regime-filtered crypto')
    c.setFont(font,10);c.drawString(39,775,'Binance perpetuals + Bybit spot  |  BTC regime  |  Frozen rules')
    c.setFillColor(HexColor('#BFD6E1'));c.setFont(font,9);c.drawString(39,756,'IS 2019-2021  /  OOS 2022-2025  /  Public historical funding')
    def para(text,x,y,width,size=9.3,color='#364458'):
        p=Paragraph(text,ParagraphStyle('p',fontName=font,fontSize=size,leading=size*1.4,textColor=HexColor(color)))
        _,height=p.wrap(width,500);p.drawOn(c,x,y-height);return y-height
    passed=acceptance['passed'];c.setFillColor(HexColor('#007F86' if passed else '#A53447'));c.setFont(strong,12)
    c.drawString(39,714,'ACCEPTANCE: '+('PASS' if passed else 'FAIL'))
    para(escape(verdict),39,699,515,size=10)
    c.setFillColor(HexColor('#EEF3F6'));c.rect(38,623,519,31,fill=1,stroke=0)
    c.setFillColor(HexColor('#14283D'));c.setFont(strong,10);c.drawString(49,634,'Combined strategy');c.drawRightString(420,634,'IS');c.drawRightString(543,634,'OOS')
    def get(case,period):return summary[(summary.case==case)&(summary.period==period)].iloc[0]
    a,b=get('combined','IS'),get('combined','OOS');ga,gb=get('combined_gross','IS'),get('combined_gross','OOS')
    rows=[('Monthly average NET',pct(a.monthly_average_net_return),pct(b.monthly_average_net_return)),
          ('Monthly average gross',pct(ga.monthly_average_net_return),pct(gb.monthly_average_net_return)),
          ('Annualized NET return',pct(a.annualized_return),pct(b.annualized_return)),
          ('Sharpe / Sortino',num(a.sharpe)+' / '+num(a.sortino),num(b.sharpe)+' / '+num(b.sortino)),
          ('Calmar',num(a.calmar),num(b.calmar)),('Max drawdown (hour-close)',f'{a.max_drawdown*100:.2f}%',f'{b.max_drawdown*100:.2f}%'),
          ('Worst month',pct(a.worst_month_return)+' '+a.worst_month,pct(b.worst_month_return)+' '+b.worst_month),
          ('Best month',pct(a.best_month_return)+' '+a.best_month,pct(b.best_month_return)+' '+b.best_month),
          ('Positive months',f'{a.positive_month_fraction*100:.1f}%',f'{b.positive_month_fraction*100:.1f}%'),
          ('Net funding receipts',f'${a.funding_cash:,.0f}',f'${b.funding_cash:,.0f}'),
          ('Fees + slip + transfers',f'${a.fees+a.slippage+a.transfer_fees:,.0f}',f'${b.fees+b.slippage+b.transfer_fees:,.0f}'),
          ('Inactive cash months',str(int(a.inactive_months)),str(int(b.inactive_months)))]
    y=609
    for i,(label,av,bv) in enumerate(rows):
        if i%2==0:c.setFillColor(HexColor('#F6F8FA'));c.rect(38,y-8,519,21,fill=1,stroke=0)
        c.setFillColor(HexColor('#263548'));c.setFont(font,9.1);c.drawString(49,y,label);c.drawRightString(420,y,av);c.drawRightString(543,y,bv);y-=22
    c.setFont(strong,11);c.setFillColor(HexColor('#14283D'));c.drawString(39,325,'What the regime filter actually changed')
    mr,mu=get('momentum_regime','OOS'),get('momentum_no_regime','OOS')
    unfiltered=get('combined_no_regime','OOS')
    para(f'Pure momentum OOS total return: {escape(pct(mu.total_return))} without gate to {escape(pct(mr.total_return))} with gate. '
         f'Drawdown: {mu.max_drawdown*100:.2f}% to {mr.max_drawdown*100:.2f}%. '
         f'Combined monthly NET: {escape(pct(unfiltered.monthly_average_net_return))} without gate vs {escape(pct(b.monthly_average_net_return))} with gate. '
         f'Positive execution-stress seeds: {acceptance["positive_seed_count"]}/5; positive fixed grid cells: {acceptance["OOS_sensitivity_positive_count"]}/6.',39,311,515)
    c.setFont(strong,11);c.drawString(39,251,'Single biggest risk and the protection limits')
    para(escape(risk),39,237,515)
    c.setFont(strong,10);c.drawString(39,163,'How to interpret the numbers')
    para('Means include incubation and post-kill cash. Drawdown uses hourly closing NAV; the kill also observes opening valuations and may overshoot after gaps/costs. '
         'Seeds stress costs on one history. All 2022-2025 years were previously examined; this is retrospective validation. The default book was killed June 30, 2023 and stays cash through 2025. '
         'Separate wallets assume one-hour USDT transfers. These historical results are not forecasts.',39,151,515,size=8.5)
    para('The cited AdaptiveTrend paper reports 40.5% CAGR for different rules, not 10% monthly for this proposal. '
         '<link href="https://arxiv.org/abs/2602.11708" color="#007F86">arXiv:2602.11708</link> | '
         '<link href="https://arxiv.org/abs/2108.11921" color="#007F86">2108.11921</link> | '
         '<link href="https://arxiv.org/abs/2212.06888" color="#007F86">2212.06888</link>',39,73,515,size=7.8)
    c.setStrokeColor(HexColor('#D5DFE7'));c.line(39,28,557,28);c.setFont(font,7);c.setFillColor(HexColor('#637589'))
    c.drawString(39,16,'Frozen run '+run.name.removeprefix('run_')+'  |  Full tables, charts, source hashes and execution ledgers accompany this page.')
    c.showPage();c.save();return path

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--run',type=Path,required=True)
    a=p.parse_args();run=a.run;summary=pd.read_csv(run/'metrics.csv');monthly=pd.read_csv(run/'monthly_returns.csv')
    acceptance=json.loads((run/'acceptance.json').read_text())
    if (run/'reuse_provenance.json').exists():
        # Clarify evaluation provenance only; numeric acceptance gates are unchanged.
        acceptance['evaluation_integrity']='All 2022-2025 years previously examined; user-requested 70% breadth revision; retrospective validation; no OOS parameter selection'
        (run/'acceptance.json').write_text(json.dumps(acceptance,indent=2))
    charts(run,summary,monthly)
    def get(name,period='OOS'):return summary[(summary.case==name)&(summary.period==period)].iloc[0]
    attrs={p:attribution(a.data,run,'combined',p) for p in ['IS','OOS']}
    m=get('combined');s=series(run,'combined','OOS');end2022=s[s.index.year==2022].iloc[-1]/100000-1
    kill=m.kill_time if isinstance(m.kill_time,str) else None;killed2022=bool(kill and kill.startswith('2022'))
    verdict=(f'OOS monthly NET mean {pct(m.monthly_average_net_return)}; worst month {pct(m.worst_month_return)} ({m.worst_month}); '
             f'max DD {m.max_drawdown*100:.2f}%; 2022 return {pct(end2022)}; '+
             ('permanent kill in 2022' if killed2022 else 'no 2022 kill')+'; acceptance '+('PASS' if acceptance['passed'] else 'FAIL')+'.')
    risk=('Funding-signal churn can spend more on opening and closing both legs than the carry earns. ATR stops and the portfolio vol target reduce price exposure; '
          'they do not fix negative carry after execution costs. A 20% kill protects remaining capital by stopping the strategy, rather than creating sustainable income.')
    if 'hedge' not in attrs['OOS'].index or attrs['OOS'].loc['hedge','net_before_transfer_fees']>=0:
        risk=('An abrupt, correlated trend reversal can hit the long sleeve before the daily BTC/breadth gate changes. Fixed entry ATR stops limit ordinary per-coin losses '
              'and the lagged 20% portfolio vol target reduces exposure after volatility rises. Price gaps can overshoot stops; a permanent drawdown kill then stops further trading.')
    pdf=one_page(run,summary,acceptance,verdict,risk)
    sections=['# Regime-filtered crypto - frozen validation','',verdict,'',
        'This is a historical baseline for the requested rules, not evidence of a dependable 10% monthly income. No OOS-based parameter selection was performed. '
        'All 2022-2025 years were examined in the prior run. This user-requested 70% breadth revision is retrospective robustness validation, not a pristine unseen holdout. '
        'The default rules are unchanged and no cell is selected by its OOS performance.','',
        '## Default combined strategy: net and gross','']
    rows=[]
    for period in ['IS','OOS']:
        for name in ['combined','combined_gross','combined_without_funding']:
            x=get(name,period);rows.append([period,name,pct(x.monthly_average_net_return),pct(x.annualized_return),num(x.sharpe),num(x.sortino),num(x.calmar),
                f'{x.max_drawdown*100:.2f}%',pct(x.worst_month_return)+' '+x.worst_month,pct(x.best_month_return)+' '+x.best_month,f'{x.positive_month_fraction*100:.1f}%'])
    sections.append(table(['Period','Case','Monthly mean','CAGR','Sharpe','Sortino','Calmar','Max DD','Worst month','Best month','Positive months'],rows))
    sections+=['','Gross removes fees, slippage and transfer charges, while retaining actual funding. The no-funding counterfactual retains the same historical funding '
               'eligibility signals but removes funding cashflows. Both are complete reruns: NAV, risk scaling and kills can differ. Booked funding receipts/payments are also shown below.','',
               'Drawdown metrics sample hourly closing NAV. The kill also checks opening valuations; it can therefore trigger even if the sampled closing curve never crosses 20%. '
               'The 25% acceptance check refers to this stated sampling resolution, not a guarantee about unobserved intrahour losses.','',
               '## Regime A/B and benchmarks','']
    names=['momentum_no_regime','momentum_regime','combined_no_regime','combined','funding_only','BTC_buy_hold','USDT_cash'];rows=[]
    for period in ['IS','OOS']:
        for name in names:
            x=get(name,period);rows.append([period,LABELS[name],pct(x.monthly_average_net_return),pct(x.total_return),num(x.sharpe),f'{abs(x.max_drawdown)*100:.2f}%',x.get('kill_time','--')])
    sections.append(table(['Period','Case','Monthly mean NET','Total return','Sharpe','Max DD','Permanent kill'],rows))
    sections+=['','The pure-momentum A/B excludes the funding sleeve from BOTH sides. The combined A/B holds funding rules constant. '
               'BTC buy-and-hold uses actual Binance spot daily bars with entry/exit costs; its drawdown is daily, while strategy drawdown is hourly. '
               'USDT cash assumes a maintained peg, no yield and no trading.','',
               '## Acceptance - all outcomes shown','',table(['Check','Outcome'],[[k,str(v)] for k,v in acceptance.items()]),'',
               '## Actual sleeve and cost attribution','']
    rows=[]
    for period,f in attrs.items():
        for sleeve,x in f.iterrows():rows.append([period,sleeve,*[f'${x[k]:,.2f}' for k in ['price_or_basis_pnl','funding_cash','fees','slippage','net_before_transfer_fees']]])
    sections.append(table(['Period','Sleeve','Price / basis PnL','Actual funding cash','Fees','Slippage','Net before transfers'],rows))
    sections+=['','Transfers cost $'+f'{m.transfer_fees:,.2f}'+' in OOS. Funding uses actual historical settlements and hourly historical opening marks, '
               'a disclosed approximation to the instantaneous settlement mark. Funding schedule changes (1/2/4/8 hours) are retained; only the signal is normalized to eight hours. '
               'Historical Bybit spot prices are used for the hedge basis, with no Binance spot substitution.','',
               'At the eligibility threshold of 0.005% per eight hours, three daily settlements yield 0.015% per day of matched notional. '
               'Two legs, opened and closed at 0.06% per leg-side, cost 0.24% of matched notional: about 16 days of threshold-level funding '
               'just to recover execution costs, before basis losses or transfers. This is an illustrative break-even calculation, not a proposed tuned holding rule.','',
               '## Fixed sensitivity grid','']
    grid=summary[summary.case.str.startswith('sensitivity_')];rows=[]
    for _,x in grid.iterrows():rows.append([x.period,x.case,pct(x.monthly_average_net_return),pct(x.total_return),f'{x.max_drawdown*100:.2f}%',x.kill_time if isinstance(x.kill_time,str) else '--'])
    sections.append(table(['Period','Configuration','Monthly mean NET','Total NET return','Max DD','Kill'],rows))
    sections+=['','## Five preregistered execution-stress seeds','']
    seeds=summary[summary.case.str.startswith('seed_')];rows=[]
    for _,x in seeds.iterrows():rows.append([x.period,x.case,pct(x.monthly_average_net_return),pct(x.total_return),f'{x.max_drawdown*100:.2f}%'])
    sections.append(table(['Period','Seed','Monthly mean NET','Total NET return','Max DD'],rows))
    sections+=['','Seeds 11/29/47/71/101 add independent uniform 0-2 bps adverse slippage per order ABOVE the mandatory 2 bps. '
               'They test execution fragility on one history; five favorable seeds are not five independent market validations.','',
               '## 2022 bear and permanent stop','']
    unfiltered=monthly[(monthly.case=='momentum_no_regime')&(monthly.period=='OOS')&monthly.month.str.startswith('2022')]
    worst=unfiltered.loc[unfiltered['return'].idxmin()];rows=[]
    for name in ['momentum_no_regime','momentum_regime','combined_no_regime','combined']:
        curve=series(run,name,'OOS');r2022=curve[curve.index.year==2022].iloc[-1]/100000-1
        rmonth=monthly[(monthly.case==name)&(monthly.period=='OOS')&(monthly.month==worst.month)]['return'].iloc[0]
        rows.append([LABELS[name],pct(r2022),pct(rmonth),get(name).kill_time if isinstance(get(name).kill_time,str) else '--'])
    sections.append(table(['Case','2022 NET return',worst.month+' NET return','Full OOS kill date'],rows))
    sections+=['','The comparison month is the unfiltered pure-momentum strategy\'s worst month in 2022, selected only for diagnosis, not parameter choice. '
               'A killed strategy holds cash for the rest of the period. Preserving capital through a kill does not mean the strategy continued generating income.','',
               'The default book was killed in June 2023 and has no active trades during 2025. Adding 2025 to its equity series does not provide independent new trading evidence. '
               'Its negative monthly mean includes 30 subsequent cash months.','',
               '## Biggest risk','',risk,'',
               'Cross-venue basis and collateral transfers remain additional risks: spot cannot collateralize the other venue\'s short instantly. '
               'The model tracks separate wallets, 2x maximum local perpetual gross, a 5% margin reserve, daily equalization with a one-hour delay and $1 transfer charge. '
               'It does not simulate exchange default, withdrawal freezes, order-book depth, historical lot-size/minimum-notional rules or liquidation ladders. '
               'Positions use continuous quantities; this is research accounting, not a production execution adapter.','',
               '## Data coverage and implementation','']
    meta=json.loads((a.data/'metadata.json').read_text());sm=json.loads((a.data/'spot_metadata.json').read_text())
    sections += [f'{len(meta)} historical perpetual tickers were discovered, including terminated/relisted episodes. The point-in-time universe selected {len(sm)} '
                 f'contracts across 2019-2025; {sum(bool(v.get("days")) for v in sm.values())} have matched Bybit spot history. '
                 'Unsupported pairs and ambiguous reused tickers are excluded from the funding sleeve and listed in spot_metadata.json. '
                 'This is venue-availability filtering, not selection by future returns; nevertheless incomplete historical venue coverage is a limitation.','',
                 'The first full 50-contract aged universe is 2021-03-14. Earlier IS dates remain cash because this exact universe could not yet be formed. '
                 'Bybit spot starts in July 2021; there is no fictitious earlier funding sleeve. Thus the effective IS trading sample is substantially shorter than three years.','',
                 'All features use completed prior UTC days. ATR is Wilder14, fixed 2 ATR below executed entry, never loosened by additions. '
                 'Rank deadbands suppress discretionary changes; eligibility exits, regime exits, volatility reductions and hard caps override them. '
                 'Signals/features and asset accounting are vectorized; an hourly state loop enforces path-dependent stops, funding and permanent kills. '
                 'Disk-backed float64 price arrays preserve price precision while limiting memory use.','',
                 'Stops fill at actual gap opens or crossed levels, plus adverse slippage. Confirmed quiet hours prohibit fills. Missing funding or unobserved API '
                 'prices while held fail the run. Last-observed-price terminal liquidation proxies are counted per case. Intrahour portfolio peaks and exact tick-level '
                 'order priority are not reconstructed; limits are evaluated at hourly observations. All books start at 100,000 USDT and close at boundaries with costs.','',
                 'Daily Sharpe uses 365 days and zero risk-free; Sortino uses RMS negative daily returns; Calmar uses CAGR / hourly maximum drawdown. '
                 'Monthly means include every calendar month, including incubation and post-kill cash.','',
                 '## What the cited papers support','',
                 '[AdaptiveTrend, arXiv:2602.11708](https://arxiv.org/html/2602.11708v1) reports 40.5% annualized return, equivalent to about 2.9% compounded monthly. '
                 'Its six-hour 70/30 long/short trend system and monthly adaptive portfolio differ from these daily long-only regime and funding rules.','',
                 '[A Time-Varying Network for Cryptocurrencies, arXiv:2108.11921](https://arxiv.org/abs/2108.11921) reports an inter-crypto network momentum result, '
                 'not a validation of this 63-day perpetual ranking after these costs.','',
                 '[Fundamentals of Perpetual Futures, arXiv:2212.06888](https://arxiv.org/abs/2212.06888) studies perpetual pricing and arbitrage with frictions. '
                 'It does not guarantee a fixed monthly yield from this funding-threshold rule.','',
                 'Public exchange sources: [Binance archive specification](https://github.com/binance/binance-public-data), '
                 '[Binance historical funding API](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History), '
                 '[Bybit historical candles](https://bybit-exchange.github.io/docs/v5/market/kline), '
                 '[Bybit spot trade archives](https://public.bybit.com/spot/).','',
                 '## Artifacts','',
                 '- `pdf/Regime-Strategy-Summary.pdf`: one-page outcome, risk and limitations.\n'
                 '- `charts/`: IS/OOS equity, pure-momentum A/B, drawdown, monthly table, cost/funding comparison and sensitivity.\n'
                 '- `metrics.csv` / `monthly_returns.csv`: every fixed case and seed, both periods.\n'
                 '- Each case folder: hourly equity/venue balances, complete filled-order ledger, closed holdings, daily/monthly returns and metrics.\n'
                 '- `attribution_combined_IS.csv` / `attribution_combined_OOS.csv`: independently reconciled sleeve economics.\n'
                 '- `pin.json`, `acceptance.json`, `verification.json`: frozen sources/inputs and checks.','']
    (run/'REPORT.md').write_text('\n'.join(sections),encoding='utf-8');(run/'verdict.txt').write_text(verdict+'\n')
    print(verdict);print('Report:',run/'REPORT.md');print('One page:',pdf)

if __name__=='__main__':main()
