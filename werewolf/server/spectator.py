"""Single-page spectator UI for the projector. Fed by /events SSE; omniscient (roles shown)."""

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Werewolf Night</title>
<style>
:root{--bg:#0b0e14;--panel:#11161f;--line:#2a313c;--fg:#e8e8e8;--dim:#8b949e;
 --wolf:#ff5c57;--seer:#b48cff;--doctor:#4fd18b;--villager:#7aa2ff;--gold:#f2c14e;--night:#1a2244}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.35 -apple-system,system-ui,sans-serif;
 height:100vh;display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);grid-template-rows:auto minmax(0,1fr);overflow:hidden;transition:background .8s}
body.night{background:#070a18}
header{grid-column:1/3;display:flex;align-items:center;gap:24px;padding:14px 24px;border-bottom:1px solid var(--line)}
#phase{font-size:34px;font-weight:800;letter-spacing:.02em}
#clock{flex:1;height:10px;background:#1c2230;border-radius:5px;overflow:hidden}
#clockbar{height:100%;width:0;background:var(--gold);transition:width 1s linear}
#secs{font:700 26px ui-monospace,monospace;min-width:70px;text-align:right}
#tally{font-size:20px;color:var(--dim)}
#tally b{color:var(--fg)}
#left{display:flex;flex-direction:column;align-items:center;justify-content:center;position:relative;min-height:0}
#circle{position:relative;width:min(56vw,80vh);height:min(56vw,80vh)}
#arrows{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.seat{position:absolute;width:150px;transform:translate(-50%,-50%);text-align:center;background:var(--panel);
 border:3px solid var(--line);border-radius:14px;padding:8px 6px;transition:all .4s}
