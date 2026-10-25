const {openBrowser}=require('../../videos/paperlab-motion/node_modules/@remotion/renderer');
(async()=>{const b=await openBrowser('chrome',{logLevel:'error'});try{
 const p=await b.newPage({context:()=>null,logLevel:'error',indent:false,pageIndex:0,onBrowserLog:null,onLog:()=>{}});
 await p.evaluateOnNewDocument(()=>{window.__errors=[];addEventListener('error',e=>__errors.push(e.message));addEventListener('unhandledrejection',e=>__errors.push(String(e.reason)))});
 await p.goto({url:'https://paperlab-production-919c.up.railway.app/public/competition/analyzer',timeout:60000});
 for(let i=0;i<120;i++){if(await p.evaluate(()=>Stream.status==='LIVE'&&document.querySelector('#nav [data-nav=programs]')))break;await new Promise(r=>setTimeout(r,150))}
 const r=await p.evaluate(()=>({path:location.pathname,errors:__errors,stream:Stream.status,removed:!document.querySelector('[data-nav=analyzer]')&&!document.querySelector('[data-panel=analyzer]')&&typeof AnalyzerView==='undefined'}));
 if(r.path!='/public/competition'||r.errors.length||!r.removed||r.stream!=='LIVE')throw Error(JSON.stringify(r));
 console.log(JSON.stringify(r));
 await p.evaluate(()=>document.querySelector('#nav [data-nav=programs]').click());
 for(let i=0;i<120;i++){if(await p.evaluate(()=>!!document.querySelector('.prog-card')))break;await new Promise(r=>setTimeout(r,150))}
 const programs=await p.evaluate(()=>({cards:document.querySelectorAll('.prog-card').length,errors:__errors}));
 if(!programs.cards||programs.errors.length)throw Error(JSON.stringify(programs));console.log(JSON.stringify({programs}));
}finally{await b.close({silent:true})}})().catch(e=>{console.error(e.message);process.exit(1)});
