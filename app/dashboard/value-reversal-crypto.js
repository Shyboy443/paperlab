let cryptoState=null,cryptoPeriod='comparison',cryptoSelected='ETHUSDT-15m-previous-10bars',cryptoReplayToken='',cryptoReplayGeneration=0,cryptoDetailGeneration=0;
const cryptoDetailCache=new Map();
const cryptoReasons={entered:'Entered at next open',already_in_position:'Existing position open',session_close:'Closing candle; no next entry',entry_outside_stop_target:'Next open outside stop / target',below_minimum_lot_or_notional:'Below lot / notional minimum',drawdown_halt:'Drawdown halt',end_of_test:'End of test'};
function renderCrypto(){
 if(!cryptoState)return;
 const r=cryptoState,selected=r.results.find(x=>x.key===cryptoSelected);
 if(!selected?.periods[cryptoPeriod].net.curve){loadCryptoDetail();return}
 const p=selected.periods[cryptoPeriod],s=p.net;
 for(const k of ['development','comparison'])$('crypto-'+k).classList.toggle('on',k===cryptoPeriod);
 const positive=r.results.filter(x=>x.periods[cryptoPeriod].net.return_pct>0).length,stressed=r.results.filter(x=>x.periods[cryptoPeriod].stress.return_pct>0).length,checks=r.source_parity?.jobs.reduce((n,j)=>n+j.level_and_signal_checks,0)||0;
 $('crypto-status').innerHTML='<strong>'+positive+' / 24 positive after costs; '+stressed+' / 24 positive under doubled costs.</strong> Default BTC verdict: '+esc(r.verdict.replaceAll('_',' '))+'. '+esc(r.reasons.join('; '))+'. '+checks.toLocaleString()+' original-Pine level/signal checks matched across both DST transitions. Nothing is promoted to live trading.';
 $('crypto-best').innerHTML=renderCryptoBest(r,cryptoPeriod);
 $('crypto-best').querySelectorAll('button[data-best-key]').forEach(b=>b.addEventListener('click',()=>{cryptoSelected=b.dataset.bestKey;$('crypto-variant').value=cryptoSelected;cryptoReplayToken='';renderCrypto()}));
 $('crypto-metrics').innerHTML=[['Monthly average',pct(s.monthly_avg_net_pct)],['Annualized',pct(s.annualized_pct)],['Sharpe',fmt(s.sharpe)],['Max drawdown',pct(s.max_dd_pct)],['Worst month',pct(s.worst_month_pct)],['Positive months',pct(s.positive_months_pct)]].map(([l,v])=>'<div class="metric"><small>'+l+'</small><b>'+v+'</b></div>').join('');
 $('crypto-counts').textContent=cryptoSelected+' · '+s.signal_count+' arrows → '+s.trades+' completed trades · '+fmt(s.average_holding_hours,1)+' hours average holding · '+pct(s.win_rate_pct)+' win rate'+(s.halted?' · halted '+time(s.halt_ts):'');
 $('crypto-equity').innerHTML=chart([{color:'#7db7e8',p:s.curve},{color:'#82cab6',p:p.without_funding.curve},{color:'#aeb7c6',p:p.gross.curve},{color:'#e6b180',p:p.stress.curve}]);
 $('crypto-dd').innerHTML=chart([{color:'#e6b180',p:s.curve}],true);
 $('crypto-grid').innerHTML=r.results.map(x=>{const a=x.periods[cryptoPeriod];return '<tr class="'+(x.key===cryptoSelected?'active':'')+'"><td><button data-crypto-key="'+esc(x.key)+'">'+esc(x.key)+(x.key===r.primary?' (default)':'')+'</button></td><td>'+pct(a.gross.return_pct)+'</td><td class="'+cls(a.net.return_pct)+'">'+pct(a.net.return_pct)+'</td><td>'+pct(a.without_funding.return_pct)+'</td><td>'+pct(a.net.monthly_avg_net_pct)+'</td><td>'+pct(a.net.max_dd_pct)+'</td><td>'+a.net.signal_count+'</td><td>'+a.net.trades+'</td><td class="'+cls(a.stress.return_pct)+'">'+pct(a.stress.return_pct)+'</td></tr>'}).join('');
 $('crypto-grid').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{cryptoSelected=b.dataset.cryptoKey;$('crypto-variant').value=cryptoSelected;cryptoReplayToken='';renderCrypto()}));
 $('crypto-audit').innerHTML='<div class="tablewrap"><table><thead><tr><th>Outcome</th><th>Arrows</th></tr></thead><tbody>'+Object.entries(s.entry_audit).map(([k,v])=>'<tr><td>'+esc(cryptoReasons[k]||k)+'</td><td>'+v+'</td></tr>').join('')+'</tbody></table></div>';
 $('crypto-costs').textContent='Fees '+money(s.fees)+' · modeled slippage '+money(s.slippage)+' · funding net paid '+money(s.funding_cost)+' ('+s.funding_events+' settlements held). Net-return impact from funding: '+pct(p.funding_return_impact_pct)+' percentage points versus the independent no-funding wallet. Fee 0.04% + slippage 0.02% per side; stress doubles both.';
 $('crypto-monthly').innerHTML=s.monthly.map(x=>'<tr><td>'+esc(x.month)+'</td><td class="'+cls(x.net_pct)+'">'+pct(x.net_pct)+'</td></tr>').join('');
 $('crypto-trades').innerHTML=s.recent_trades.slice().reverse().map(t=>'<tr><td>'+time(t.entry_ts)+'</td><td>'+(t.side>0?'Long':'Short')+'</td><td>'+esc(t.reason)+'</td><td>'+fmt(t.qty,3)+'</td><td>'+money(t.funding_cost)+'</td><td class="'+cls(t.net)+'">'+money(t.net)+'</td></tr>').join('');
 const dates=s.session_dates||[];
 const prior=$('crypto-replay-date').value;$('crypto-replay-date').innerHTML=dates.map(d=>'<option value="'+d+'">'+d+' · session starts 18:00 NY</option>').join('');if(dates.includes(prior))$('crypto-replay-date').value=prior;loadCryptoReplay();
}
async function loadCryptoReplay(){
 const date=$('crypto-replay-date').value;if(!date){$('crypto-replay').textContent='No signals in this period.';return}
 const token=cryptoSelected+'|'+date;if(token===cryptoReplayToken)return;cryptoReplayToken=token;const generation=++cryptoReplayGeneration;
 $('crypto-replay').textContent='Loading crypto candle replay…';
 try{const response=await fetch('/api/public/value-reversal/crypto/replay?'+new URLSearchParams({key:cryptoSelected,date}));const d=await response.json();if(!response.ok)throw Error(d.detail||'Crypto replay unavailable');if(generation!==cryptoReplayGeneration)return;
 await PaperLabVela.render('crypto-replay',d.candles,{symbol:d.symbol,minutes:d.minutes,profile:cryptoSelected.includes('-developing-')?'developing':'previous',digits:d.symbol==='SOLUSDT'?3:2,label:d.symbol+' perpetual · '+d.minutes+'m · '+cryptoSelected.split('-').slice(2).join('-')+' · '+date+' NY18'});
 if(generation!==cryptoReplayGeneration)return;
 $('crypto-replay-caption').textContent=d.symbol+' · '+d.minutes+'m · '+date+' from 18:00 New York · '+d.signals.length+' arrows · '+d.candles.length+' candles. Drag to pan, scroll to zoom; use Previous / Next signal. All times New York.';
 }catch(e){if(generation===cryptoReplayGeneration)$('crypto-replay').textContent=e.message;cryptoReplayToken=''}
}
function downloadCrypto(){const a=document.createElement('a');a.href='/api/public/value-reversal/crypto';a.download='crypto-value-reversal-v1-results.json';a.click()}
async function loadCrypto(){try{const response=await fetch('/api/public/value-reversal/crypto/view?'+new URLSearchParams({key:cryptoSelected}));const d=await response.json();if(!response.ok)throw Error(d.detail||'Crypto study unavailable');cryptoState=d;cryptoDetailCache.set(cryptoSelected,d.results.find(x=>x.key===cryptoSelected));$('crypto-variant').innerHTML=d.results.map(x=>'<option value="'+esc(x.key)+'">'+esc(x.key)+'</option>').join('');$('crypto-variant').value=cryptoSelected;renderCrypto()}catch(e){$('crypto-status').textContent=e.message;setTimeout(loadCrypto,30000)}}loadCrypto();

