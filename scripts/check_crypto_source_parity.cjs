const fs=require('fs');const {PineTS}=require(require('path').resolve('data/qqq-source-validation/node_modules/pinets'));
(async()=>{
 const jobs=JSON.parse(fs.readFileSync('data/crypto-parity-input.json','utf8'));const summary=[];
 const sourceFile=process.env.PINE_SOURCE||'app/value_reversal/sources/value-area-reversion-official.pine';
 const original=fs.readFileSync(sourceFile,'utf8');
 for(const job of jobs.filter(j=>!process.env.DEBUG_SYMBOL||j.symbol===process.env.DEBUG_SYMBOL)){
  let code=original.replace('input.int(10, "Maximum bars','input.int('+job.window+', "Maximum bars').replaceAll('syminfo.mintick',String(job.tick));
  code+='\nplot(valueAreaLow, "CHECK_CL")\nplot(valueAreaHigh, "CHECK_CH")\nplot(pointOfControl, "CHECK_CP")\nplot(previousValueAreaLow, "CHECK_PL")\nplot(previousValueAreaHigh, "CHECK_PH")\nplot(previousPointOfControl, "CHECK_PP")\nplot(bullishReclaimSignal ? 1 : bearishReclaimSignal ? -1 : 0, "CHECK_CS")\nplot(previousBullishReclaimSignal ? 1 : previousBearishReclaimSignal ? -1 : 0, "CHECK_PS")';
  if(process.env.PARITY_DEBUG){code+='\nplot(totalProfileVolume,"DBG_TOTAL")\nplot(valueAreaTarget,"DBG_TARGET")\nplot(valueAreaAccumulated,"DBG_ACC")\nplot(maximumBinVolume,"DBG_MAX")\nplot(valueAreaHighIndex,"DBG_HI")';}
  const result=await new PineTS(job.candles,job.symbol,String(job.minutes)).run(code);
  if(process.env.PARITY_DEBUG)console.log(JSON.stringify(Object.fromEntries(['DBG_TOTAL','DBG_TARGET','DBG_ACC','DBG_MAX','DBG_HI'].map(k=>[k,result.plots[k].data[Number(process.env.DEBUG_BAR||468)].value]))));
  const mismatches=[];let signals=0;
  for(let i=0;i<job.expected.length;i++){
   const e=job.expected[i];
   for(const [kind,prefix] of [['previous','P'],['developing','C']]){
    for(const [field,suffix] of [['low','L'],['high','H'],['poc','P'],['signal','S']]){
     const a=result.plots['CHECK_'+prefix+suffix].data[i]?.value,b=e[kind][field];
     const same=b===null?(a===null||a===undefined||Number.isNaN(a)):Math.abs(a-b)<=1e-8;
     if(!same&&mismatches.length<10)mismatches.push({i,time:e.time,kind,field,pine:a,python:b});
     if(field==='signal'&&b)signals++;
    }
   }
  }
  const row={symbol:job.symbol,tick:job.tick,minutes:job.minutes,window:job.window,bars:job.candles.length,level_and_signal_checks:job.candles.length*8,signals,mismatches};
  console.log(JSON.stringify(row));summary.push(row);
  if(mismatches.length)throw Error('Published Pine source parity failed');
 }
 const output={source_file:sourceFile,source_sha256:require('crypto').createHash('sha256').update(fs.readFileSync(sourceFile)).digest('hex'),runtime:require(require('path').resolve('data/qqq-source-validation/node_modules/pinets/package.json')).version,scope:'Original published Pine on BTC/ETH/SOL 24h candles across both 2024 DST changes; asset tick and 5/10 input overrides only',jobs:summary};
 fs.writeFileSync(process.env.PARITY_OUTPUT||'data/crypto-pine-parity-verified.json',JSON.stringify(output,null,2));
 console.log('All source parity checks passed');
})().catch(e=>{console.error(String(e));process.exit(1)});
