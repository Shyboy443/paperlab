let suppliedState=null,suppliedPeriod='comparison';
function renderSupplied(){
 if(!suppliedState)return;const v=suppliedState;
 for(const k of ['development','comparison'])$('supplied-'+k).classList.toggle('on',k===suppliedPeriod);
 $('supplied-proof').innerHTML='<strong>Your exact 517-line file was executed: '+v.level_and_signal_checks.toLocaleString()+' level/signal checks matched; '+v.wallet_evaluations+' historical wallets rerun.</strong><p>Its bytes match the source already used in the corrected studies. Exact pasted defaults: previous-session signals, 10 outside bars, 60 rows, 70% input, 18:00 New York. Five-bar variants remain separately labelled.</p><span class="muted">QQQ Pine checks cover 2024–2025; crypto Pine checks cover selected sessions across both DST changes. Full historical wallet retests use the verified identical signal caches.</span>';
 $('supplied-results').innerHTML=v.results.map(r=>{const p=r.periods[suppliedPeriod],n=p.net,verdict=v.default_verdicts[r.key];return '<tr><td><button data-supplied-key="'+esc(r.key)+'">'+esc(r.market+' · '+r.minutes+'m · previous · 10 bars')+'</button></td><td>'+n.signal_count+'</td><td>'+n.trades+'</td><td class="'+cls(n.return_pct)+'">'+pct(n.return_pct)+'</td><td>'+pct(n.monthly_avg_net_pct)+'</td><td>'+pct(n.max_dd_pct)+'</td><td class="'+cls(p.stress.return_pct)+'">'+pct(p.stress.return_pct)+'</td><td>'+esc(suppliedPeriod==='comparison'?verdict.verdict.replaceAll('_',' '):'Development period')+'</td></tr>'}).join('');
 $('supplied-results').querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{
  const key=b.dataset.suppliedKey;
  if(key.startsWith('QQQ-')){if(!state)await refresh();selected=key;period=suppliedPeriod;$('variant').value=key;replayToken='';render();$('replay-chart').scrollIntoView({block:'center'})}
  else {if(!cryptoState)await loadCrypto();if(!cryptoState)return;cryptoSelected=key;cryptoPeriod=suppliedPeriod;$('crypto-variant').value=key;cryptoReplayToken='';renderCrypto();$('crypto-replay').scrollIntoView({block:'center'})}
 }));
}
async function loadSupplied(){try{const response=await fetch('/api/public/value-reversal/source-verification');const v=await response.json();if(!response.ok)throw Error(v.detail||'Source verification unavailable');suppliedState=v;renderSupplied()}catch(e){$('supplied-proof').textContent=e.message}}loadSupplied();
