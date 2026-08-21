/* dashboard/factory/scene_canvas.js — animierte Canvas-Fabrik (Vision-Transfer 24.7.2026).
 *
 * Ersetzt die statische SVG-Szene (scene.py) im Live-Haupt-Tab durch ein
 * animiertes <canvas>. Reine Zeichen-/Animationslogik; die echten Daten kommen
 * aus Python (state.read_state()) als injiziertes STATE-Objekt, die Farben aus
 * theme.PALETTE als P.
 *
 * Stufe 4 (freigegebene Halle, 24.7.2026): EIN großer Raum, Warenfluss
 * waagerecht links→rechts. Ein DURCHGEHENDES Förderband trägt die Kisten durch
 * die ganze Entscheidungs-Kette (Wareneingang → Daten-Kontrolle →
 * Katalysator-Weiche → Analyse → Sicherung → Bestands-/Signal-/Risiko-Prüfung →
 * Positions-Limit → Order-Schleuse); die Prozess-Maschinen sitzen AUF dem Band
 * und verdecken die Kiste beim Durchlauf (rein → raus). Lager/Ausschuss als
 * Abzweig-Schienen, Lern-Labor als Turm, Uhr/Wetter als Ecken-HUD,
 * Kontrollraum/Backup als Eck-Räume. Welt größer als Sichtfenster → Kamera
 * schwenkt per Ziehen. canvas.py ersetzt __PALETTE__ / __STATE__.
 */
"use strict";
const P = __PALETTE__;
const STATE = __STATE__;

// Sichtfenster (Canvas) vs. Welt (größer, wird geschwenkt).
const VIEW_W = 1200, VIEW_H = 760;
const canvas = document.getElementById("factory");
const DPR = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
canvas.width = VIEW_W * DPR; canvas.height = VIEW_H * DPR;
const ctx = canvas.getContext("2d");
ctx.scale(DPR, DPR);

const BELT_Y = 340, CH = 150, CY = BELT_Y - CH / 2;   // Ketten-Boxen zentriert aufs Band
// Hallen-Layout (Welt-Koordinaten). Kette = auf dem Band; Rest = Abzweige/Räume.
const LAYOUT = {
  docks:           [40, CY, 200, CH],
  data_gate:       [296, CY, 200, CH],
  catalyst_check:  [620, 250, 150, 180],   // Weiche am Split-Punkt (auf dem Band)
  analyzer_ollama: [800, 195, 240, 130],   // oberes Band (Mitte auf upperY=260)
  analyzer_claude: [800, 355, 240, 130],   // unteres Band (Mitte auf lowerY=420)
  breaker:         [1100, CY, 190, CH],
  position_check:  [1320, CY, 200, CH],
  signal_check:    [1576, CY, 200, CH],
  risk_check:      [1832, CY, 200, CH],
  position_limit:  [2088, CY, 200, CH],
  gate:            [2344, CY, 210, CH],
  // Abzweig-Schienen & Räume
  warehouse:       [1300, 500, 420, 210],
  queue:           [2088, 70, 200, 150],
  ausschuss:       [2360, 500, 270, 210],
  lab:             [560, 18, 300, 120],
  control_room:    [60, 500, 300, 160],
  backup_bot:      [420, 520, 250, 130],
};
const WORLD_W = 2680, WORLD_H = VIEW_H;
// Ketten-Maschinen sitzen AUF dem Band (verdecken die durchlaufende Kiste).
const CHAIN = ["docks","data_gate","catalyst_check","breaker","position_check",
  "signal_check","risk_check","position_limit","gate"];
// HUD-Instrumente (bildschirmfest in den Ecken, NICHT auf dem Hallenboden).
const HUD = { clock: [16, 14, 190, 84], weather: [VIEW_W-214, 14, 198, 92] };

const STATUS_COLOR = { ok:"neon_green", warn:"amber", err:"red", off:"border", active:"cobalt" };
function statusColor(s){ return P[STATUS_COLOR[s] || "border"]; }
function machine(id){ return (STATE.machines || {})[id]; }

function rrect(x,y,w,h,r){ ctx.beginPath(); ctx.moveTo(x+r,y); ctx.arcTo(x+w,y,x+w,y+h,r);
  ctx.arcTo(x+w,y+h,x,y+h,r); ctx.arcTo(x,y+h,x,y,r); ctx.arcTo(x,y,x+w,y,r); ctx.closePath(); }
function label(txt,x,y,col,size,align){ ctx.font=(size||15)+"px 'VT323','Courier New',monospace";
  ctx.textAlign=align||"center"; ctx.textBaseline="alphabetic"; ctx.fillStyle=col; ctx.fillText(txt,x,y); }

// ── Pixel-Engine — warme Artefakt-Palette + 5x7-Bitmap-Font + Helfer. ────────
const C = { floor:'#1b1811', text:'#eee7d8', muted:'#a89e8c',
  iron:'#4d535c', ironHi:'#727a85', ironDk:'#2b3037', ironMid:'#3c424a', steelHi:'#8b95a3',
  belt:'#2b2f36', beltRail:'#3f4650', tread:'#c87533',
  crate:'#a9773f', crateHi:'#c79457', crateDk:'#7a5228',
  brass:'#e0a24a', brassHi:'#f4cf8a', brassDk:'#8a6a2e', copper:'#c8804a',
  green:'#3ad16a', amber:'#ffb84d', red:'#ff5a45', screen:'#08140f', scr:'#57e08a', glass:'#0d1a22',
  brick:'#5a3b2b', brickHi:'#6f4a35', dome:'#123a4a', domeHi:'#2f7fa0', sweep:'#57e0c0',
  bot:'#5b6470', botDk:'#363d47', rack:'#524437', rackDk:'#332a20', desk:'#5a4636', wall:'#221e18' };
const FONT={
 'A':"01110/10001/10001/11111/10001/10001/10001",'B':"11110/10001/10001/11110/10001/10001/11110",
 'C':"01110/10001/10000/10000/10000/10001/01110",'D':"11100/10010/10001/10001/10001/10010/11100",
 'E':"11111/10000/10000/11110/10000/10000/11111",'F':"11111/10000/10000/11110/10000/10000/10000",
 'G':"01110/10001/10000/10111/10001/10001/01111",'H':"10001/10001/10001/11111/10001/10001/10001",
 'I':"01110/00100/00100/00100/00100/00100/01110",'J':"00111/00010/00010/00010/00010/10010/01100",
 'K':"10001/10010/10100/11000/10100/10010/10001",'L':"10000/10000/10000/10000/10000/10000/11111",
 'M':"10001/11011/10101/10101/10001/10001/10001",'N':"10001/11001/10101/10011/10001/10001/10001",
 'O':"01110/10001/10001/10001/10001/10001/01110",'P':"11110/10001/10001/11110/10000/10000/10000",
 'Q':"01110/10001/10001/10001/10101/10010/01101",'R':"11110/10001/10001/11110/10100/10010/10001",
 'S':"01111/10000/10000/01110/00001/00001/11110",'T':"11111/00100/00100/00100/00100/00100/00100",
 'U':"10001/10001/10001/10001/10001/10001/01110",'V':"10001/10001/10001/10001/10001/01010/00100",
 'W':"10001/10001/10001/10101/10101/11011/10001",'X':"10001/10001/01010/00100/01010/10001/10001",
 'Y':"10001/10001/01010/00100/00100/00100/00100",'Z':"11111/00001/00010/00100/01000/10000/11111",
 '0':"01110/10001/10011/10101/11001/10001/01110",'1':"00100/01100/00100/00100/00100/00100/01110",
 '2':"01110/10001/00001/00010/00100/01000/11111",'3':"11111/00010/00100/00010/00001/10001/01110",
 '4':"00010/00110/01010/10010/11111/00010/00010",'5':"11111/10000/11110/00001/00001/10001/01110",
 '6':"00110/01000/10000/11110/10001/10001/01110",'7':"11111/00001/00010/00100/01000/01000/01000",
 '8':"01110/10001/10001/01110/10001/10001/01110",'9':"01110/10001/10001/01111/00001/00010/01100",
 '-':"00000/00000/00000/11111/00000/00000/00000",'.':"000/000/000/000/000/000/011",'%':"11001/11010/00100/01011/10011/00000/00000",
 '/':"00001/00001/00010/00100/01000/10000/10000",' ':"000/000/000/000/000/000/000" };
