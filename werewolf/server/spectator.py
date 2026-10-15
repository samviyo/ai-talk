"""Single-page spectator UI for a 1920x1080 projector, fed by /events SSE; omniscient (roles shown).

Layout follows the Claude Design canvas: header with phase and clock, seat ring with a
most-accused tally, message board, wolf den, power-up feed, spend panel, full-screen
transition cards, a vote-count reveal and a game-over board. Panels for data the server
does not send yet (power-ups, whispers) stay hidden until the first such event arrives.
"""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Werewolf Night</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;800;900&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#0C1017;--panel:#151B26;--head:#11161F;--line:#232B38;--ring:#2A3342;--fg:#ECEAE4;--dim:#9AA3B2;
 --gold:#F2B84B;--wolf:#FF6B6B;--wolf-soft:#FF8A8A;--seer:#B79CFF;--doctor:#3DD9C1;--villager:#8FB4FF;--whisper:#C4B5FD;
 --disp:'Big Shoulders Display',Impact,sans-serif;--body:'IBM Plex Sans',system-ui,sans-serif;--mono:'IBM Plex Mono',ui-monospace,monospace}
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden;background:var(--bg);color:var(--fg);font-family:var(--body)}
#stage{position:absolute;left:50%;top:50%;width:1920px;height:1080px;transform-origin:center;display:grid;
 grid-template-columns:1080px 1fr;grid-template-rows:112px 1fr;transition:background 2.5s}
#stage.night{background:#070A18}
header{grid-column:1/3;display:flex;align-items:center;gap:40px;padding:0 48px;border-bottom:1px solid var(--line);background:var(--head);transition:background 2.5s}
#stage.night header{background:#0A0E22}
#day{font:900 64px/1 var(--disp);letter-spacing:.02em;text-transform:uppercase;color:var(--gold)}
#stage.night #day{color:#9DB4FF}
#sub{font:600 40px/1 var(--disp);text-transform:uppercase}
.clockwrap{flex:1;display:flex;align-items:center;gap:20px}
#clock{flex:1;height:14px;border-radius:7px;background:var(--line);overflow:hidden}
#clockbar{height:100%;width:100%;background:var(--gold);border-radius:7px;transform-origin:left;transition:background .4s}
#clockbar.low{background:var(--wolf)}
#secs{font:600 44px/1 var(--mono);min-width:150px;text-align:right}
#secs.paused{font-size:28px;color:var(--dim)}
.counts{display:flex;gap:28px;font:800 40px/1 var(--disp)}
.counts span{display:flex;align-items:center;gap:10px}
main{position:relative;display:flex;align-items:center;justify-content:center}
#ringwrap{position:relative;width:960px;height:940px}
#ringline{position:absolute;inset:140px;border-radius:50%;border:1px dashed var(--ring)}
#lines,#arrows{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:2}
#center{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:600px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:14px}
#center h3{margin:0;font:600 26px/1 var(--disp);letter-spacing:.18em;text-transform:uppercase;color:var(--dim)}
.acc{width:100%;display:grid;grid-template-columns:120px 1fr 40px;align-items:center;gap:14px;font-size:24px}
.acc .b{height:16px;border-radius:8px;background:var(--line);overflow:hidden}.acc .b i{display:block;height:100%;background:var(--gold);border-radius:8px;transition:width .6s}
.acc .n{font-family:var(--mono);text-align:right}
.seat{position:absolute;width:150px;transform:translate(-50%,-50%);padding:10px 10px 10px;border-radius:16px;background:var(--panel);
 text-align:center;display:flex;flex-direction:column;align-items:center;gap:4px;box-shadow:0 0 0 1px var(--ring);transition:opacity .6s,box-shadow .3s,transform .4s}
