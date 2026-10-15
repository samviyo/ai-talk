"""Single-page spectator UI, dark, for a projector. Fed by /events SSE."""

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Werewolf</title>
<style>
 body{margin:0;background:#0b0e14;color:#e6e6e6;font:16px/1.4 -apple-system,system-ui,sans-serif;display:flex;height:100vh}
 #board{flex:1.2;display:flex;flex-direction:column;align-items:center;justify-content:center}
 #phase{font-size:28px;font-weight:700;margin:8px;color:#7ee787}
 #circle{position:relative;width:520px;height:520px}
 .seat{position:absolute;width:150px;margin-left:-75px;margin-top:-42px;text-align:center;
   background:#161b22;border:2px solid #30363d;border-radius:10px;padding:8px 4px}
 .seat.dead{opacity:.35;text-decoration:line-through}
 .seat.broke{border-color:#6e5200;background:#1b1a12;color:#8a8a8a}
 .seat .role{font-size:13px;color:#9da5b3}
 .seat.wolf{border-color:#f85149}
 .seat .spend{font-size:12px;color:#58a6ff}
 #chat{flex:1;border-left:1px solid #30363d;overflow-y:auto;padding:10px;background:#0d1117}
 #chat .ev{margin:4px 0;padding:4px 6px;border-radius:6px;background:#161b22}
 #chat .death{color:#f85149}
 #chat .wolf{color:#d29922}
 #chat .phase{color:#7ee787;font-weight:600}
 #chat .broke{color:#8a8a8a}
</style></head><body>
<div id="board"><div id="phase">connecting…</div><div id="circle"></div></div>
<div id="chat"></div>
<script>
const op = new URLSearchParams(location.search).get('op') || '';
const circle = document.getElementById('circle');
const chat = document.getElementById('chat');
const phase = document.getElementById('phase');
let seats = {};
function layout(n){
  circle.innerHTML=''; seats={};
  for(let s=0;s<n;s++){
    const a = 2*Math.PI*s/n - Math.PI/2;
    const el = document.createElement('div');
    el.className='seat'; el.id='seat'+s;
    el.style.left = (260+240*Math.cos(a))+'px';
    el.style.top = (260+240*Math.sin(a))+'px';
    circle.appendChild(el);
    seats[s]=el; render(s,{});
  }
}
function render(s,d){
  const el = seats[s]; if(!el) return;
  el.className = 'seat'+(d.alive===false?' dead':'')+(d.broke?' broke':'')+(d.role==='werewolf'?' wolf':'');
  el.innerHTML = '<b>Seat '+s+'</b>'+(d.team?(' · '+esc(d.team)):'')
    +'<div class="role">'+(d.role||'?')+(d.broke?' · BROKE':'')+'</div>'
    +'<div class="spend">$'+(d.spend||0).toFixed(3)+'</div>';
}
function esc(t){return String(t).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function add(text, cls){
  const el=document.createElement('div'); el.className='ev '+(cls||''); el.textContent=text;
  chat.appendChild(el); chat.scrollTop=chat.scrollHeight;
  while(chat.children.length>500) chat.removeChild(chat.firstChild);
}
const meta = {};
function handle(m){
  if(m.kind==='init'){
    layout(m.seats);
    m.seat_info.forEach(d=>{meta[d.seat]=d; render(d.seat,d)});
    add('game '+m.game+' started','phase');
    return;
  }
  if(m.kind==='spend'){ m.seats.forEach(d=>{meta[d.seat]=Object.assign(meta[d.seat]||{},d); render(d.seat,meta[d.seat])}); return; }
  if(m.kind==='broke'){ const d=meta[m.seat]=meta[m.seat]||{}; d.broke=true; render(m.seat,d); add('seat '+m.seat+' is broke','broke'); return; }
  if(m.kind==='phase'){ phase.textContent = m.phase.toUpperCase().replace('_',' ')+' · day '+m.day+(m.round?' · round '+m.round:''); add('— '+m.phase+' day '+m.day+' round '+m.round,'phase'); return; }
  if(m.kind==='posts_revealed'){ m.posts.forEach(p=>add('seat '+p.seat+': '+p.chat+(p.accuse!=null?' (accuses '+p.accuse+')':''))); return; }
  if(m.kind==='wolf_chat'){ add('[wolf '+m.seat+'] '+m.msg,'wolf'); return; }
  if(m.kind==='death'){ const d=meta[m.seat]=meta[m.seat]||{}; d.alive=false; d.role=m.role; render(m.seat,d); add('seat '+m.seat+' died ('+m.cause+') — '+m.role,'death'); return; }
  if(m.kind==='no_death'){ add('no death ('+m.cause+')'); return; }
  if(m.kind==='vote_tally'){ add('vote: '+m.ballots.map(b=>b.seat+'→'+(b.target==null?'-':b.target)).join('  ')+(m.eliminated!=null?' ⇒ '+m.eliminated:'')); return; }
  if(m.kind==='seer_result'){ add('seer '+m.seer+' checked '+m.seat+': '+m.role); return; }
  if(m.kind==='result'){ phase.textContent='RESULT: '+m.result.toUpperCase(); add('GAME OVER: '+m.result,'phase'); return; }
}
const es = new EventSource('/events?op='+encodeURIComponent(op));
es.onmessage = e => { try{ handle(JSON.parse(e.data)) }catch(_){}} ;
es.onerror = () => { phase.textContent='connection lost'; };
</script></body></html>
"""