function pha(hex,a){ const n=parseInt(hex.slice(1),16); return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`; }
function pr(x,y,w,h,c){ ctx.fillStyle=c; ctx.fillRect(x,y,w,h); }
function pdc(x,y,r,c){ ctx.fillStyle=c; ctx.beginPath(); ctx.arc(x,y,r,0,7); ctx.fill(); }
function _gl(ch){ return (FONT[ch]||FONT[' ']).split('/').map(r=>r.split('').map(Number)); }
function ptw(str,sc){ sc=sc||1; let w=0; [...String(str)].forEach((c,i)=>{ w+=(_gl(c)[0].length+(i?1:0)); }); return w*sc; }
function ptx(str,x,y,col,sc){ sc=sc||1; let cx=x; ctx.fillStyle=col;
  [...String(str)].forEach(ch=>{ const g=_gl(ch);
    for(let r=0;r<7;r++)for(let c=0;c<g[r].length;c++) if(g[r][c]) ctx.fillRect(cx+c*sc,y+r*sc,sc,sc);
    cx+=(g[0].length+1)*sc; }); }
function pbr(x0,y0,x1,y1,wd,col){ const a=Math.atan2(y1-y0,x1-x0),len=Math.hypot(x1-x0,y1-y0);
  ctx.save(); ctx.translate(x0,y0); ctx.rotate(a); ctx.fillStyle=col; ctx.fillRect(0,-wd/2,len,wd); ctx.restore(); }
const _bs={}; function bst(id,init){ if(!_bs[id]) _bs[id]=init(); return _bs[id]; }
let t = 0;
const paused = !!STATE.paused;

// ── bespoke Maschinen (zell-angepasst, an echten payload gebunden) ──────────
function bWarehouse(x,y,w,h,m,anim){
  const pos=(m.payload||{}).positions||{}, keys=Object.keys(pos);
  const cols=Math.max(4,Math.floor((w-16)/30)), sw=Math.floor((w-16)/cols);
  const rows=Math.max(3,Math.floor((h-40)/24)), sh=Math.floor((h-40)/rows);
  const rx=x+8, ry=y+8;
  pr(rx-3,ry-3,cols*sw+6,rows*sh+6,C.rackDk);
  for(let c=0;c<=cols;c++) pr(rx+c*sw-1,ry-3,2,rows*sh+6,C.rack);
  for(let r=0;r<=rows;r++) pr(rx-3,ry+r*sh-1,cols*sw+6,2,C.rack);
  keys.forEach((k,i)=>{ if(i>=cols*rows) return; const info=pos[k]||{}, rr=info.age_ratio;
    let cc=C.brass; if(rr!=null) cc = rr>=1?C.red : rr>=0.8?C.amber : C.green;
    const cx=rx+(i%cols)*sw+2, cy=ry+Math.floor(i/cols)*sh+2, cw=sw-4, ch=sh-4;
    pr(cx,cy,cw,ch,C.crate); pr(cx,cy,cw,2,C.crateHi); pr(cx,cy+ch-2,cw,2,C.crateDk);
    pr(cx+1,cy+ch-4,cw-2,2,pha(cc,0.95));
    const lw=ptw(k,1); if(lw<cw-2) ptx(k,cx+(cw-lw)/2,cy+2,'#3a2a12',1); });
  const railY=ry+rows*sh+6; pr(rx-3,railY,cols*sw+6,2,C.ironDk);
  const st=bst('wh',()=>({p:0})); if(anim) st.p+=0.02;
  const craneX=rx+(0.5+0.45*Math.sin(st.p))*(cols*sw);
  pr(craneX-2,y+4,4,railY-(y+4),C.ironMid); pr(craneX-4,railY-2,8,4,C.iron);
}
function bGate(x,y,w,h,m,anim){
  // Ausgangstor am rechten Ende: Backsteinwand + Rolltor auf Bandhöhe.
  const wallX=x+w-42;
  for(let yy=y+4;yy<y+h-6;yy+=8) for(let xx=wallX;xx<x+w;xx+=20){ pr(xx,yy,18,7,C.brick); pr(xx,yy,18,1,C.brickHi); }
  const oT=BELT_Y-26, oH=52; pr(wallX-2,oT-2,26,oH+4,C.ironDk); pr(wallX,oT,22,oH,'#0e0c0a');
  const open=(m.status==='ok')?(0.5+0.5*Math.sin(t*0.04)):0.12;
  for(let yy=oT+open*(oH-4); yy<oT+oH; yy+=5) pr(wallX,yy,22,4,C.iron);
  const lc=(m.status==='ok')?C.green:C.red; pdc(x+12,y+16,3,pha(lc,0.9)); ptx('IBKR',x+18,y+13,pha(C.muted,0.85),1);
}
function bBreaker(x,y,w,h,m,anim){
  // Gantry über dem Band: zwei Türme + Warnstreifen + Kipphebel + Verlust-Säule.
  const tripped=m.status==='err'; const topY=y+8, botY=BELT_Y-6;
  for(let xx=x+2;xx<x+w-2;xx+=10){ pr(xx,topY,5,4,C.amber); pr(xx+5,topY,5,4,'#241a08'); }
  pr(x+10,topY+6,8,botY-(topY+6),C.ironMid); pr(x+w-18,topY+6,8,botY-(topY+6),C.ironMid);
  const dp=Math.max(0,-((m.payload||{}).daily_pct||0)), fill=Math.min(1,dp/5), colH=botY-(topY+10);
  pr(x+11,topY+10+colH*(1-fill),6,colH*fill,tripped?C.red:C.amber);
  const la=tripped?0.95:0.12; pbr(x+w-14,topY+12,x+w-14-Math.cos(la)*16,topY+12+Math.sin(la)*16,4,tripped?C.red:C.green);
  if(tripped){ pr(x+w/2-1,topY+8,3,botY-(topY+8),pha(C.amber,0.8));
    if(anim) for(let k=0;k<4;k++){ const a=Math.random()*7; pdc(x+w-14+Math.cos(a)*6,topY+14+Math.sin(a)*6,1.4,C.red); } }
}
function bLab(x,y,w,h,m,anim){
  const st=bst('lab',()=>({s:0})); if(anim) st.s+=0.04;
  const dx=x+w*0.32, dy=y+h*0.5, dr=Math.min(w*0.28,h*0.42);
  ctx.fillStyle=C.dome; ctx.beginPath(); ctx.arc(dx,dy,dr,Math.PI,0); ctx.closePath(); ctx.fill();
  ctx.strokeStyle=pha(C.domeHi,0.35); ctx.lineWidth=1; for(let rr=dr-5;rr>4;rr-=6){ ctx.beginPath(); ctx.arc(dx,dy,rr,Math.PI,0); ctx.stroke(); }
  const a=Math.PI+(st.s%Math.PI); ctx.strokeStyle=pha(C.sweep,0.9); ctx.lineWidth=1.4;
  ctx.beginPath(); ctx.moveTo(dx,dy); ctx.lineTo(dx+Math.cos(a)*(dr-2),dy+Math.sin(a)*(dr-2)); ctx.stroke();
  ctx.strokeStyle=pha(C.domeHi,0.9); ctx.lineWidth=2; ctx.beginPath(); ctx.arc(dx,dy,dr,Math.PI,0); ctx.stroke();
  pdc(dx,dy,3,pha(C.sweep,0.6)); pr(dx-2,dy+2,4,h-(dy+2-y)-6,C.ironMid);
  const s=m.payload||{}; let wr=s.win_rate; if(wr==null && (s.wins||s.losses)) wr=s.wins/((s.wins||0)+(s.losses||0));
  const sx=x+w*0.58, sy=y+8, sw2=w*0.38, sh2=h-16; pr(sx,sy,sw2,sh2,C.screen);
  ptx('TREFFER',sx+3,sy+3,pha(C.scr,0.8),1); if(wr!=null) ptx(Math.round(wr*100)+'%',sx+3,sy+13,C.scr,2);
  const lb=s.labeled; if(lb!=null) ptx('N '+lb,sx+3,sy+sh2-9,pha(C.scr,0.7),1);
}
function bWeather(x,y,w,h,m,anim){
  pr(x,y,w,h,C.floor); const reg=((m.payload||{}).regime||'').toUpperCase();
  const val = reg==='BULL'?0.7 : reg==='NEUTRAL'?0 : reg?-0.7:0;
  const dx=x+h*0.5, dy=y+h*0.55, dr=h*0.36;
  pdc(dx,dy,dr+2,C.brassDk); pdc(dx,dy,dr,C.glass);
  ctx.strokeStyle=pha(C.brass,0.9); ctx.lineWidth=2; ctx.beginPath(); ctx.arc(dx,dy,dr,0,7); ctx.stroke();
  const na=Math.PI-((val+1)/2)*Math.PI, nc=val>0.33?C.green:val<-0.33?C.red:C.amber;
  pbr(dx,dy,dx+Math.cos(na)*(dr-4),dy-Math.sin(na)*(dr-4),2,nc); pdc(dx,dy,2,C.brassHi);
  const lbl=val>0.33?'RISK-ON':val<-0.33?'RISK-OFF':'NEUTRAL';
  ptx('KLIMA',x+h+2,y+10,pha(C.scr,0.8),1); ptx(lbl,x+h+2,y+20,nc,2);
}
function bBackup(x,y,w,h,m,anim){
  const ah=(m.payload||{}).age_hours, charge=ah!=null?Math.max(0,Math.min(1,1-ah/48)):0.5;
  const st=bst('bk',()=>({r:0})); if(anim) st.r+=0.1;
  const rx=x+16, ry=y+h-12; pdc(rx-6,ry,3,C.botDk); pdc(rx+6,ry,3,C.botDk);
  pr(rx-8,ry-18,16,15,C.bot); pr(rx-6,ry-26,12,9,C.bot); pdc(rx-2,ry-21,1.5,C.scr); pdc(rx+2,ry-21,1.5,C.scr);
  const dx=x+w*0.42, dy=y+8, dw=w*0.34, dh=h*0.48; pr(dx,dy,dw,dh,C.ironMid); pr(dx,dy,dw,2,C.steelHi);
  const ry2=dy+dh*0.5; [dx+dw*0.3,dx+dw*0.7].forEach(rc=>{ pdc(rc,ry2,dh*0.3,C.botDk);
    ctx.save(); ctx.translate(rc,ry2); ctx.rotate(st.r); for(let i=0;i<3;i++){ ctx.rotate(Math.PI*2/3); pr(-1,-dh*0.26,2,dh*0.52,pha(C.steelHi,0.8)); } ctx.restore(); pdc(rc,ry2,2,C.brass); });
  const bx=x+w*0.42, by=y+h-14, bw=w*0.42, bh=9, col=charge>0.5?C.green:charge>0.25?C.amber:C.red;
  ctx.strokeStyle=C.muted; ctx.lineWidth=1; ctx.strokeRect(bx,by,bw,bh); pr(bx+2,by+2,(bw-4)*charge,bh-4,pha(col,0.9));
}
function bControl(x,y,w,h,m,anim){
  pr(x,y,w,h,C.wall); const mw=(w-16)/3, my=y+6, mh=h*0.46;
  const titles=['PULS','CONF','STAT'];
  for(let i=0;i<3;i++){ const mx=x+6+i*mw; pr(mx,my,mw-4,mh,C.screen); ptx(titles[i],mx+2,my+2,pha(C.scr,0.8),1); }
  const st=bst('cr',()=>({o:0})); if(anim) st.o+=1;
  const px0=x+6, py0=my+mh-6; ctx.strokeStyle=pha(C.scr,0.9); ctx.lineWidth=1; ctx.beginPath();
  for(let i=0;i<20;i++){ const xx=px0+i/19*(mw-8), yy=py0-Math.sin(i*0.6+st.o*0.1)*mh*0.2; i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy);} ctx.stroke();
  ptx('SCHW 0.60',x+6+mw+2,my+12,C.scr,1);
  const bm=((m.payload||{}).broker_mode||'').toUpperCase(); if(bm) ptx(bm,x+6+mw+2,my+20,C.green,1);
  ptx('LAEUFT',x+6+2*mw+2,my+14,m.status==='ok'?C.green:C.amber,1);
  pr(x+6,y+h*0.6,w-12,h*0.3,C.desk); pr(x+6,y+h*0.6,w-12,2,pha('#ffffff',0.12));
  for(let i=0;i<3;i++) pdc(x+22+i*22,y+h*0.78,5,C.ironDk);
  for(let i=0;i<4;i++){ const lit=((t*0.04|0)+i)%5<2; pr(x+w-58+i*13,y+h*0.72,8,8,lit?pha([C.green,C.amber,C.red,C.green][i],0.9):C.ironDk); }
}
function bClock(x,y,w,h,m,anim){
  pr(x,y,w,h,C.wall);
  const scr_x=x+w*0.12, scr_w=w*0.76, scr_y=y+6, scr_h=h*0.52; pr(scr_x,scr_y,scr_w,scr_h,C.screen);
  const now=new Date(); const hh=String(now.getHours()).padStart(2,'0'), mm=String(now.getMinutes()).padStart(2,'0');
  const sc=3, tw=ptw(hh,sc)+8+ptw(mm,sc); let cx=x+w/2-tw/2, cy=scr_y+scr_h/2-sc*3.5;
  ptx(hh,cx,cy,C.scr,sc); cx+=ptw(hh,sc)+2;
  if(now.getSeconds()%2===0){ pr(cx+1,cy+sc*1.5,sc,sc,C.scr); pr(cx+1,cy+sc*4,sc,sc,C.scr); } cx+=8;
  ptx(mm,cx,cy,C.scr,sc);
  const s=m.payload||{}; const ph=(s.phase||s.state||'').toString().toUpperCase().slice(0,18);
  if(ph) ptx(ph, x+w/2-ptw(ph,1)/2, y+h-13, pha(C.amber,0.85),1);
}
function bAnalyzerClaude(x,y,w,h,m,anim){
  // Hightech-Schrank auf dem UNTEREN Band (lowerY), kein Hintergrund-Kasten.
  const pl=m.payload||{}; const st=bst('acl',()=>({s:0})); if(anim) st.s+=0.02;
  const bx=x+24, bw=w-48, top=lowerY-70, baseY=lowerY+16, bh=baseY-top;
  pr(bx-4,baseY,bw+8,8,C.ironDk); pr(bx-4,baseY,bw+8,2,C.ironMid);       // Sockel
  pr(bx,top,bw,bh,'#20262f'); pr(bx,top,bw,3,C.steelHi); pr(bx,top,3,bh,C.brass);
  const gx=bx+8,gy=top+8,gw=bw*0.44,gh=bh*0.5; pr(gx,gy,gw,gh,C.glass);
  const bxp=gx+(0.5+0.5*Math.sin(st.s))*(gw-4); ctx.strokeStyle=pha(C.sweep,0.85); ctx.lineWidth=2;
  ctx.beginPath(); ctx.moveTo(bxp,gy); ctx.lineTo(bxp,gy+gh); ctx.stroke(); pdc(gx+gw/2,gy+gh/2,3,pha(C.sweep,0.5));
  const tx=gx+gw+6, tw2=bw-(tx-bx)-6; pr(tx,gy,tw2,gh,C.screen);
  ['STIM','KAT','MAK','TEC','RIS'].forEach((p,i)=>{ const py=gy+3+i*((gh-4)/5); ptx(p,tx+2,py,pha(C.scr,0.6),1);
    const fill=(Math.sin(st.s*3+i)+1)/2; pr(tx+22,py,(tw2-24)*fill,2,pha(C.scr,0.6)); });
  ptx('CLAUDE '+(pl.n||0)+'/'+(pl.total||0), bx, top-4, pha(C.brassHi,0.85),1);
}
function bAnalyzerOllama(x,y,w,h,m,anim){
  // Gusseisen-Ofen/Werkbank auf dem OBEREN Band (upperY), kein Hintergrund-Kasten.
  const pl=m.payload||{}, share=pl.total?(pl.n||0)/pl.total:0;
  const bx=x+24, bw=w-48, top=y+16, baseY=upperY+16, bh=baseY-top;
  pr(bx-4,baseY,bw+8,8,C.ironDk); pr(bx-4,baseY,bw+8,2,C.ironMid);       // Sockel
  pr(bx,top,bw,bh,'#2a1a12'); pr(bx,top,bw,3,'#3a2a1a'); pr(bx,top,3,bh,'#3a2a1a');
  const glow=0.5+0.5*Math.abs(Math.sin(t*0.1)); pr(bx+8,baseY-14,bw*0.4,8,pha(C.amber,0.4+0.4*glow));
  pdc(bx+8+bw*0.2,baseY-10,4,pha(C.red,0.5+0.4*glow));
  const chx=bx+bw-16; pr(chx,top-16,8,18,'#2a1a12');                     // Schornstein
  const puffs=1+Math.floor(share*4); for(let i=0;i<puffs;i++){ const ph=(t*0.6+i*30)%54; pdc(chx+4,top-16-ph*0.6,3,pha(C.muted,0.4*(1-ph/54))); }
  const wx=bx+bw*0.5, wy=top+bh*0.44, wr=Math.min(bw*0.13,bh*0.3); const st=bst('aol',()=>({r:0})); if(anim) st.r+=0.12;
  pdc(wx,wy,wr,'#2a2018'); ctx.save(); ctx.translate(wx,wy); ctx.rotate(st.r); for(let i=0;i<6;i++){ ctx.rotate(Math.PI/3); pr(-1,-wr,2,wr,pha('#6a5030',0.9)); } ctx.restore(); pdc(wx,wy,2,C.brass);
  ptx('OLLAMA '+(pl.n||0)+'/'+(pl.total||0), bx, top-4, pha('#E09A5A',0.85),1);
}
function bDocks(x,y,w,h,m,anim){
  // Wareneingang: Quellen-LKWs docken an, Sammelschiene rechts führt aufs Band.
  const pl=m.payload||{};
  const slots=[].concat((pl.healthy||[]).map(n=>[n,C.green]),(pl.weak||[]).map(n=>[n,C.amber]),(pl.dead||[]).map(n=>[n,C.ironDk]));
  pr(x+w-10,y+6,6,h-12,C.ironMid); pr(x+w-10,y+6,2,h-12,C.steelHi);      // Sammelschiene
  const maxN=Math.max(1,Math.floor((h-16)/18)); const bayH=Math.min(26,(h-16)/Math.max(1,Math.min(slots.length,maxN)));
  slots.slice(0,maxN).forEach(([n,c],i)=>{ const by=y+8+i*bayH;
    const tw=Math.min(40,w*0.26); pr(x+w-10-tw,by+2,tw,bayH-6,c); pr(x+w-10-tw,by+2,tw,2,pha('#ffffff',0.25));  // LKW
    pr(x+w-10-tw-7,by+4,7,bayH-8,pha(c,0.65));                                                                  // Fahrerhaus
    ptx(String(n).toUpperCase().slice(0,13), x+4, by+3, pha(C.text,0.85),1); });
  if(slots.length>maxN) ptx('+'+(slots.length-maxN),x+4,y+h-12,pha(C.muted,0.7),1);
}
function bRisk(x,y,w,h,m,anim){
  // Inspektions-Gantry über dem Band: Warnstreifen + 3 Messuhren.
  const rej=(m.payload||{}).rejected||0; const topY=y+8, botY=BELT_Y-6;
  for(let xx=x+2;xx<x+w-2;xx+=10){ pr(xx,topY,5,3,C.amber); pr(xx+5,topY,5,3,'#241a08'); }
  pr(x+8,topY+5,5,botY-(topY+5),C.ironMid); pr(x+w-13,topY+5,5,botY-(topY+5),C.ironMid); pr(x+8,topY+3,w-16,4,C.iron);
  for(let i=0;i<3;i++){ const dx=x+w*0.26+i*(w*0.24), dy=y+h*0.34, dr=Math.min(w*0.1,h*0.12);
    pdc(dx,dy,dr,C.glass); const hot=(i===2 && rej>0);
    const a=Math.PI*1.15-(hot?0.85:0.35)*Math.PI*1.3; ctx.strokeStyle=hot?C.red:C.green; ctx.lineWidth=1.4;
    ctx.beginPath(); ctx.moveTo(dx,dy); ctx.lineTo(dx+Math.cos(a)*dr*0.8,dy-Math.sin(a)*dr*0.8); ctx.stroke(); pdc(dx,dy,1.5,C.brass); }
  ptx('ABGEW '+rej, x+8, BELT_Y+16, pha(rej>0?C.amber:C.muted,0.85),1);
}
function bPositionLimit(x,y,w,h,m,anim){
  // Zähl-Drehkreuz überspannt das Band + Anzeige.
  const pl=m.payload||{}, open=pl.open||0, full=(pl.full_hits||0)>0;
  const cx=x+w*0.34, cy=BELT_Y, st=bst('pl',()=>({r:0})); if(anim && !full) st.r+=0.05;
  pr(cx-20,y+16,5,BELT_Y-(y+16),C.ironMid); pr(cx+16,y+16,5,BELT_Y-(y+16),C.ironMid);
  for(let i=0;i<3;i++){ const a=st.r+i*Math.PI*2/3; pbr(cx,cy,cx+Math.cos(a)*15,cy+Math.sin(a)*7,3,full?C.red:C.brass); }
  pdc(cx,cy,3,C.ironDk);
  if(full){ pr(cx-20,y+16,41,4,C.red); }
  const sx=x+w*0.58,sy=y+12,sw=w*0.38,sh=BELT_Y-26-sy; pr(sx,sy,sw,sh,C.screen);
  ptx('OFFEN',sx+3,sy+3,pha(C.scr,0.8),1); ptx(String(open),sx+3,sy+12,full?C.red:C.green,2);
  ptx(full?'VOLL':'FREI',sx+3,sy+sh-9,full?C.red:pha(C.scr,0.7),1);
}
function bAusschuss(x,y,w,h,m,anim){
  const rej=(m.payload||{}).rejected||0;
  ctx.fillStyle='#4a5058'; ctx.beginPath(); ctx.moveTo(x+w*0.3,y+8); ctx.lineTo(x+w*0.7,y+8);
  ctx.lineTo(x+w*0.56,y+h*0.42); ctx.lineTo(x+w*0.44,y+h*0.42); ctx.closePath(); ctx.fill();
  const bl=x+w*0.28, br=x+w*0.72, bt=y+h*0.46, bb=y+h-12;
  pr(bl-3,bt,3,bb-bt,'#2c3138'); pr(br,bt,3,bb-bt,'#2c3138'); pr(bl-3,bb,br-bl+6,3,'#2c3138');
  const cw=(br-bl)/4-2, per=4; const n=Math.min(rej,per*4);
  for(let i=0;i<n;i++){ const col=i%per; pr(bl+col*(cw+2)+1, bb-4-Math.floor(i/per)*7-6, cw, 6, C.crate); }
  ptx('HEUTE '+rej, x+6, y+8, pha(C.amber,0.8),1);
}
function bQueue(x,y,w,h,m,anim){
  const pl=m.payload||{}, waiting=pl.waiting||[], n=pl.count!=null?pl.count:waiting.length;
  const by=y+h*0.52, barX=x+w-26; pr(x+4,by,barX-x,10,C.belt); pr(x+4,by-2,barX-x,2,C.beltRail);
  pr(barX,y+8,3,by-y,C.ironMid); pbr(barX,y+11,barX+16,y+(n>0?11:5),3,C.amber);
  pdc(barX+20,y+14,3,pha(n>0?C.red:C.green,0.9));
  waiting.slice(0,6).forEach((tk,i)=>{ const cxp=barX-20-i*20; if(cxp<x+4) return;
    pr(cxp,by-12,16,12,C.crate); pr(cxp,by-12,16,2,C.crateHi);
    const s=String(tk), lw=ptw(s,1); if(lw<15) ptx(s,cxp+(16-lw)/2,by-9,'#3a2a12',1); });
  ptx('WARTEND '+n, x+6, y+8, pha(n>0?C.amber:C.muted,0.85),1);
}
function bDataGate(x,y,w,h,m,anim){
  // Scanner-Portal überm Band (kein eigenes Band, kein Hintergrund).
  const rej=(m.payload||{}).rejected||0;
  const sx=x+w*0.2, sw=w*0.6, sy=y+16;
  pr(sx-4,sy,4,BELT_Y-sy,C.ironMid); pr(sx+sw,sy,4,BELT_Y-sy,C.ironMid);
  pr(sx-4,sy,sw+8,6,C.iron); pr(sx-4,sy,sw+8,2,C.steelHi);
  const sh=(BELT_Y-18)-(sy+10); pr(sx,sy+8,sw,sh,C.ironDk);
  const scanX=sx+((t*1.4)%(sw-4)); ctx.strokeStyle=pha(C.sweep,0.8); ctx.lineWidth=2;
  ctx.beginPath(); ctx.moveTo(scanX,sy+10); ctx.lineTo(scanX,BELT_Y-16); ctx.stroke();
  const ok=rej===0; pdc(sx+sw-8,sy+12,4,pha(ok?C.green:C.red,0.9));
  ptx('DATEN', sx+3, sy+2, pha(C.scr,0.8),1);
  ptx('ABGEW '+rej, x+8, BELT_Y+16, pha(rej>0?C.amber:C.muted,0.85),1);
}
function bCatalyst(x,y,w,h,m,anim){
  // Weichen-Turm am Split-Punkt (auf dem Band), kein Hintergrund-Kasten.
  const pl=m.payload||{}, cn=pl.claude_n||0, on=pl.ollama_n||0, up=on>=cn;
  const cx=x+w/2;
  pr(cx-8,y+24,16,BELT_Y-(y+24),C.ironMid); pr(cx-8,y+24,4,BELT_Y-(y+24),C.ironHi);   // Post
  pr(cx-15,y+6,30,20,C.ironDk); pr(cx-15,y+6,30,2,C.ironMid);                            // Signalkopf
  pdc(cx-7,y+16,3,pha(up?C.green:C.ironDk,0.9)); pdc(cx+7,y+16,3,pha(!up?C.green:C.ironDk,0.9));
  ptx('WEICHE', cx-ptw('WEICHE',1)/2, y-3, pha(C.amber,0.75),1);
  const ang = up? -0.62 : 0.62;                                                          // Zunge zur aktiven Spur
  pbr(cx,BELT_Y, cx+Math.cos(ang)*26, BELT_Y+Math.sin(ang)*26, 5, C.brass);
  pdc(cx,BELT_Y,4,C.brassDk); pdc(cx,BELT_Y,2,C.brassHi);
  ptx('OLL '+on, cx-ptw('OLL '+on,1)-8, BELT_Y-24, pha(C.brassHi,0.85),1);
  ptx('CLA '+cn, cx+8, BELT_Y+18, pha(C.copper,0.85),1);
}
function bPositionCheck(x,y,w,h,m,anim){
  // Kontroll-Pult überm Band mit Depot-Screen + Prüfkopf (kein eigenes Band).
  const held=(m.payload||{}).held||[];
  const sx=x+10, sy=y+10, sw=w-20, sh=BELT_Y-26-sy; pr(sx,sy,sw,sh,C.screen); pr(sx,sy,sw,2,C.steelHi);
  ptx('DEPOT', sx+3, sy+2, pha(C.scr,0.8),1);
  held.slice(0,6).forEach((tk,i)=>{ ptx(String(tk).slice(0,6), sx+3+(i%3)*((sw-6)/3), sy+11+Math.floor(i/3)*9, pha(C.scr,0.75),1); });
  ptx('N '+held.length, sx+sw-ptw('N '+held.length,1)-3, sy+2, pha(C.amber,0.8),1);
  pr(x+w/2-2,sy+sh,4,BELT_Y-12-(sy+sh),C.ironDk); pr(x+w/2-6,BELT_Y-16,12,8,C.ironMid);
  pdc(x+w/2,BELT_Y-6,2,pha(C.green,0.9));
}
function bSignalCheck(x,y,w,h,m,anim){
  // Balkenwaage von oben überm Band (kein eigenes Band, kein Hintergrund).
  const rej=(m.payload||{}).rejected||0;
  const fx=x+w*0.5, fy=y+18; pr(fx-2,y+6,4,fy-(y+6),C.ironMid);
  const tilt = rej>0 ? 0.26 : -0.22; const arm=w*0.3;
  const lx=fx-Math.cos(tilt)*arm, ly=fy-Math.sin(tilt)*arm, rx=fx+Math.cos(tilt)*arm, ry=fy+Math.sin(tilt)*arm;
  pbr(lx,ly,rx,ry,4,C.brass); pdc(fx,fy,3,C.brassDk);
  pdc(lx,ly+12,5,pha(rej>0?C.amber:C.green,0.9)); pr(rx-6,ry+10,12,10,C.ironMid);
  ptx('0.60', rx-ptw('0.60',1)/2, ry+12, pha(C.brassHi,0.85),1);
  ptx('SCHWACH '+rej, x+8, BELT_Y+16, pha(rej>0?C.amber:C.muted,0.85),1);
}
const BESPOKE = { warehouse:bWarehouse, gate:bGate, breaker:bBreaker, lab:bLab,
  weather:bWeather, backup_bot:bBackup, control_room:bControl, clock:bClock,
  analyzer_claude:bAnalyzerClaude, analyzer_ollama:bAnalyzerOllama, docks:bDocks,
  risk_check:bRisk, position_limit:bPositionLimit, ausschuss:bAusschuss, queue:bQueue,
  data_gate:bDataGate, catalyst_check:bCatalyst, position_check:bPositionCheck, signal_check:bSignalCheck };

// ── Förderband-System: ein durchgehendes Band, Kisten fließen von links nach
// rechts durch die Kette; die Ketten-Boxen (CHAIN) werden NACH dem Band
// gezeichnet und verdecken die Kiste beim Durchlauf (rein → raus). ──────────
const BELT_X0 = 16, BELT_X1 = 2596, BBH = 22;
// Analyse-Raute: das Band teilt sich an der Weiche in ZWEI Bänder (Ollama
// oben / Claude unten), beide laufen durch ihren Analysator und werden danach
// wieder zusammengeführt — kein Band endet im Nichts.
const SPLIT_X = 700, MERGE_X = 1050, LANE = 80, RAMP = 54;
const upperY = BELT_Y - LANE, lowerY = BELT_Y + LANE;
const mainCrates = []; let spawnT = 0;
// Zweig-Kisten folgen einer Polylinie (Ablehnung → Ausschuss, voll → Queue).
const branchCrates = []; let branchT = 0;
function bcLen(pts){ let s=0; for(let i=0;i<pts.length-1;i++) s+=Math.hypot(pts[i+1][0]-pts[i][0],pts[i+1][1]-pts[i][1]); return s; }
function bcPos(bc){ let d=bc.d; for(let i=0;i<bc.pts.length-1;i++){ const a=bc.pts[i],b=bc.pts[i+1],L=Math.hypot(b[0]-a[0],b[1]-a[1]);
  if(d<=L){ const f=L?d/L:0; return [a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f]; } d-=L; } const e=bc.pts[bc.pts.length-1]; return [e[0],e[1]]; }
function beltActive(){ const c=machine('conveyor'); return !paused && (!c || ["ok","active"].includes(c.status)); }
function ollamaShare(){ const c=machine('catalyst_check'); if(c&&c.payload){ const o=c.payload.ollama_n||0, cl=c.payload.claude_n||0; return (o+cl)? o/(o+cl):0.6; } return 0.6; }
function drawBeltH(x0,x1,cyy,hh){
  pr(x0,cyy-hh/2-2,x1-x0,2,C.beltRail); pr(x0,cyy-hh/2,x1-x0,hh,C.belt); pr(x0,cyy+hh/2,x1-x0,2,C.beltRail);
  if(beltActive()){ ctx.fillStyle=C.tread; const off=(t*1.4)%16;
    for(let x=x0-16+off;x<x1;x+=16){ ctx.fillRect(x,cyy-hh/2+3,2,2); ctx.fillRect(x+4,cyy+hh/2-5,2,2);} }
}
function drawBeltV(cxx,y0,y1,ww){
  pr(cxx-ww/2-2,y0,2,y1-y0,C.beltRail); pr(cxx-ww/2,y0,ww,y1-y0,C.belt); pr(cxx+ww/2,y0,2,y1-y0,C.beltRail);
}
function drawBeltDiag(x0,y0,x1,y1,hh){ pbr(x0,y0,x1,y1,hh+4,C.beltRail); pbr(x0,y0,x1,y1,hh,C.belt); }
// y der Kiste je Spur (0=Mitte): rampt an Split/Merge hoch/runter, sonst Spur.
function crateY(x,lane){ if(!lane||x<=SPLIT_X||x>=MERGE_X) return BELT_Y;
  const ly = lane>0?upperY:lowerY;
  if(x<SPLIT_X+RAMP) return BELT_Y + (ly-BELT_Y)*((x-SPLIT_X)/RAMP);
  if(x>MERGE_X-RAMP) return ly + (BELT_Y-ly)*((x-(MERGE_X-RAMP))/RAMP);
  return ly; }
function drawBelts(){
  // Haupt-Band VOR und NACH der Analyse-Raute
  drawBeltH(BELT_X0, SPLIT_X, BELT_Y, BBH);
  drawBeltH(MERGE_X, BELT_X1, BELT_Y, BBH);
  // Zwei-Band-Raute: Rampen hoch/runter, zwei Spuren, Rampen zurück
  drawBeltDiag(SPLIT_X,BELT_Y, SPLIT_X+RAMP,upperY, BBH-4);
  drawBeltDiag(SPLIT_X,BELT_Y, SPLIT_X+RAMP,lowerY, BBH-4);
  drawBeltH(SPLIT_X+RAMP-2, MERGE_X-RAMP+2, upperY, BBH-4);
  drawBeltH(SPLIT_X+RAMP-2, MERGE_X-RAMP+2, lowerY, BBH-4);
  drawBeltDiag(MERGE_X-RAMP,upperY, MERGE_X,BELT_Y, BBH-4);
  drawBeltDiag(MERGE_X-RAMP,lowerY, MERGE_X,BELT_Y, BBH-4);
  // Abzweige: Lager (unter Bestands-Prüfung), Warteschlange (über Limit),
  // Ausschuss-Schiene (unter der Kette).
  drawBeltV(1420, 415, 500, 12);
  drawBeltV(2188, 220, 265, 12);
  drawBeltH(300, 2470, 452, 12); drawBeltV(2470, 452, 500, 12);
  // Reject-Fallrohre der Prüf-Stationen aufs Ausschuss-Band
  drawBeltV(396, BELT_Y+8, 452, 10); drawBeltV(1676, BELT_Y+8, 452, 10); drawBeltV(1932, BELT_Y+8, 452, 10);
  // laufende Kisten; Spur an der Weiche nach Routing-Anteil gewählt
  if(beltActive()){ spawnT+=1; if(spawnT>72 && mainCrates.length<16){ spawnT=0;
    mainCrates.push({x:BELT_X0, tk:TICK[(Math.random()*TICK.length)|0], lane:(Math.random()<ollamaShare()?1:-1)}); }
    mainCrates.forEach(c=>c.x+=1.4); }
  for(let i=mainCrates.length-1;i>=0;i--){ if(mainCrates[i].x>BELT_X1-4) mainCrates.splice(i,1); }
  mainCrates.forEach(c=>{ const cx=c.x, cyy=crateY(c.x,c.lane);
    pr(cx-9,cyy-8,18,15,C.crate); pr(cx-9,cyy-8,18,2,C.crateHi); pr(cx-9,cyy+5,18,2,C.crateDk);
    const lw=ptw(c.tk,1); pr(cx-lw/2-1,cyy-5,lw+2,8,'#f2e2c0'); ptx(c.tk,cx-lw/2,cyy-4,'#3a2a12',1); });
  // Zweig-Kisten: beide abgehenden Bänder werden weitergeführt.
  if(beltActive()){ branchT++;
    if(branchT>66){ branchT=0;
      const src=[]; const dg=machine('data_gate'), sc=machine('signal_check'), rc=machine('risk_check');
      if(dg&&dg.payload&&dg.payload.rejected>0) src.push(396);
      if(sc&&sc.payload&&sc.payload.rejected>0) src.push(1676);
      if(rc&&rc.payload&&rc.payload.rejected>0) src.push(1932);
      if(src.length){ const cx=src[(Math.random()*src.length)|0]; const pts=[[cx,BELT_Y+8],[cx,452],[2470,452],[2470,522]];
        branchCrates.push({pts,d:0,len:bcLen(pts),tk:TICK[(Math.random()*TICK.length)|0],reject:true}); }
      const plm=machine('position_limit'); if(plm&&plm.payload&&plm.payload.full_hits>0){
        const pts=[[2188,BELT_Y-8],[2188,236]]; branchCrates.push({pts,d:0,len:bcLen(pts),tk:TICK[(Math.random()*TICK.length)|0],reject:false}); }
    }
    branchCrates.forEach(c=>c.d+=1.3);
  }
  for(let i=branchCrates.length-1;i>=0;i--){ if(branchCrates[i].d>=branchCrates[i].len) branchCrates.splice(i,1); }
  branchCrates.forEach(c=>{ const p=bcPos(c);
    pr(p[0]-8,p[1]-7,16,13,C.crate); pr(p[0]-8,p[1]-7,16,2,C.crateHi);
    if(c.reject) pr(p[0]+3,p[1]-9,5,4,pha(C.red,0.9)); else pr(p[0]+3,p[1]-9,5,4,pha(C.amber,0.9)); });
  // Durchsatz-Zählwerk
  const cv=machine('conveyor'); const tot=cv&&cv.payload?cv.payload.total:null;
  if(tot!=null){ const bx=44, byy=196;
    ptx('ENTSCHEIDUNGEN HEUTE', bx, byy-2, pha(C.amber,0.7),1);
    pr(bx-2,byy+8,60,22,C.ironDk); pr(bx,byy+10,56,18,'#0A0C0F');
    ptx(String(Math.min(tot,999)).padStart(3,'0'), bx+15, byy+15, C.scr,2); }
}
const TICK=['AAPL','MSFT','NVDA','TSLA','AMZN','META','GOOG','AMD','SAP','NFLX'];

// ── Maschinen-Box ────────────────────────────────────────────────────────────
// Muster-Umbau: diese Maschinen stehen als echte Objekte OHNE Hintergrund-Kasten
// auf dem Hallenboden (Stufe 5). Werden schrittweise auf alle ausgeweitet.
const NO_BOX = new Set(["docks","data_gate","catalyst_check","analyzer_ollama","analyzer_claude",
  "breaker","position_check","signal_check","risk_check","position_limit","gate",
  "warehouse","ausschuss","queue","lab","backup_bot"]);
  // control_room bleibt Raum (Wände), clock/weather sind Ecken-HUD.
function drawBox(id){
  const r=LAYOUT[id]; if(!r) return; const m=machine(id); if(!m) return;
  const [x,y,w,h]=r; const col=statusColor(m.status); const animate=!paused;
  const bfn=BESPOKE[id];
  if(NO_BOX.has(id)){
    // Boden-Schatten unter dem Maschinen-Objekt, kein Kasten/Rahmen
    ctx.save(); ctx.globalAlpha=0.30; ctx.fillStyle="#000";
    ctx.beginPath(); ctx.ellipse(x+w/2, y+h-6, w*0.38, 9, 0,0,7); ctx.fill(); ctx.restore();
    try{ if(bfn) bfn(x,y,w,h,m,animate); }catch(e){}
  } else {
    ctx.save(); rrect(x,y,w,h,4); ctx.clip();
    try{ if(bfn) bfn(x,y,w,h,m,animate); else { pr(x,y,w,h,P.bg_panel); } }
    catch(e){ pr(x,y,w,h,P.bg_panel); }
    ctx.restore();
    ctx.strokeStyle=P.border; ctx.lineWidth=1.5; rrect(x,y,w,h,4); ctx.stroke();
  }
  let ledA=1; if(animate && (m.status==="warn"||m.status==="err")) ledA=0.4+0.6*Math.abs(Math.sin(t*0.12));
  ctx.fillStyle=col; ctx.globalAlpha=ledA; ctx.beginPath(); ctx.arc(x+w-14,y+13,6,0,7); ctx.fill(); ctx.globalAlpha=1;
  label(m.label||id, x+w/2+1, y+h-8, "rgba(0,0,0,0.6)", 14, "center");
  label(m.label||id, x+w/2, y+h-9, P.text_muted, 14, "center");
}

// ── Ereignis-Requisiten (an echte Zustände gebunden) ────────────────────────
function drawEvents(){
  const ev=STATE.events||{};
  const br=machine("breaker");
  if(br && br.status==="err"){ const [bx,by,bw]=LAYOUT.breaker; const cx=bx+bw/2,cy=by-14;
    ctx.fillStyle=P.red; ctx.globalAlpha=0.4+0.6*Math.abs(Math.sin(t*0.12));
    ctx.beginPath(); ctx.arc(cx,cy,8,0,7); ctx.fill(); ctx.globalAlpha=1;
    label("NOT-AUS", cx, cy-12, P.red, 13, "center"); }
  if(ev.first_live_trade){ const [gx,gy,gw]=LAYOUT.gate;
    ctx.fillStyle="gold"; ctx.beginPath(); ctx.moveTo(gx+gw/2,gy-22); ctx.lineTo(gx+gw/2-13,gy-8);
    ctx.lineTo(gx+gw/2+13,gy-8); ctx.closePath(); ctx.fill(); }
  if(ev.thesis_proven){ const [lx,ly,lw]=LAYOUT.lab; ctx.fillStyle="gold";
    ctx.beginPath(); ctx.arc(lx+lw/2,ly-10,8,0,7); ctx.fill(); }
}

// ── Halle (großer Backstein-Raum) ────────────────────────────────────────────
let brickPattern=null;
function makeBrickPattern(){
  const c=document.createElement("canvas"); c.width=40; c.height=20; const g=c.getContext("2d");
  g.fillStyle=P.brick; g.fillRect(0,0,40,20); g.strokeStyle=P.border; g.lineWidth=1.5;
  g.strokeRect(0,0,40,20); g.beginPath(); g.moveTo(20,0);g.lineTo(20,10); g.moveTo(0,10);g.lineTo(40,10);
  g.moveTo(20,10);g.lineTo(20,20); g.stroke(); return ctx.createPattern(c,"repeat");
}
function drawHall(){
  ctx.fillStyle=P.grass; ctx.fillRect(0,0,WORLD_W,WORLD_H);
  if(!brickPattern) brickPattern=makeBrickPattern();
  ctx.fillStyle=brickPattern; ctx.fillRect(10,10,WORLD_W-20,WORLD_H-20);
  ctx.strokeStyle=P.border; ctx.lineWidth=3; ctx.strokeRect(10,10,WORLD_W-20,WORLD_H-20);
  // Tore (Lücken links = Wareneingang, rechts = Warenausgang) auf Band-Höhe
  ctx.fillStyle=P.bg; ctx.fillRect(6,BELT_Y-30,10,60); ctx.fillRect(WORLD_W-16,BELT_Y-30,10,60);
  label("WARENEINGANG", 90, 30, pha(P.copper_hi,0.8), 15, "left");
  label("WARENAUSGANG →", WORLD_W-90, 30, pha(P.copper_hi,0.8), 15, "right");
}

// ── HUD (Uhr + Wetter, bildschirmfest in den Ecken) ─────────────────────────
function drawHUD(){
  for(const id in HUD){ const m=machine(id); if(!m) continue; const [x,y,w,h]=HUD[id];
    ctx.save(); rrect(x,y,w,h,4); ctx.clip();
    try{ BESPOKE[id](x,y,w,h,m,!paused); }catch(e){ pr(x,y,w,h,P.bg_panel); }
    ctx.restore(); ctx.strokeStyle=P.border; ctx.lineWidth=1.5; rrect(x,y,w,h,4); ctx.stroke(); }
  // Regen überm Wetter-HUD bei erhöhter Nachfrage
  if(STATE.weather_demand_label==="ELEVATED"){ const [x,y,w,h]=HUD.weather;
    for(let i=0;i<5;i++){ const dx=x+20+i*(w-40)/4, ph=(t*2+i*7)%18;
      ctx.strokeStyle=pha(P.cobalt_hi,0.8); ctx.lineWidth=2; ctx.beginPath();
      ctx.moveTo(dx,y+h+2+ph); ctx.lineTo(dx-5,y+h+12+ph); ctx.stroke(); } }
  // Schwenk-Hinweis
  if(WORLD_W>VIEW_W){ label("↔ ziehen zum Schwenken", VIEW_W/2, VIEW_H-8, pha(P.text_muted,0.6), 13, "center"); }
}

// ── Kamera (horizontaler Schwenk) ───────────────────────────────────────────
let camX = 0; const camMax = Math.max(0, WORLD_W - VIEW_W);
let dragging=false, dragMoved=0, lastPX=0;
function viewScale(){ const r=canvas.getBoundingClientRect(); return r.width? VIEW_W/r.width : 1; }
canvas.addEventListener("pointerdown",(e)=>{ dragging=true; dragMoved=0; lastPX=e.clientX; canvas.setPointerCapture(e.pointerId); });
canvas.addEventListener("pointermove",(e)=>{ if(!dragging) return; const s=viewScale();
  const dx=(e.clientX-lastPX)*s; lastPX=e.clientX; dragMoved+=Math.abs(dx);
  camX=Math.max(0,Math.min(camMax, camX-dx)); });
canvas.addEventListener("pointerup",(e)=>{ dragging=false;
  if(dragMoved<5){ handleClick(e); } });
canvas.addEventListener("wheel",(e)=>{ camX=Math.max(0,Math.min(camMax, camX+(e.deltaY+e.deltaX))); e.preventDefault(); }, {passive:false});
canvas.style.cursor="grab";
function handleClick(e){
  const rect=canvas.getBoundingClientRect(); const s=viewScale();
  const vx=(e.clientX-rect.left)*s, vy=(e.clientY-rect.top)*s;
  // HUD zuerst (bildschirmfest)
  let hit=null;
  for(const id in HUD){ const [x,y,w,h]=HUD[id]; if(vx>=x&&vx<=x+w&&vy>=y&&vy<=y+h){ hit=id; break; } }
  if(!hit){ const wx=vx+camX, wy=vy; for(const id in LAYOUT){ const [x,y,w,h]=LAYOUT[id];
    if(wx>=x&&wx<=x+w&&wy>=y&&wy<=y+h){ hit=id; break; } }
    // Klick aufs freie Band → Förderband-Detail (conveyor ist das Band selbst)
    if(!hit && wy>=BELT_Y-BBH && wy<=BELT_Y+BBH && wx>=BELT_X0 && wx<=BELT_X1) hit="conveyor"; }
  if(!hit) return;
  try{ const base=((document.referrer||"").split("?")[0]) || (window.top&&window.top.location.href.split("?")[0]);
    if(base) window.top.location.href = base + "?factory=" + encodeURIComponent(hit); }catch(err){}
}

// ── Render-Schleife ─────────────────────────────────────────────────────────
const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function frame(){
  ctx.clearRect(0,0,VIEW_W,VIEW_H);
  ctx.save(); ctx.translate(-camX,0);           // Welt (geschwenkt)
  drawHall();
  drawBelts();
  for(const id in LAYOUT){ if(!CHAIN.includes(id)) drawBox(id); }   // Abzweige/Räume
  for(const id of CHAIN) drawBox(id);                                // Ketten-Boxen verdecken die Kiste
  drawEvents();
  ctx.restore();
  if(paused){ ctx.fillStyle=P.bg; ctx.globalAlpha=0.5; ctx.fillRect(0,0,VIEW_W,VIEW_H); ctx.globalAlpha=1; }
  drawHUD();                                     // bildschirmfest
  if(!reduce && !paused) t+=1;
  requestAnimationFrame(frame);
}
frame();