.seat .num{font-size:22px;font-weight:800}
.seat .team{font-size:14px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.seat .role{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.seat .bar{height:6px;background:#1c2230;border-radius:3px;margin-top:6px;overflow:hidden}
.seat .bar i{display:block;height:100%;background:var(--villager)}
.seat .usd{font:12px ui-monospace,monospace;color:var(--dim)}
.seat.werewolf{border-color:var(--wolf)} .seat.werewolf .role{color:var(--wolf)}
.seat.seer .role{color:var(--seer)} .seat.doctor .role{color:var(--doctor)} .seat.villager .role{color:var(--villager)}
.seat.pending{box-shadow:0 0 0 3px var(--gold) inset}
.seat.thinking{animation:pulse 1s infinite}
@keyframes pulse{50%{box-shadow:0 0 18px 4px var(--gold)}}
.seat.dead{opacity:.3;filter:grayscale(1)} .seat.dead .num::after{content:" ✝"}
.seat.broke{background:#1b1a12} .seat.broke .bar i{background:var(--wolf)} .seat.broke .usd::after{content:" BROKE";color:var(--wolf);font-weight:700}
.seat.flash{transform:translate(-50%,-50%) scale(1.15)}
#right{display:grid;grid-template-rows:minmax(0,1fr) minmax(0,.45fr) auto;border-left:1px solid var(--line);min-height:0}
section{display:flex;flex-direction:column;min-height:0;border-bottom:1px solid var(--line)}
section h2{margin:0;padding:8px 16px;font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim)}
.feed{flex:1;overflow-y:auto;padding:4px 14px 12px}
.msg{margin:6px 0;padding:8px 12px;border-radius:10px;background:var(--panel);animation:in .35s}
@keyframes in{from{opacity:0;transform:translateY(8px)}}
.msg .who{font-weight:800;margin-right:6px}
.msg .tag{font-size:12px;padding:1px 6px;border-radius:6px;margin-left:6px;background:#222a36;color:var(--dim)}
.msg.sys{background:none;color:var(--dim);font-size:15px;padding:2px 12px}
.msg.death{background:none;color:var(--wolf);font-weight:700}
.msg.vote{background:none;font-size:15px}
#wolf .msg{background:#2a1214}
#spend{padding:8px 16px 12px}
#spend .row{display:flex;align-items:center;gap:10px;font-size:15px;margin:3px 0}
#spend .row span:first-child{width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#spend .row .b{flex:1;height:8px;background:#1c2230;border-radius:4px;overflow:hidden}
#spend .row .b i{display:block;height:100%;background:var(--gold)}
#banner{position:fixed;inset:0;display:none;align-items:center;justify-content:center;flex-direction:column;
 background:rgba(5,7,12,.86);z-index:5;text-align:center}
#banner h1{font-size:96px;margin:0} #banner p{font-size:26px;color:var(--dim)}
#banner.show{display:flex}
</style></head><body>
<header><div id="phase">connecting…</div><div id="clock"><div id="clockbar"></div></div><div id="secs"></div>
<div id="tally"></div></header>
<div id="left"><div id="circle"><svg id="arrows"></svg></div></div>
<div id="right">
 <section><h2>Message board</h2><div class="feed" id="board"></div></section>
 <section id="wolfsec"><h2>Wolf den (audience only)</h2><div class="feed" id="wolf"></div></section>
 <section><h2>Spend by team</h2><div id="spend"></div></section>
</div>
<div id="banner"><h1 id="bannerh"></h1><p id="bannerp"></p></div>
<script>
const op=new URLSearchParams(location.search).get('op')||'';
const $=id=>document.getElementById(id);
const circle=$('circle'),svg=$('arrows'),board=$('board'),wolf=$('wolf');
let N=0,budget=0.5,meta={},els={},pos={};
const ROLE_ICON={werewolf:'🐺',seer:'🔮',doctor:'🩺',villager:'🧑‍🌾'};
function esc(t){return String(t??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function name(s){const d=meta[s]||{};return 'Seat '+s+(d.team?' · '+d.team:'')}
function layout(n){
  N=n; circle.querySelectorAll('.seat').forEach(e=>e.remove()); els={};
  for(let s=0;s<n;s++){
    const a=2*Math.PI*s/n-Math.PI/2, x=50+42*Math.cos(a), y=50+42*Math.sin(a);
    pos[s]=[x,y];
    const el=document.createElement('div'); el.className='seat';
    el.style.left=x+'%'; el.style.top=y+'%'; circle.appendChild(el); els[s]=el;
  }
}
function render(s){
  const el=els[s], d=meta[s]; if(!el||!d) return;
  el.className='seat '+(d.role||'')+(d.alive===false?' dead':'')+(d.broke?' broke':'')
    +(d.pending?' pending':'')+(d.thinking?' thinking':'');
  const pct=Math.min(100,100*(d.spend||0)/budget);
  el.innerHTML='<div class="num">'+(ROLE_ICON[d.role]||'')+' '+s+'</div><div class="team">'+esc(d.team)+'</div>'
    +'<div class="role">'+esc(d.role)+'</div><div class="bar"><i style="width:'+pct+'%"></i></div>'
    +'<div class="usd">$'+(d.spend||0).toFixed(3)+'</div>';
}
function flash(s){const el=els[s]; if(!el) return; el.classList.add('flash'); setTimeout(()=>el.classList.remove('flash'),600)}
function add(feed,html,cls){
  const el=document.createElement('div'); el.className='msg '+(cls||''); el.innerHTML=html; feed.appendChild(el);
  feed.scrollTop=feed.scrollHeight; while(feed.children.length>400) feed.removeChild(feed.firstChild);
}
function who(s){const d=meta[s]||{}; return '<span class="who" style="color:var(--'+(d.role||'fg')+')">'+esc(name(s))+'</span>'}
function arrows(ballots){
  svg.innerHTML='<defs><marker id="h" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0L10,5L0,10z" fill="#f2c14e"/></marker></defs>';
  const W=circle.clientWidth,H=circle.clientHeight;
  ballots.forEach(b=>{ if(b.target==null||!pos[b.seat]||!pos[b.target]) return;
    const [x1,y1]=pos[b.seat],[x2,y2]=pos[b.target];
    const l=document.createElementNS('http://www.w3.org/2000/svg','line');
    const k=0.82; const mx=x1+(x2-x1)*k, my=y1+(y2-y1)*k;
    l.setAttribute('x1',x1*W/100);l.setAttribute('y1',y1*H/100);l.setAttribute('x2',mx*W/100);l.setAttribute('y2',my*H/100);
    l.setAttribute('stroke','#f2c14e');l.setAttribute('stroke-width','3');l.setAttribute('opacity','.75');l.setAttribute('marker-end','url(#h)');
    svg.appendChild(l);});
  setTimeout(()=>{svg.innerHTML=''},9000);
}
function tally(){
  let w=0,v=0; for(const s in meta){ if(meta[s].alive!==false){ meta[s].role==='werewolf'?w++:v++ } }
  $('tally').innerHTML='🐺 <b>'+w+'</b> &nbsp; 🏡 <b>'+v+'</b>';
}
function spendPanel(){
  const t={}; for(const s in meta){const d=meta[s]; const k=d.team||('seat '+s); t[k]=t[k]||{usd:0,n:0}; t[k].usd+=d.spend||0; t[k].n++}
  const rows=Object.entries(t).sort((a,b)=>b[1].usd-a[1].usd);
  $('spend').innerHTML=rows.map(([k,v])=>'<div class="row"><span>'+esc(k)+'</span><span class="b"><i style="width:'
    +Math.min(100,100*v.usd/(budget*v.n))+'%"></i></span><span>$'+v.usd.toFixed(3)+'</span></div>').join('');
}
function setPhase(p,day,round){
  document.body.classList.toggle('night',p==='night');
  const label={night:'🌙 Night',day_discussion:'☀️ Discussion',day_vote:'🗳️ Vote',over:'Game over'}[p]||p;
  $('phase').textContent=label+' · day '+day+(p==='day_discussion'?' · round '+round:'');
}
function handle(m){
  if(m.kind==='init'){
    budget=m.budget_usd||budget; meta={}; layout(m.seats);
    m.seat_info.forEach(d=>{meta[d.seat]=d; render(d.seat)});
    board.innerHTML=''; wolf.innerHTML=''; $('banner').classList.remove('show');
    add(board,'Game '+m.game+' · '+m.seats+' seats','sys'); tally(); spendPanel(); return;
  }
  if(m.kind==='tick'){
    const frac=m.window_s?m.remaining_s/m.window_s:0;
    $('clockbar').style.width=(100*frac)+'%'; $('secs').textContent=m.pending.length?Math.ceil(m.remaining_s)+'s':'';
    const pend=new Set(m.pending), think=new Set(m.thinking);
    m.seats.forEach(x=>{const d=meta[x.seat]=meta[x.seat]||{}; d.spend=x.spent_usd; d.broke=x.broke; d.alive=x.alive;
      d.pending=pend.has(x.seat); d.thinking=think.has(x.seat); render(x.seat)});
    if(m.waiting_ready.length) $('phase').textContent='Waiting for bots: '+m.waiting_ready.join(', ');
    spendPanel(); return;
  }
  if(m.kind==='broke'){const d=meta[m.seat]=meta[m.seat]||{}; d.broke=true; render(m.seat); add(board,'💸 '+esc(name(m.seat))+' is out of budget and goes mute','sys'); return}
  onEvent(m);
}
function onEvent(e){
  switch(e.kind){
    case 'phase': if(e.phase!=='over'){ setPhase(e.phase,e.day,e.round);
      add(board,{night:'🌙 Night '+e.day+' falls',day_discussion:'☀️ Day '+e.day+' · discussion round '+e.round,day_vote:'🗳️ Day '+e.day+' · vote'}[e.phase]||e.phase,'sys')} break;
    case 'posts_revealed': e.posts.forEach(p=>{ add(board, who(p.seat)+esc(p.chat)
      +(p.claimed_role?'<span class="tag">claims '+esc(p.claimed_role)+'</span>':'')
      +(p.accuse!=null?'<span class="tag">accuses '+p.accuse+'</span>':'')); flash(p.seat)}); break;
    case 'wolf_chat': add(wolf, who(e.seat)+esc(e.msg)); break;
    case 'seer_result': add(wolf,'🔮 '+esc(name(e.seer))+' checked '+esc(name(e.seat))+': <b>'+esc(e.role)+'</b>','sys'); break;
    case 'night_result': if(e.kill_target!=null) add(wolf,'🐺 Wolves target '+esc(name(e.kill_target))
      +(e.save_target!=null?' · 🩺 doctor guards '+esc(name(e.save_target)):'')+(e.killed==null?' · <b>saved!</b>':''),'sys'); break;
    case 'vote_tally': { const cast=e.ballots.filter(b=>b.target!=null);
      add(board,'🗳️ '+(cast.length?cast.map(b=>b.seat+'→'+b.target).join('  '):'no votes cast')
      +(e.eliminated!=null?' ⇒ <b>'+esc(name(e.eliminated))+'</b> out':' ⇒ no one'),'vote'); arrows(cast); break; }
    case 'death': { const d=meta[e.seat]=meta[e.seat]||{}; d.alive=false; d.role=e.role; render(e.seat); flash(e.seat); tally();
      add(board,(e.cause==='night'?'🌙 ':'⚖️ ')+esc(name(e.seat))+(e.cause==='night'?' was killed in the night':' was voted out')+' — '+esc(e.role),'death'); break; }
    case 'no_death': add(board,e.cause==='night'?'🌅 Nobody died last night':'⚖️ No one was eliminated','sys'); break;
    case 'result': { const w=e.result==='wolves'?'🐺 Wolves win':e.result==='village'?'🏡 Village wins':'🤝 Draw';
      $('phase').textContent=w; $('bannerh').textContent=w; $('secs').textContent=''; $('clockbar').style.width='0';
      $('bannerp').textContent='Wolves: '+Object.keys(meta).filter(s=>meta[s].role==='werewolf').map(name).join(', ');
      $('banner').classList.add('show'); add(board,'<b>'+w+'</b>','sys'); break; }
  }
}
let es; function connect(){
  es=new EventSource('/events?op='+encodeURIComponent(op));
  es.onmessage=ev=>{ try{ handle(JSON.parse(ev.data)) }catch(err){console.error(err)} };
  es.onerror=()=>{ $('phase').textContent='reconnecting…'; es.close(); setTimeout(connect,1500) };
}
$('banner').onclick=()=>$('banner').classList.remove('show');
connect();
</script></body></html>
"""
