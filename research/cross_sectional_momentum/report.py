"""Export metrics, funding attribution, charts and a one-page research summary."""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from pypdf import PdfReader

HERE=Path(__file__).resolve().parent
NAMES={'long_only':'Cross-sectional long-only','long_short':'Cross-sectional long-short',
       'buy_hold':'Equal-weight buy & hold','tsm':'63-day time-series momentum'}
COLORS={'long_only':'#008577','long_short':'#7451ad','buy_hold':'#687688','tsm':'#c7771a'}
SHORT={'long_only':'CS long-only','long_short':'CS long-short','buy_hold':'Buy & hold','tsm':'TS momentum'}


def pct(x):
    return f'{float(x):.2%}'


def number(x):
    return 'n/a' if pd.isna(x) else f'{x:.2f}'


def load_series(path,case,kind):
    frame=pd.read_csv(path/(case+'.'+kind+'.csv'),index_col=0)
    return pd.Series(frame.iloc[:,0].to_numpy(),index=pd.to_datetime(frame.index,utc=True))


def save_figure(fig,folder,name):
    fig.savefig(folder/(name+'.png'),dpi=180,bbox_inches='tight',facecolor='white')
    fig.savefig(folder/(name+'.pdf'),bbox_inches='tight',facecolor='white')
    plt.close(fig)