function renderCryptoBest(r,period){
 // Rank by combined net return across BOTH periods (the two-year halves). Robust configs that print
 // on 2022-2023 *and* 2024-2025 are the ones a real paper book should track. Period-only views can
 // overfit a single regime. Paper only — never a live trading promotion.
 const ranked=r.results.map(x=>{
  const a=x.periods.development.net, b=x.periods[period].net;
  return {key:x.key,total:a.return_pct+b.return_pct,dev:a.return_pct>0,visible:b.return_pct>0,
          visible_period:period,visible_ret:b.return_pct,visible_m:b.monthly_avg_net_pct,
          visible_dd:b.max_dd_pct,dev_ret:a.return_pct,dev_m:a.monthly_avg_net_pct,
          dev_dd:a.max_dd_pct,trades:b.trades};
 }).sort((a,b)=>b.total-a.total);
 // Strict: a configuration must be positive on BOTH periods. Ties broken by combined net return.
 const top=ranked.filter(x=>x.dev&&x.visible).slice(0,3);
 if(!top.length)return '<strong>No configuration is positive on both 2022-2023 and '+period+'.</strong> <span class="muted">The signal does not edge the execution model here; do not trade it.</span>';
 const rows=top.map((x,i)=>'<tr><td>'+(i+1)+'.</td><td><button data-best-key="'+esc(x.key)+'">'+esc(x.key)+'</button></td>'
   +'<td class="'+cls(x.visible_ret)+'">+'+fmt(x.visible_ret)+'% <span class="muted">on '+esc(period)+'</span></td>'
   +'<td>'+(x.visible_m>=0?'+':'')+fmt(x.visible_m,2)+'% / mo</td>'
   +'<td>'+fmt(x.visible_dd,1)+'% DD</td>'
   +'<td>'+x.trades+' trades</td></tr>').join('');
 return '<strong>'+top.length+' configuration'+(top.length===1?'':'s')+' positive on both periods (2022-2023 + '+period+')</strong>'
   +' <span class="muted">· ranked by combined net return · click to load that setting into the chart below</span>'
   +'<div class="tablewrap" style="margin-top:10px"><table><thead><tr><th>#</th><th>Setting</th><th>Net return</th><th>Monthly avg</th><th>Max DD</th><th>Trades</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
}

async function loadCryptoDetail(){
 const key=cryptoSelected,generation=++cryptoDetailGeneration;
 if(cryptoDetailCache.has(key)){const row=cryptoDetailCache.get(key);cryptoState.results=cryptoState.results.map(x=>x.key===key?row:x);renderCrypto();return}
 $('crypto-counts').textContent='Loading selected experiment…';
 try{const response=await fetch('/api/public/value-reversal/crypto/view?'+new URLSearchParams({key}));const d=await response.json();if(!response.ok)throw Error(d.detail||'Experiment unavailable');
 const row=d.results.find(x=>x.key===key);cryptoDetailCache.set(key,row);cryptoState.results=cryptoState.results.map(x=>x.key===key?row:x);
 if(generation===cryptoDetailGeneration&&cryptoSelected===key)renderCrypto();
 }catch(e){if(generation===cryptoDetailGeneration)$('crypto-counts').textContent=e.message}
}