.seat .av{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;font:900 30px/1 var(--disp);color:var(--bg)}
.seat .team{font-weight:600;font-size:18px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:130px}
.seat .role{font:800 18px/1 var(--disp);letter-spacing:.12em;text-transform:uppercase}
.seat .bar{width:100%;height:6px;border-radius:3px;background:var(--line);overflow:hidden}.seat .bar i{display:block;height:100%;background:var(--villager);transition:width .6s}
.seat .usd{font:16px/1 var(--mono);color:var(--dim)}
.seat .badge{font:800 16px/1 var(--disp);letter-spacing:.1em;text-transform:uppercase;padding:4px 10px;border-radius:999px;color:var(--bg)}
.seat.wolf{box-shadow:0 0 0 2px var(--wolf)}
.seat.pending{box-shadow:0 0 0 3px var(--gold)}
.seat.thinking{animation:pulse 1.1s infinite}
@keyframes pulse{50%{box-shadow:0 0 22px 6px rgba(242,184,75,.55)}}
.seat.speaking{box-shadow:0 0 0 4px var(--gold),0 0 40px 8px rgba(242,184,75,.45);transform:translate(-50%,-50%) scale(1.18);z-index:3}
.seat.queued::after{content:attr(data-q);position:absolute;top:-12px;right:-12px;min-width:30px;height:30px;border-radius:15px;background:#3A2A12;color:var(--gold);font:800 20px/30px var(--disp);text-align:center}
.seat.done::before{content:'✓';position:absolute;top:-12px;left:-12px;width:30px;height:30px;border-radius:15px;background:#12302C;color:var(--doctor);font:700 18px/30px var(--mono);text-align:center}
.seat.onblock{outline:3px dashed var(--wolf-soft);outline-offset:6px}
.seat .tally{position:absolute;bottom:-18px;left:50%;transform:translateX(-50%);background:var(--gold);color:var(--bg);font:900 26px/1 var(--disp);padding:4px 14px;border-radius:999px;animation:pop .35s both}
#spot{width:600px;min-height:0;display:flex;flex-direction:column;align-items:center;gap:10px;transition:opacity .4s}
#spot.idle{opacity:0}
#center{z-index:3}#spot .say,#spot .who{text-shadow:0 0 12px var(--bg),0 0 24px var(--bg),0 0 4px var(--bg)}
#spot .who{display:flex;align-items:center;gap:14px;font:800 30px/1 var(--disp);letter-spacing:.06em;text-transform:uppercase}
#spot .who .av{width:52px;height:52px;border-radius:50%;display:flex;align-items:center;justify-content:center;font:900 30px/1 var(--disp);color:var(--bg)}
#spot .say{font-size:34px;line-height:1.3;font-weight:500;text-wrap:balance}
#spot .say.big{font:900 64px/1.05 var(--disp);text-transform:uppercase;letter-spacing:.02em}
#spot .tags{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}
#upnext{font:20px/1 var(--mono);color:var(--dim);min-height:20px}
#center.live h3,#center.live #accused{display:none!important}#spot.idle{display:none}
.seat.dead{opacity:.32}.seat.dead .av{background:#4A5260!important}
.seat.broke .bar i{background:var(--wolf)}.seat.broke .usd{color:var(--wolf);font-weight:600}
.seat.flash{transform:translate(-50%,-50%) scale(1.12)}
.wlabel{position:absolute;transform:translate(-50%,-50%);padding:6px 14px;border-radius:999px;background:#2A2340;color:var(--whisper);font-size:18px;font-weight:600;white-space:nowrap}
aside{border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
section{display:flex;flex-direction:column;min-height:0;padding:20px 32px;border-top:1px solid var(--line)}
section:first-child{border-top:0;flex:1;padding-bottom:0}
section h2{margin:0 0 12px;font:800 28px/1 var(--disp);letter-spacing:.14em;text-transform:uppercase;color:var(--dim)}
#board{flex:1;display:flex;flex-direction:column;gap:14px;overflow:hidden;justify-content:flex-end}
.post{display:grid;grid-template-columns:52px 1fr;gap:14px;align-items:start;animation:in .4s}
.post .av{width:52px;height:52px;border-radius:50%;display:flex;align-items:center;justify-content:center;font:900 28px/1 var(--disp);color:var(--bg)}
.bubble{background:var(--panel);border-radius:4px 18px 18px 18px;padding:12px 18px;display:flex;flex-direction:column;gap:6px}
.bubble .who{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.bubble .who b{font-size:20px}
.tag{font-size:16px;font-weight:600;padding:2px 10px;border-radius:999px}
.tag.claim{background:#2A2340;color:var(--whisper)}.tag.accuse{background:#3A2A12;color:var(--gold)}
.bubble .chat{font-size:25px;line-height:1.3}
.sys{font:18px/1.35 var(--mono);color:var(--dim);animation:in .4s}.sys.death{color:var(--wolf-soft)}
@keyframes in{from{opacity:0;transform:translateY(10px)}}
#den{background:#1A1014;border-top-color:#3A1E24;height:210px;flex:none}
#den h2{color:var(--wolf-soft)}
#denfeed,#shopfeed{display:flex;flex-direction:column;gap:8px;overflow:hidden;justify-content:flex-end}
#denfeed div{font-size:21px;animation:in .4s}#denfeed b{color:var(--wolf-soft)}#denfeed .sys{color:#C9A0A6}
#shop{background:#17140C;border-top-color:#3A3016;height:190px;flex:none}#shop h2{color:var(--gold)}
.buy{display:grid;grid-template-columns:30px 1fr auto;align-items:center;gap:12px;font-size:20px;animation:in .4s}
.buy .p{font-family:var(--mono);color:var(--gold)}
#spend{height:200px;flex:none;overflow:hidden}
.srow{display:grid;grid-template-columns:150px 1fr 96px;align-items:center;gap:14px;font-size:20px;margin:3px 0}
.srow span:first-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.srow .b{height:10px;border-radius:5px;background:var(--line);overflow:hidden}.srow .b i{display:block;height:100%;transition:width .6s}
.srow .v{font-family:var(--mono);text-align:right}
[hidden]{display:none!important}
@media (max-width:1099px){
 html,body{height:auto;overflow:auto}
 #stage{position:static;width:auto;height:auto;transform:none;display:flex;flex-direction:column}
 header{flex-wrap:wrap;gap:12px 20px;padding:14px 16px;position:sticky;top:0;z-index:3}
 #day{font-size:40px}#sub{font-size:24px}
 .clockwrap{order:3;flex-basis:100%}#secs{font-size:26px;min-width:70px}
 .counts{font-size:24px;gap:16px}.counts svg{width:22px;height:22px}
 main{padding:12px 16px}
 #ringwrap{width:100%;height:auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:10px}
 #ringline,#lines,.wlabel{display:none}
 .seat{position:static;width:auto;transform:none!important;padding:8px}
 .seat .av{width:38px;height:38px;font-size:22px}.seat .team{font-size:15px;max-width:100%}.seat .role{font-size:15px}.seat .usd{font-size:13px}
 #center{position:static;transform:none;width:100%;grid-column:1/-1;order:-1;padding:8px 0}
 #center h3{font-size:18px}.acc{font-size:18px;grid-template-columns:90px 1fr 30px}
 aside{border-left:0;border-top:1px solid var(--line)}
 section{padding:14px 16px}section:first-child{flex:none}
 #board{max-height:60vh;overflow-y:auto;justify-content:flex-start}
 #den,#shop,#spend{height:auto}
 section h2{font-size:20px}
 .bubble .chat{font-size:18px}.post{grid-template-columns:38px 1fr}.post .av{width:38px;height:38px;font-size:20px}
 #denfeed div,.buy,.srow{font-size:16px}.srow{grid-template-columns:110px 1fr 76px}
 .overlay{position:fixed}
 #card{gap:16px;padding:16px;overflow:hidden}#card h1{font-size:56px;max-width:100%;overflow-wrap:anywhere}#card .line{max-width:100%}#card .wake{flex-wrap:wrap;justify-content:center}#count{overflow:auto}.tiles{min-width:0}#card h1{font-size:56px}#card .line{font-size:20px}#card .left{font-size:22px;gap:16px}
 #card .icon{width:80px;height:80px}#card .rolechip{font-size:22px;padding:2px 10px}#card .wake .b{width:160px}#card .wake{font-size:16px}
 #count{padding:24px 16px;gap:18px}#count h1{font-size:44px}#count .top{flex-direction:column;gap:6px}#count .meta{font-size:16px}
 .crow{grid-template-columns:90px 1fr 50px;gap:12px}.crow .who b{font-size:26px}.crow .who span{font-size:14px}
 .tile{height:42px;min-width:42px;font-size:20px;padding:0 8px}.crow .tot{font-size:36px}#count .verdict{font-size:18px;padding:14px}
 #result{display:flex;flex-direction:column;overflow:auto}#result .l{padding:32px 16px;gap:14px}#result h1{font-size:80px}#result .k{font-size:18px}
 #result aside{padding:20px 16px}#result .tl{font-size:16px;grid-template-columns:80px 1fr}#result .stats b{font-size:32px}
}
.overlay{position:absolute;inset:0;z-index:5;opacity:0;pointer-events:none;transition:opacity .9s}
.overlay.show{opacity:1}
#card{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:32px;text-align:center}
#card.night{background:radial-gradient(circle at 50% 40%,rgba(14,20,52,.97),rgba(3,4,14,.99))}
#card.day{background:radial-gradient(circle at 50% 30%,rgba(78,56,16,.96),rgba(14,11,6,.98))}
#card.vote{background:radial-gradient(circle at 50% 40%,rgba(52,42,10,.96),rgba(10,9,6,.98))}
#card .icon{animation:rise 1.2s ease}
#card h1{margin:0;font:900 200px/.9 var(--disp);text-transform:uppercase;animation:rise 1.4s ease}
#card .line{font-size:44px;font-weight:600;max-width:1500px;animation:rise 1.8s ease}
#card .rolechip{font:900 52px/1 var(--disp);letter-spacing:.08em;text-transform:uppercase;padding:4px 22px;border-radius:12px;color:var(--bg)}
#card .left{font:800 44px/1 var(--disp);text-transform:uppercase;display:flex;gap:48px}
#card .wake{display:flex;align-items:center;gap:18px;font-size:28px;color:var(--dim);margin-top:24px}
#card .wake .b{width:360px;height:6px;border-radius:3px;background:rgba(255,255,255,.12);overflow:hidden}
#card .wake .b i{display:block;height:100%;width:0;background:#9DB4FF}
/* transitions take over the header strip so the ring, arrows and board stay visible */
@media (min-width:1100px){
 #card{inset:0 0 auto 0;height:112px;flex-direction:row;justify-content:flex-start;gap:28px;padding:0 48px;text-align:left;box-shadow:0 2px 0 var(--gold)}
 #card .icon{width:64px;height:64px;flex:none}#card h1{font-size:76px;white-space:nowrap}
 #card .line{font-size:24px;max-width:none;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 #card .rolechip{font-size:22px;padding:2px 10px}
 #card .left{font-size:28px;gap:14px;flex:none}#card .wake{margin:0;font-size:18px;flex:none}#card .wake .b{width:140px}
}
}
@keyframes rise{from{opacity:0;transform:translateY(28px)}}
#count{background:#0F0D08;padding:72px 96px;display:flex;flex-direction:column;gap:44px}
#count .top{display:flex;align-items:baseline;justify-content:space-between}
#count h1{margin:0;font:900 120px/.9 var(--disp);text-transform:uppercase}
#count .meta{font:28px var(--mono);color:var(--dim)}
.crow{display:grid;grid-template-columns:260px 1fr 110px;align-items:center;gap:28px;animation:in .5s both}
.crow .who b{display:block;font:900 56px/1 var(--disp)}.crow .who span{font-size:24px;color:var(--dim)}
.tiles{display:flex;flex-wrap:wrap;gap:12px}
.tile{height:72px;min-width:72px;padding:0 16px;border-radius:14px;display:flex;align-items:center;justify-content:center;gap:8px;font:900 36px/1 var(--disp);background:#1F2633;animation:pop .35s both}
.tile.dbl{background:var(--gold);color:#0F0D08;box-shadow:0 0 24px 4px rgba(242,184,75,.55)}
@keyframes pop{from{opacity:0;transform:scale(.6)}}
.crow .tot{font:900 72px/1 var(--disp);text-align:right}
#count .verdict{margin-top:auto;padding:28px 36px;border-radius:20px;background:#1C170C;box-shadow:0 0 0 2px var(--gold);font-size:38px;font-weight:600}
#result{background:#140A0D;display:grid;grid-template-columns:1fr 760px}
#result.show{pointer-events:auto}
#result.village{background:#0A1220}
#result .l{display:flex;flex-direction:column;justify-content:center;gap:28px;padding:0 96px}
#result .k{font:800 40px/1 var(--disp);letter-spacing:.2em;text-transform:uppercase;color:var(--dim)}
#result h1{margin:0;font:900 240px/.85 var(--disp);text-transform:uppercase}
#result .chips{display:flex;flex-wrap:wrap;gap:20px}
#result .chip{padding:18px 26px;border-radius:18px;background:#2A1217;box-shadow:0 0 0 2px var(--wolf);display:flex;flex-direction:column;gap:4px}
#result .chip b{font:900 40px/1 var(--disp)}#result .chip span{font-size:24px;font-weight:600}
#result aside{border-left:1px solid #3A1E24;background:rgba(255,255,255,.02);padding:72px 56px;gap:22px}
#result .tl{display:grid;grid-template-columns:110px 1fr;gap:18px;font-size:24px;line-height:1.3}
#result .tl span:first-child{font:600 20px var(--mono);color:var(--dim)}
#result .stats{margin-top:auto;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
#result .stats b{display:block;font:900 56px/1 var(--disp)}#result .stats span{font-size:20px;color:var(--dim)}
</style></head><body>
<div id="stage">
 <header>
  <div style="display:flex;align-items:baseline;gap:20px"><div id="day">Connecting</div><div id="sub"></div></div>
  <div class="clockwrap"><div id="clock"><div id="clockbar"></div></div><div id="secs"></div></div>
  <div class="counts"><span style="color:var(--wolf)"><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 4l3 6-2 6 7 5 7-5-2-6 3-6-6 3h-4z"/></svg><b id="nw">0</b> wolves</span>
   <span style="color:var(--villager)"><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 11l9-7 9 7v9H3z"/><path d="M10 20v-6h4v6"/></svg><b id="nv">0</b> village</span></div>
 </header>
 <main><div id="ringwrap"><div id="ringline"></div><svg id="lines" viewBox="0 0 960 940" aria-hidden="true"></svg><svg id="arrows" viewBox="0 0 960 940" aria-hidden="true"><defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="context-stroke"/></marker></defs><g id="arrowg"></g></svg>
  <div id="center"><div id="spot" class="idle"></div><div id="upnext"></div><h3>Most accused</h3><div id="accused" style="width:100%;display:flex;flex-direction:column;gap:12px"></div></div></div></main>
 <aside>
  <section><h2>Message board</h2><div id="board"></div></section>
  <section id="den"><h2>Wolf den · audience only</h2><div id="denfeed"></div></section>
  <section id="shop" hidden><h2>Power-ups · secret from bots</h2><div id="shopfeed"></div></section>
  <section id="spend"><h2 id="spendh">Spend vs budget</h2><div id="spendrows"></div></section>
 </aside>
 <div id="card" class="overlay"></div>
 <div id="count" class="overlay"></div>
 <div id="result" class="overlay"></div>
</div>
<script>
const $=id=>document.getElementById(id);
const op=new URLSearchParams(location.search).get('op')||'';
const TINT={werewolf:'var(--wolf)',seer:'var(--seer)',doctor:'var(--doctor)',villager:'var(--villager)'};
const ITEM={extra_post:['extra post','var(--villager)'],whisper:['whisper','var(--whisper)'],double_vote:['double vote','var(--gold)'],shield:['shield','var(--doctor)'],frame:['frame','var(--wolf)']};
const CARD_MS=4500,COUNT_MS=6500;
let N=0,budget=0.5,meta={},els={},pos={},accuse={},badges={},whispers=[];
let held=0,live=false,everLive=false,lastPhase=null,outcome=[],queue=[],busy=false,curDay=1;
let stats={posts:0,buys:0},timeline=[];
let sawReveal=false,floor={speaking:null,queue:[]},acted=new Set(),onblock=new Set(),tallies={};
let clk={turn:null,end:0,win:1,heldAt:false},turnSeq=0;
/* the bar is driven per frame from a local deadline, so it drains smoothly and snaps full on a new turn */
function clockTick(m){
  const turn=turnSeq;
  if(held>0){clk.heldAt=true;clk.turn=null;return}
  const end=performance.now()+(m.remaining_s||0)*1000;
  if(clk.heldAt||turn!==clk.turn||Math.abs(end-clk.end)>1500){clk.end=end;clk.win=(m.window_s||1)*1000;clk.turn=turn}
  clk.heldAt=false;
}
function frame(){
  const bar=$('clockbar');
  if(held>0){bar.style.transform='scaleX(1)';bar.style.background='#5B6577';bar.classList.remove('low')}
  else{const left=Math.max(0,clk.end-performance.now()),f=Math.min(1,left/clk.win);
    bar.style.transform='scaleX('+f+')';bar.style.background='';bar.classList.toggle('low',f<0.25&&f>0)}
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
function esc(t){return String(t??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function fit(){const st=$('stage');if(innerWidth<1100){st.style.transform='';return}const s=Math.min(innerWidth/1920,innerHeight/1080);st.style.transform='translate(-50%,-50%) scale('+s+')'}
addEventListener('resize',fit);fit();
function team(s){const d=meta[s]||{};return d.house?'house · '+d.house:(d.team||'Seat '+s)}
function name(s){return 'Seat '+s+' · '+team(s)}
function tint(s){const d=meta[s]||{};return TINT[d.role]||'var(--dim)'}
function layout(n){
  N=n;$('ringwrap').querySelectorAll('.seat').forEach(e=>e.remove());els={};pos={};
  for(let s=0;s<n;s++){const a=2*Math.PI*s/n-Math.PI/2,x=50+42*Math.cos(a),y=50+40*Math.sin(a);pos[s]=[x,y];
    const el=document.createElement('div');el.className='seat';el.style.left=x+'%';el.style.top=y+'%';$('ringwrap').appendChild(el);els[s]=el}
}
function render(s){
  const el=els[s],d=meta[s];if(!el||!d)return;
  el.className='seat'+(d.role==='werewolf'&&d.alive!==false?' wolf':'')+(d.alive===false?' dead':'')+(d.broke?' broke':'')+(d.pending?' pending':'')+(d.thinking?' thinking':'')+(floor.speaking==s?' speaking':'')+(floor.queue.includes(s)?' queued':'')+(acted.has(s)?' done':'')+(onblock.has(s)?' onblock':'');
  el.dataset.q=floor.queue.indexOf(s)+1;
  const pct=Math.min(100,100*(d.spend||0)/budget),b=badges[s];
  el.innerHTML='<div class="av" style="background:'+tint(s)+'">'+s+'</div><div class="team">'+esc(team(s))+'</div>'
   +'<div class="role" style="color:'+tint(s)+'">'+esc(d.role||'?')+'</div>'
   +(b?'<div class="badge" style="background:'+b[1]+'">'+esc(b[0])+'</div>':'')
   +'<div class="bar"><i style="width:'+pct+'%"></i></div><div class="usd">'+(d.broke?'BROKE · ':'')+'$'+(d.spend||0).toFixed(3)+'</div>'
   +(tallies[s]?'<div class="tally">'+tallies[s]+'</div>':'');
}
function flash(s){const el=els[s];if(!el)return;el.classList.add('flash');setTimeout(()=>el.classList.remove('flash'),600)}
function counts(){let w=0,v=0;for(const s in meta){if(meta[s].alive!==false){meta[s].role==='werewolf'?w++:v++}}$('nw').textContent=w;$('nv').textContent=v;return[w,v]}
function trim(feed,max){while(feed.children.length>max)feed.removeChild(feed.firstChild)}
function sys(text,cls){const el=document.createElement('div');el.className='sys '+(cls||'');el.innerHTML=text;$('board').appendChild(el);trim($('board'),40)}
function post(p){
  stats.posts++;
  const el=document.createElement('div');el.className='post';
  const tags=(p.claimed_role?'<span class="tag claim">claims '+esc(p.claimed_role)+'</span>':'')+(p.accuse!=null?'<span class="tag accuse">accuses seat '+esc(p.accuse)+'</span>':'')+(p.nominate!=null?'<span class="tag accuse">nominates seat '+esc(p.nominate)+'</span>':'')+(p.second!=null?'<span class="tag accuse">seconds seat '+esc(p.second)+'</span>':'');
  const chats=[p.chat,p.chat2].filter(Boolean).map(c=>'<div class="chat">'+esc(c)+'</div>').join('');
  el.innerHTML='<div class="av" style="background:'+tint(p.seat)+'">'+p.seat+'</div><div class="bubble"><div class="who"><b>'+esc(team(p.seat))+'</b>'+tags+'</div>'+chats+'</div>';
  $('board').appendChild(el);trim($('board'),40);flash(p.seat);
  if(p.accuse!=null){accuse[p.accuse]=(accuse[p.accuse]||0)+1;drawAccused()}
  spot(p.seat,p.chat,tags);
  const t=p.accuse??p.nominate??p.second;clearArrows();if(t!=null)arrow(p.seat,t,'var(--gold)');
}
/* centre spotlight: the one voice the room should be reading right now */
function spot(seat,text,tags,big){
  const el=$('spot');el.classList.remove('idle');$('center').classList.add('live');
  el.innerHTML=(seat!=null?'<div class="who"><div class="av" style="background:'+tint(seat)+'">'+seat+'</div>'+esc(team(seat))+'</div>':'')
   +(tags?'<div class="tags">'+tags+'</div>':'')+'<div class="say'+(big||String(text).length<48?' big':'')+'">'+esc(text)+'</div>';
}
function quiet(){$('spot').classList.add('idle');$('center').classList.remove('live');$('upnext').textContent=''}
function arrow(from,to,color,width){
  if(!pos[from]||!pos[to]||from==to)return;
  let[x1,y1]=pos[from],[x2,y2]=pos[to];x1*=9.6;y1*=9.4;x2*=9.6;y2*=9.4;
  const dx=x2-x1,dy=y2-y1,L=Math.hypot(dx,dy),k=80/L;
  const l=document.createElementNS('http://www.w3.org/2000/svg','line');
  l.setAttribute('x1',x1+dx*k);l.setAttribute('y1',y1+dy*k);l.setAttribute('x2',x2-dx*k);l.setAttribute('y2',y2-dy*k);
  l.setAttribute('stroke',color);l.setAttribute('stroke-width',width||5);l.setAttribute('stroke-linecap','round');l.setAttribute('marker-end','url(#ah)');
  l.style.strokeDasharray=L;l.style.strokeDashoffset=L;l.style.transition='stroke-dashoffset .6s ease-out';
  $('arrowg').appendChild(l);requestAnimationFrame(()=>requestAnimationFrame(()=>l.style.strokeDashoffset=0));
}
function clearArrows(){$('arrowg').innerHTML=''}
function renderAll(){for(const s in meta)render(+s)}
function roleSeat(r){return Object.keys(meta).map(Number).filter(s=>meta[s].role===r&&meta[s].alive!==false)}
function den(html){const el=document.createElement('div');el.innerHTML=html;$('denfeed').appendChild(el);trim($('denfeed'),8)}
function drawAccused(){
  const rows=Object.entries(accuse).filter(([s])=>meta[s]&&meta[s].alive!==false).sort((a,b)=>b[1]-a[1]).slice(0,4);
  const top=rows.length?rows[0][1]:1;
  $('accused').innerHTML=rows.length?rows.map(([s,n])=>'<div class="acc"><div style="text-align:left;font-weight:600">Seat '+s+'</div><div class="b"><i style="width:'+(100*n/top)+'%"></i></div><div class="n">'+n+'</div></div>').join(''):'<div class="sys">no accusations yet</div>';
}
function drawSpend(){
  const t={};for(const s in meta){const k=team(s);t[k]=t[k]||{usd:0,n:0,broke:false};t[k].usd+=meta[s].spend||0;t[k].n++;t[k].broke=t[k].broke||!!meta[s].broke}
  const rows=Object.entries(t).sort((a,b)=>b[1].usd-a[1].usd).slice(0,4);
  $('spendh').textContent='Spend vs $'+budget.toFixed(2)+' budget'+(stats.buys?' · thinking + power-ups':'');
  $('spendrows').innerHTML=rows.map(([k,v])=>{const pct=Math.min(100,100*v.usd/(budget*v.n));
    return '<div class="srow"><span>'+esc(k)+'</span><span class="b"><i style="width:'+pct+'%;background:'+(v.broke?'var(--wolf)':pct>75?'var(--gold)':'var(--villager)')+'"></i></span><span class="v">$'+v.usd.toFixed(3)+'</span></div>'}).join('');
}
function drawLines(){
  const svg=$('lines');svg.innerHTML='';$('ringwrap').querySelectorAll('.wlabel').forEach(e=>e.remove());
  whispers.forEach(w=>{if(!pos[w.from]||!pos[w.to])return;const[x1,y1]=pos[w.from],[x2,y2]=pos[w.to];
    const l=document.createElementNS('http://www.w3.org/2000/svg','line');
    l.setAttribute('x1',x1*9.6);l.setAttribute('y1',y1*9.4);l.setAttribute('x2',x2*9.6);l.setAttribute('y2',y2*9.4);
    l.setAttribute('stroke','#C4B5FD');l.setAttribute('stroke-width','3');l.setAttribute('stroke-dasharray','4 10');l.setAttribute('stroke-linecap','round');svg.appendChild(l);
    const lab=document.createElement('div');lab.className='wlabel';lab.style.left=(x1+x2)/2+'%';lab.style.top=(y1+y2)/2+'%';lab.textContent='whisper '+w.from+' → '+w.to;$('ringwrap').appendChild(lab)});
}
function setPhase(p,day,round){
  curDay=day;$('stage').classList.toggle('night',p==='night');
  $('day').textContent=(p==='night'?'Night ':'Day ')+day;
  $('sub').textContent={night:'',day_discussion:'Discussion · round '+round,day_vote:'Vote',over:'Game over'}[p]??'';
}
function show(item){if(item.kind==='card')queue=queue.filter(q=>q.kind!=='card');queue.push(item);if(!busy)next()}
function next(){
  const it=queue.shift();if(!it){busy=false;return}busy=true;
  const el=$(it.kind);el.className='overlay '+(it.cls||'');el.innerHTML=it.html;requestAnimationFrame(()=>el.classList.add('show'));
  if(it.kind==='result')return;
  const t0=Date.now(),min=it.kind==='count'?COUNT_MS:CARD_MS;
  const iv=setInterval(()=>{const wake=el.querySelector('.wake i');if(wake)wake.style.width=Math.min(100,100*(Date.now()-t0)/Math.max(min,held*1000))+'%';
    if(Date.now()-t0>=min&&(held<=0||queue.length)){clearInterval(iv);el.classList.remove('show');setTimeout(next,900)}},200);
}
const MOON='<svg class="icon" width="168" height="168" viewBox="0 0 24 24" fill="none" stroke="#9DB4FF" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z"/></svg>';
const SUN='<svg class="icon" width="168" height="168" viewBox="0 0 24 24" fill="none" stroke="#F2B84B" stroke-width="1.2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></svg>';
const BOX='<svg class="icon" width="150" height="150" viewBox="0 0 24 24" fill="none" stroke="#F2B84B" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 11h18v9H3z"/><path d="M8 11V4h8v7"/><path d="M10 7h4"/></svg>';
function card(e){
  if(!live||(e.phase===lastPhase&&e.phase!=='day_discussion')){lastPhase=e.phase;return}
  const prev=lastPhase;lastPhase=e.phase;const[w,v]=counts();
  const left='<div class="left"><span style="color:var(--wolf)">'+w+' wolves</span><span style="color:#4A5260">·</span><span style="color:var(--villager)">'+v+' village</span></div>';
  const out=outcome.length?'<div class="line">'+outcome.join(' · ')+'</div>':'';outcome=[];
  const wake=t=>'<div class="wake"><div class="b"><i></i></div><span>'+t+'</span></div>';
  if(e.phase==='night')show({kind:'card',cls:'night',html:MOON+'<h1>'+(e.day===1?'Night 1':'Night falls')+'</h1>'+(out||'<div class="line">The village sleeps. The wolves wake.</div>')+left+wake('Wolves wake soon')});
  else if(e.phase==='day_discussion'&&prev==='night')show({kind:'card',cls:'day',html:SUN+'<h1>Day '+e.day+'</h1>'+(out||'<div class="line">Everyone survived the night</div>')+left+wake('Discussion opens soon')});
  else if(e.phase==='day_vote')show({kind:'card',cls:'vote',html:BOX+'<h1>The village votes</h1><div class="line">Day '+e.day+'</div>'+left+wake('Ballots open soon')});
}
function countReveal(e){
  const groups={};e.ballots.forEach(b=>{if(b.target==null)return;(groups[b.target]=groups[b.target]||[]).push(b)});
  const rows=Object.entries(groups).map(([t,bs])=>[t,bs,bs.reduce((a,b)=>a+(b.weight||1),0)]).sort((a,b)=>b[2]-a[2]).slice(0,4);
  const dbl=e.ballots.find(b=>(b.weight||1)>1);
  if(!rows.length){sys('🗳️ No votes cast · no one eliminated');return}
  let html='<div class="top"><h1>The count · Day '+e.day+'</h1><div class="meta">'+e.ballots.filter(b=>b.target!=null).length+' ballots'+(dbl?' · 1 doubled':'')+'</div></div><div style="display:flex;flex-direction:column;gap:28px">';
  rows.forEach(([t,bs,tot],i)=>{html+='<div class="crow" style="animation-delay:'+(i*0.25)+'s"><div class="who"><b>Seat '+t+'</b><span>'+esc(team(t))+'</span></div><div class="tiles">'
    +bs.map((b,j)=>'<div class="tile'+((b.weight||1)>1?' dbl':'')+'" style="animation-delay:'+(i*0.25+j*0.08)+'s">'+b.seat+((b.weight||1)>1?'<span style="font-size:26px">×2</span>':'')+'</div>').join('')
    +'</div><div class="tot" style="color:'+(String(e.eliminated)===t?'var(--gold)':'var(--fg)')+'">'+tot+'</div></div>'});
  html+='</div><div class="verdict">'+(e.eliminated!=null?esc(name(e.eliminated))+' is voted out.':'Tie. No one is eliminated.')
    +(dbl?' <span style="color:var(--gold)">Seat '+dbl.seat+' paid to count twice.</span>':'')+'</div>';
  if(!sawReveal)show({kind:'count',html});sawReveal=false;
  sys('🗳️ '+rows.map(([t,,tot])=>'seat '+t+': '+tot).join(' · ')+(e.eliminated!=null?' ⇒ <b>'+esc(name(e.eliminated))+'</b> out':' ⇒ no one'));
}
function result(e){
  const wolvesWin=e.result==='wolves',title=wolvesWin?'Wolves win':e.result==='village'?'Village wins':'Draw';
  const wolves=Object.keys(meta).filter(s=>meta[s].role==='werewolf');
  const total=Object.values(meta).reduce((a,d)=>a+(d.spend||0),0),broke=Object.values(meta).filter(d=>d.broke).length;
  const html='<div class="l"><div class="k">Day '+curDay+' · game over</div><h1 style="color:'+(wolvesWin?'var(--wolf)':'var(--villager)')+'">'+title+'</h1>'
   +'<div class="chips">'+wolves.map(s=>'<div class="chip"><b>Seat '+s+'</b><span>'+esc(team(s))+'</span></div>').join('')+'</div></div>'
   +'<aside><h2 style="margin:0;font:800 36px/1 var(--disp);letter-spacing:.14em;text-transform:uppercase;color:var(--dim)">How it went</h2>'
   +timeline.slice(-7).map(t=>'<div class="tl"><span>'+esc(t[0])+'</span><span>'+esc(t[1])+'</span></div>').join('')
   +'<div class="stats"><div><b>$'+total.toFixed(2)+'</b><span>total spend</span></div><div><b>'+broke+'</b><span>went broke</span></div><div><b>'+(stats.buys||stats.posts)+'</b><span>'+(stats.buys?'power-ups':'posts')+'</span></div></div></aside>';
  show({kind:'result',cls:wolvesWin?'':'village',html});
}
function reset(m){
  budget=m.budget_usd||budget;meta={};accuse={};badges={};whispers=[];stats={posts:0,buys:0};timeline=[];outcome=[];lastPhase=null;
  layout(m.seats);m.seat_info.forEach(d=>{meta[d.seat]=d;render(d.seat)});
  $('board').innerHTML='';$('denfeed').innerHTML='';$('shopfeed').innerHTML='';$('shop').hidden=true;
  floor={speaking:null,queue:[]};acted=new Set();onblock=new Set();tallies={};clearArrows();quiet();
  queue=[];['card','count','result'].forEach(k=>$(k).classList.remove('show'));busy=false;
  live=everLive;counts();drawAccused();drawSpend();drawLines();
  sys('Game '+m.game+' · '+m.seats+' seats');
}
function onEvent(e){
  switch(e.kind){
    case 'phase':turnSeq++;acted=new Set();floor={speaking:null,queue:[]};$('upnext').textContent='';if(e.phase!=='defence'){tallies={};clearArrows()}
      if(e.phase==='night'){onblock=new Set();quiet()}
      if(e.phase==='defence'&&e.seat!=null){spot(e.seat,'On the block · last words',null,true);floor={speaking:e.seat,queue:[]}}
      renderAll();
      if(e.phase!=='over'){card(e);setPhase(e.phase,e.day,e.round);
      if(e.phase==='day_discussion'&&e.round===1){accuse={};drawAccused()}
      sys({night:'🌙 Night '+e.day,day_discussion:'☀️ Day '+e.day+' · round '+e.round,day_vote:'🗳️ Day '+e.day+' · vote'}[e.phase]||e.phase)}break;
    case 'posts_revealed':e.posts.forEach(post);break;
    case 'wolf_chat':den('<b>Seat '+e.seat+'</b> '+esc(e.msg));break;
    case 'seer_result':den('<span class="sys">Seer '+e.seer+' checked seat '+e.seat+': <b>'+esc(e.role)+'</b></span>');break;
    case 'night_result':if(e.kill_target!=null)den('<span class="sys">Wolves targeted seat '+e.kill_target+(e.save_target!=null?' · doctor guarded seat '+e.save_target:'')+(e.killed==null?' · <b>saved</b>':'')+'</span>');break;
    case 'vote_tally':countReveal(e);if(e.silent&&e.silent.length)sys('🤐 Stayed silent: seats '+e.silent.join(', '));break;
    case 'floor':floor={speaking:e.speaking??null,queue:e.queue||[]};renderAll();
      $('upnext').textContent=floor.queue.length?'Up next: '+floor.queue.map(s=>'seat '+s).join(' · '):'';break;
    case 'cut_off':sys('✂️ Out of time · cut off: seats '+e.seats.join(', '));break;
    case 'nomination':sys('📌 Seat '+e.seat+' nominates <b>seat '+e.target+'</b>');break;
    case 'seconded':sys('✋ Seat '+e.seat+' seconds seat '+e.target);break;
    case 'standing':onblock=new Set(e.targets);renderAll();sys('⚖️ On the block: <b>seats '+e.targets.join(', ')+'</b>');
      spot(null,'On the block: '+e.targets.map(t=>'seat '+t).join(', '),null,true);break;
    case 'vote_skipped':sys('🕊️ No nominations stood · no vote today'+(e.silent&&e.silent.length?' · silent: seats '+e.silent.join(', '):''));
      spot(null,'No vote today',null,true);break;
    case 'ballot_cast':case 'night_acted':acted.add(e.seat);render(e.seat);break;
    case 'ballot_reveal':sawReveal=true;arrow(e.seat,e.target,(e.weight||1)>1?'var(--gold)':'var(--fg)',(e.weight||1)>1?10:5);
      tallies=e.tally||tallies;acted.delete(e.seat);renderAll();flash(e.seat);
      spot(e.seat,'votes seat '+e.target+((e.weight||1)>1?' ×2':''),null,true);break;
    case 'night_beat':{const T=e.target;
      if(e.step==='wolves'){roleSeat('werewolf').forEach(w=>arrow(w,T,'var(--wolf)'));spot(null,'The wolves pick seat '+T,null,true)}
      else if(e.step==='doctor'){roleSeat('doctor').forEach(d=>arrow(d,T,'var(--doctor)'));spot(null,'The doctor guards seat '+T,null,true)}
      else if(e.step==='seer'){arrow(e.seer,T,'var(--seer)');spot(null,'The seer checks seat '+T+': '+e.role,null,true)}
      if(T!=null)flash(T);break}
    case 'thought':den('<span class="sys">🧠 '+esc(team(e.seat))+':</span> '+esc(e.text));break;
    case 'death':{const d=meta[e.seat]=meta[e.seat]||{};d.alive=false;d.role=e.role;render(e.seat);flash(e.seat);counts();delete badges[e.seat];
      const txt=esc(name(e.seat))+(e.cause==='night'?' was killed in the night':' was voted out');
      outcome.push(txt+' — <span class="rolechip" style="background:'+(TINT[e.role]||'var(--dim)')+'">'+esc(e.role)+'</span>');
      timeline.push([(e.cause==='night'?'Night ':'Day ')+e.day,'Seat '+e.seat+(e.cause==='night'?' killed. ':' voted out. ')+e.role[0].toUpperCase()+e.role.slice(1)+'.']);
      sys('✝ '+txt+' — '+esc(e.role),'death');drawAccused();break}
    case 'no_death':outcome.push(e.cause==='night'?'Nobody died last night':'The village could not agree. No one was eliminated.');
      sys(e.cause==='night'?'🌅 Nobody died last night':'⚖️ No one was eliminated');break;
    case 'powerup':{const it=ITEM[e.item]||[e.item,'var(--gold)'];stats.buys++;$('shop').hidden=false;
      if(e.item==='shield'||e.item==='double_vote'){badges[e.seat]=[e.item==='shield'?'shield':'2× vote',it[1]];render(e.seat)}
      const el=document.createElement('div');el.className='buy';
      el.innerHTML='<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="'+it[1]+'" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M9 10h4.5a1.5 1.5 0 0 1 0 3h-3a1.5 1.5 0 0 0 0 3H15"/></svg><span><b>'+esc(name(e.seat))+'</b> bought <b style="color:'+it[1]+'">'+esc(it[0])+'</b>'+(e.target!=null?' on seat '+esc(e.target):'')+'</span><span class="p">'+(e.usd?'−$'+Number(e.usd).toFixed(2):'')+'</span>';
      $('shopfeed').appendChild(el);trim($('shopfeed'),5);timeline.push(['Day '+e.day,'Seat '+e.seat+' bought '+it[0]+'.']);drawSpend();break}
    case 'whisper':whispers.push({from:e.seat,to:e.target});whispers=whispers.slice(-3);drawLines();
      den('<span class="sys" style="color:var(--whisper)">Whisper '+e.seat+' → '+esc(e.target)+':</span> '+esc(e.msg));break;
    case 'result':result(e);clk.end=0;$('secs').textContent='';break;
  }
}
function handle(m){
  if(m.kind==='init'){reset(m);return}
  if(m.kind==='tick'){
    if(!live){live=everLive=true;outcome=[]}
    held=m.held_s||0;clockTick(m);
    const s=$('secs');s.className=held>0?'paused':'';s.textContent=held>0?'paused':(m.pending.length?Math.ceil(m.remaining_s)+'s':'');
    const pend=new Set(m.pending),think=new Set(m.thinking);
    m.seats.forEach(x=>{const d=meta[x.seat]=meta[x.seat]||{};d.spend=x.spent_usd;d.broke=x.broke;d.alive=x.alive;d.pending=pend.has(x.seat);d.thinking=think.has(x.seat);if(x.house)d.house=x.house;render(x.seat)});
    if(m.waiting_ready&&m.waiting_ready.length){$('day').textContent='Waiting';$('sub').textContent='bots joining: '+m.waiting_ready.length}
    counts();drawSpend();return;
  }
  if(m.kind==='broke'){const d=meta[m.seat]=meta[m.seat]||{};d.broke=true;render(m.seat);sys('💸 '+esc(name(m.seat))+' is out of budget and goes mute','death');timeline.push(['Day '+curDay,'Seat '+m.seat+' went broke.']);return}
  onEvent(m);
}
let es;function connect(){es=new EventSource('/events?op='+encodeURIComponent(op));
  es.onmessage=ev=>{try{handle(JSON.parse(ev.data))}catch(err){console.error(err)}};
  es.onerror=()=>{$('day').textContent='Reconnecting';es.close();setTimeout(connect,1500)}}
$('result').onclick=()=>{$('result').classList.remove('show');busy=false;next()};
connect();
</script></body></html>
"""