def bootstrap_mean(values):
    """95% circular six-month moving-block interval; 5000 fixed-seed replicates."""
    rng=np.random.default_rng(20261007)
    values=np.asarray(values)
    starts=rng.integers(0,len(values),size=(5000,int(np.ceil(len(values)/6))))
    indices=(starts[:,:,None]+np.arange(6))%len(values)
    draws=values[indices.reshape(5000,-1)[:,:len(values)]].mean(axis=1)
    return [float(v) for v in np.quantile(draws,[.025,.975])]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=HERE/'output')
    args=parser.parse_args()
    output=args.output
    path=output/(output/'LATEST_RUN.txt').read_text().strip()
    metrics=pd.read_csv(path/'metrics.csv')
    main_rows=metrics[(metrics.lookback==63)&(metrics.universe_size==50)].copy()
    figures=output/'plots'
    figures.mkdir(exist_ok=True)
    pdfs=output/'pdf'
    pdfs.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,
                         'axes.spines.right':False,'axes.grid':True,'grid.alpha':.2})
    monthly=[]
    funding=[]
    estimates={}
    for stage in ('IS','OOS'):
        month_frame={}
        for mode in NAMES:
            for flag in (True,False):
                row=main_rows[(main_rows.stage==stage)&(main_rows['mode']==mode)&(main_rows.funding==flag)].iloc[0]
                series=load_series(path,row.case,'monthly')
                month_frame[mode+('_funding' if flag else '_no_funding')]=series
            yes=main_rows[(main_rows.stage==stage)&(main_rows['mode']==mode)&main_rows.funding].iloc[0]
            no=main_rows[(main_rows.stage==stage)&(main_rows['mode']==mode)&~main_rows.funding].iloc[0]
            funding.append({'stage':stage,'mode':mode,'net_funding_cash_cost':yes.funding_net_cost,
                            'funding_cost_percent_initial_equity':yes.funding_net_cost/100000,
                            'monthly_return_drag':no.monthly_average_net_return-yes.monthly_average_net_return,
                            'annualized_return_drag':no.annualized_return-yes.annualized_return,
                            'terminal_wealth_difference':no.end_equity-yes.end_equity})
            if stage=='OOS' and mode in ('long_only','long_short'):
                estimates[mode]={'monthly_mean':float(yes.monthly_average_net_return),
                                 'block_bootstrap_95_interval':bootstrap_mean(month_frame[mode+'_funding'])}
        table=pd.DataFrame(month_frame)
        table.insert(0,'stage',stage)
        monthly.append(table)
    monthly_table=pd.concat(monthly)
    monthly_table.to_csv(output/'monthly_returns.csv',index_label='month')
    pd.DataFrame(funding).to_csv(output/'funding_impact.csv',index=False)
    main_rows.to_csv(output/'main_metrics.csv',index=False)
    (output/'monthly_estimates.json').write_text(json.dumps(estimates,indent=2))

    fig,axes=plt.subplots(1,2,figsize=(15,5.4),layout='constrained')
    for ax,stage in zip(axes,('IS','OOS')):
        for mode in NAMES:
            row=main_rows[(main_rows.stage==stage)&(main_rows['mode']==mode)&main_rows.funding].iloc[0]
            curve=load_series(path,row.case,'daily')/1000
            ax.plot(curve.index,curve,label=SHORT[mode]+' + funding',color=COLORS[mode],linewidth=1.7)
            if mode in ('long_only','long_short'):
                no=main_rows[(main_rows.stage==stage)&(main_rows['mode']==mode)&~main_rows.funding].iloc[0]
                other=load_series(path,no.case,'daily')/1000
                ax.plot(other.index,other,label=SHORT[mode]+' without funding',color=COLORS[mode],linestyle='--',linewidth=1,alpha=.6)
        ax.set_title(('In-sample 2019-2021' if stage=='IS' else 'Out-of-sample 2022-2024')+' | L63 / top 50')
        ax.set_ylabel('Equity index (initial = 100; log scale)')
        ax.set_yscale('log')
        ax.tick_params(axis='x',rotation=25)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='outside lower center',ncol=3,fontsize=9,frameon=False)
    fig.suptitle('Crypto perpetual momentum - all curves include fees and slippage',fontsize=15)
    save_figure(fig,figures,'equity_curves')

    fig,axes=plt.subplots(1,2,figsize=(15,4.8),layout='constrained')
    for ax,stage in zip(axes,('IS','OOS')):
        for mode in NAMES:
            row=main_rows[(main_rows.stage==stage)&(main_rows['mode']==mode)&main_rows.funding].iloc[0]
            frame=pd.read_csv(path/(row.case+'.hourly.csv.gz'))
            frame.index=pd.to_datetime(frame.timestamp,utc=True)
            values=frame.equity
            peaks=np.maximum.accumulate(np.r_[100000,values.to_numpy()])[1:]
            draw=pd.Series(values.to_numpy()/peaks-1,index=frame.index)
            display=draw.resample('D').min()
            ax.plot(display.index,display,color=COLORS[mode],label=SHORT[mode],linewidth=1.5)
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.set_title(stage+' - hourly drawdown, daily worst observations')
        ax.set_ylabel('Drawdown from running peak')
        ax.tick_params(axis='x',rotation=25)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='outside lower center',ncol=4,fontsize=9,frameon=False)
    fig.suptitle('Drawdown with actual historical funding',fontsize=15)
    save_figure(fig,figures,'drawdowns')

    rows=metrics[(metrics.stage=='OOS')&metrics.funding&metrics['mode'].isin(['long_only','long_short'])]
    maxabs=max(abs(rows.total_net_return).max(),.01)
    fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
    for ax,mode in zip(axes,('long_only','long_short')):
        table=rows[rows['mode']==mode].pivot(index='lookback',columns='universe_size',values='total_net_return')
        image=ax.imshow(table.to_numpy(),cmap='RdYlGn',vmin=-maxabs,vmax=maxabs,aspect='auto')
        for i in range(len(table)):
            for j in range(len(table.columns)):
                ax.text(j,i,pct(table.iloc[i,j]),ha='center',va='center',fontsize=12,
                        color='white' if abs(table.iloc[i,j])>.65*maxabs else '#17252b')
        positive=int((table>0).sum().sum())
        ax.set_title(SHORT[mode]+f' | positive {positive}/9')
        ax.set_xticks(range(len(table.columns)),table.columns)
        ax.set_yticks(range(len(table.index)),table.index)
        ax.set_xlabel('Top-volume universe cutoff')
        ax.set_ylabel('Trailing return lookback (days)')
        ax.grid(False)
    fig.colorbar(image,ax=axes,format=PercentFormatter(1),label='2022-2024 cumulative net return')
    fig.suptitle('Sensitivity - fees, slippage and funding included',fontsize=14)
    save_figure(fig,figures,'sensitivity')

    fig,axes=plt.subplots(2,1,figsize=(14,6.8),layout='constrained')
    maximum=max(.03,monthly_table[['long_only_funding','long_short_funding']].abs().max().max())
    for ax,mode in zip(axes,('long_only','long_short')):
        series=monthly_table[mode+'_funding']
        table=pd.DataFrame({'year':series.index.year,'month':series.index.month,'return':series.to_numpy()}).pivot(
            index='year',columns='month',values='return')
        image=ax.imshow(table.to_numpy(),cmap='RdYlGn',vmin=-maximum,vmax=maximum,aspect='auto')
        for i in range(6):
            for j in range(12):
                ax.text(j,i,f'{table.iloc[i,j]*100:.1f}%',ha='center',va='center',fontsize=8,
                        color='white' if abs(table.iloc[i,j])>.65*maximum else '#17252b')
        ax.set_xticks(range(12),['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])
        ax.set_yticks(range(6),table.index)
        ax.set_title(SHORT[mode]+' | L63 / top 50 / actual funding')
        ax.grid(False)
        ax.axhline(2.5,color='black',linewidth=1.6)
    fig.suptitle('Monthly net returns - cash until February 2021; IS/OOS books start separately',fontsize=14)
    save_figure(fig,figures,'monthly_returns')

    robustness=json.loads((path/'robustness.json').read_text())
    oos=main_rows[(main_rows.stage=='OOS')&main_rows.funding].set_index('mode')
    verdict='OOS robustness: '+ '; '.join(
        f"{SHORT[r['mode']]} {r['positive_configurations']}/9 positive ({'PASS' if r['passed_5_of_9'] else 'FAIL'})"
        for r in robustness if r['stage']=='OOS')+'. The required threshold is 5/9.'
    lines=['# Crypto perpetual momentum - results','',
           'Default: 63-day trailing return, top-50 past-volume universe, weekly Monday 00:00 UTC allocation. '
           'All percentages below include mandatory fees, adverse slippage and actual funding unless labelled otherwise. '
           'In-sample and out-of-sample each start with separate 100,000 USDT books.','',
           '**'+verdict+'**','',
           '## Main comparison','',
           '| Period | Variant | Funding | Monthly mean | CAGR | Sharpe | Sortino | Max DD | Calmar | Win rate | Hold days |',
           '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in main_rows.itertuples(index=False):
        lines.append(f'| {r.stage} | {SHORT[r.mode]} | {"actual" if r.funding else "excluded"} | '
              f'{pct(r.monthly_average_net_return)} | {pct(r.annualized_return)} | {number(r.sharpe)} | '
              f'{number(r.sortino)} | {pct(r.max_drawdown)} | {number(r.calmar)} | {pct(r.win_rate)} | {number(r.average_holding_days)} |')
    lines+=['','## Funding impact','',
            'Positive funding cost means a net debit; negative means a net credit. Positive return drag means funding reduced '
            'returns. Paired wallets compound independently, so return drag includes later sizing effects.','',
            '| Period | Variant | Net funding paid (USDT) | Monthly drag, pp | CAGR drag, pp |',
            '|---|---|---:|---:|---:|']
    for row in funding:
        lines.append(f"| {row['stage']} | {SHORT[row['mode']]} | {row['net_funding_cash_cost']:,.2f} | "
                     f"{row['monthly_return_drag']*100:.3f} | {row['annualized_return_drag']*100:.3f} |")
    lines+=['','## Nine-case sensitivity','',
            'Each mode has nine configurations: lookback 30/63/126 days by universe cutoff 40/50/60. '
            'The user’s criterion is at least five positive OOS cumulative returns after all costs and funding. '
            'The default case is retained regardless of which cells performed best.','',
            '| Period | Mode | Positive cases | Criterion |','|---|---|---:|---|']
    for row in robustness:
        lines.append(f"| {row['stage']} | {SHORT[row['mode']]} | {row['positive_configurations']}/{row['tested']} | "
                     f"{'PASS' if row['passed_5_of_9'] else 'FAIL'} |")
    for mode in ('long_only','long_short'):
        table=rows[rows['mode']==mode].pivot(index='lookback',columns='universe_size',values='total_net_return')
        lines+=['',f'### {SHORT[mode]}: OOS cumulative net return','', '| Lookback | Top 40 | Top 50 | Top 60 |','|---|---:|---:|---:|']
        for lookback,values in table.iterrows():
            lines.append(f'| {lookback} | '+ ' | '.join(pct(x) for x in values)+' |')
    lines+=['','## Monthly estimate and the largest risk','',
            'The OOS monthly mean is an empirical estimate, not a future return forecast. The confidence intervals below '
            'come from 5,000 circular six-month moving-block bootstrap replicates of the 36 monthly observations. '
            'Three years cannot represent every future market regime.','']
    for mode,estimate in estimates.items():
        ci=estimate['block_bootstrap_95_interval']
        row=oos.loc[mode]
        lines.append(f"- {SHORT[mode]}: monthly mean **{pct(estimate['monthly_mean'])}**, block-bootstrap interval "
                     f"{pct(ci[0])} to {pct(ci[1])}. Worst month: **{pct(row.worst_month_return)}** in {row.worst_month[:7]}.")
    lines+=['','The single biggest strategy risk is a correlated momentum reversal, including violent squeezes in the short basket. '
            'Lagged volatility sizing, the 5% cap, paid risk reductions and ratcheting 2-ATR stops reduce exposure to that event. '
            'A jump can cross the stop before it can execute, so the stop does not guarantee the planned loss.','',
            '## Data and implementation limits','',
            '- Cash before 22 February 2021 is included in IS statistics; fewer than 40 aged contracts existed in this dataset. '
            'The available universe expands from 40 up to the requested cutoff. The 2019 daily warm-up comes from REST.',
            '- The 40% asset-volatility target is constrained by the 5% weight ceiling. It is not a 40% portfolio-volatility target. '
            'Position caps are observed hourly; continuous intrahour compliance is not observable from this data.',
            '- Terminal settlements use the last real hourly price as a proxy. Dummy zero-volume post-delisting rows are removed. '
            'Settlement TWAP, exchange maintenance-margin tiers, ADL and order-book depth are not fully reconstructed.',
            '- Daily and hourly archive holes are restored from actual REST history or daily exchange archives. '
            'Verified quiet hours prohibit execution. Contract relistings reset age eligibility and close prior inventory. '
            'This prevents a ticker reuse or token split from becoming a fictitious momentum return.',
            '- Funding rates and event intervals are historical observations. Opening hourly mark bars are the settlement-notional '
            'price proxy; rates and marks while held cannot silently be missing.',
            '- Buy-and-hold has 1x initial exposure and drifting weights. Time-series momentum can reach 2x gross. '
            'Compare volatility and exposure alongside raw returns.','',
            '| OOS variant | Mean gross exposure | Realized annual volatility | Terminal-price proxy exits |',
            '|---|---:|---:|---:|']
    for mode,row in oos.iterrows():
        lines.append(f'| {SHORT[mode]} | {pct(row.average_gross_exposure)} | {pct(row.realized_annual_vol)} | {int(row.terminal_settlement_proxies)} |')
    lines+=['','## Files and sources','',
            '- `main_metrics.csv`: all requested default metrics with/without funding.',
            '- `monthly_returns.csv`: all 72 calendar months, both strategies and benchmarks with/without funding.',
            '- `funding_impact.csv`: direct signed payments and paired return drag.',
            '- `plots/`: equity, drawdown, monthly-return and sensitivity charts, PNG and vector PDF.',
            '- `pdf/momentum-summary.pdf`: one-page research summary.',
            f'- `{path.name}/`: complete metrics, closed trades, daily curves, main hourly equity/orders and historical universes.',
            '- `protocol.json`, `data_quality.json`, `universe_coverage.csv`, and the raw-cache manifests: reproducibility/audit trail.',
            '', '[Binance archive documentation](https://github.com/binance/binance-public-data) and '
            '[Binance funding/market-data API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).']
    lines+=['','Contract lifecycle sources: '
            '[TLM/ICP 2022 settlements](https://www.binance.com/en/support/announcement/detail/af469aeeab074738bb4a276070a9d11b) and '
            '[BNX old-contract settlement and redenomination](https://www.binance.com/en/support/announcement/detail/4d23ada51a2e4fa182835c77d51ba1a9). '
            'Cached exchange responses and SHA-256 manifests document every restored file.']
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

    summary=['# Crypto perpetual momentum - one-page summary','',
             'OOS: January 2022 through December 2024. Default: 63-day return / top-50 historical-volume universe. '
             'All headline results include 0.04% taker fees, 0.02% slippage per side and actual historical funding.','',verdict,'']
    for mode in ('long_only','long_short'):
        row=oos.loc[mode]
        robust=next(r for r in robustness if r['stage']=='OOS' and r['mode']==mode)
        summary.append(f"- **{SHORT[mode]}:** monthly net mean {pct(row.monthly_average_net_return)}; CAGR {pct(row.annualized_return)}; "
                       f"Sharpe {number(row.sharpe)}; max drawdown {pct(row.max_drawdown)}. Worst month "
                       f"{pct(row.worst_month_return)} ({row.worst_month[:7]}). Positive sensitivity cases: "
                       f"{robust['positive_configurations']}/9; {'passes' if robust['passed_5_of_9'] else 'fails'} the 5/9 criterion.")
    summary+=['','**Expected monthly return:** the observed OOS mean above is the empirical estimate. '
              'A future monthly outcome is not guaranteed. Six-month block-bootstrap 95% intervals: '+
              '; '.join(f"{SHORT[m]} {pct(e['block_bootstrap_95_interval'][0])} to {pct(e['block_bootstrap_95_interval'][1])}"
                        for m,e in estimates.items())+'.','',
              '**Single biggest risk:** correlated momentum reversals and short squeezes. Volatility scaling with a 5% position '
              'cap, paid exposure reductions and ratcheting 2-ATR stops mitigate this risk. Gaps can bypass stop prices.','',
              '**Limits:** the 5% cap usually dominates the 40% asset-vol target. IS includes cash before February 2021. '
              'Post-delisting dummy candles are excluded; terminal settlement prices and funding-notional marks use disclosed '
             'historical bar proxies. Monthly means are arithmetic; CAGR reflects compounding. '
             'See report.md for all metrics, funding drag, benchmarks and execution assumptions.']
    (output/'summary.md').write_text('\n'.join(summary)+'\n',encoding='utf-8')
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='TitleCustom',fontName='Helvetica-Bold',fontSize=21,leading=25,textColor=colors.HexColor('#124744'),spaceAfter=12))
    styles.add(ParagraphStyle(name='HeadingCustom',fontName='Helvetica-Bold',fontSize=11,leading=14,textColor=colors.HexColor('#124744'),spaceBefore=11,spaceAfter=5))
    styles.add(ParagraphStyle(name='BodyCustom',fontName='Helvetica',fontSize=9.5,leading=13,spaceAfter=7))
    story=[Paragraph('Crypto perpetual momentum',styles['TitleCustom']),
           Paragraph('Research summary | 2022-2024 out-of-sample | 63-day signal / historical top 50',styles['BodyCustom']),
           Paragraph('Net performance includes 4 bp taker fees and 2 bp adverse slippage per side, plus actual historical funding.',styles['BodyCustom']),
           Paragraph(verdict,styles['BodyCustom'])]
    table=[['OOS variant','Monthly mean','CAGR','Sharpe','Max drawdown']]
    for mode in NAMES:
        r=oos.loc[mode]
        table.append([SHORT[mode],pct(r.monthly_average_net_return),pct(r.annualized_return),number(r.sharpe),pct(r.max_drawdown)])
    t=Table(table,colWidths=[130,84,72,62,100],rowHeights=[26]*5)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#124744')),
             ('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
             ('FONTSIZE',(0,0),(-1,-1),9),('ALIGN',(1,0),(-1,-1),'RIGHT'),
             ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#edf5f4'),colors.white]),
             ('BOTTOMPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7)]))
    story+=[Spacer(1,8),t]
    for title,text in [('Monthly estimate',summary[-5]),('Worst months and robustness',
        ' '.join(f"{SHORT[m]}: worst month {pct(oos.loc[m].worst_month_return)} ({oos.loc[m].worst_month[:7]}); "
                 f"{next(r for r in robustness if r['stage']=='OOS' and r['mode']==m)['positive_configurations']}/9 configurations positive after funding."
                 for m in ('long_only','long_short'))),('Largest risk',summary[-3]),('Interpretation limits',summary[-1])]:
        # Plain ASCII and supported PDF glyphs; Markdown emphasis is removed.
        plain=text.replace('**','').replace('&','&amp;')
        story+=[Paragraph(title,styles['HeadingCustom']),Paragraph(plain,styles['BodyCustom'])]
    story+=[Spacer(1,8),Paragraph('Sources: Binance Public Data archives and USD-M funding history. '
                 'Full metrics, funding attribution, monthly table, code and data manifests accompany this summary.',styles['BodyCustom'])]
    pdf=pdfs/'momentum-summary.pdf'
    SimpleDocTemplate(str(pdf),pagesize=A4,leftMargin=40,rightMargin=40,topMargin=38,bottomMargin=38).build(story)
    reader=PdfReader(pdf)
    if len(reader.pages)!=1:
        raise AssertionError('Summary must fit one page')
    print(json.dumps({'report':str(output/'report.md'),'summary_pdf':str(pdf),'pages':len(reader.pages),
                      'oos_main':oos.loc[['long_only','long_short']].to_dict('index'),'robustness':robustness},default=str))


if __name__=='__main__':
    main()
