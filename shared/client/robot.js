/* Small articulated in-app rig. Limbs rotate at separate hips, knees and elbows. */
(() => {
  const root=document.getElementById('robot');if(!root)return;
  root.innerHTML=`<svg viewBox="0 0 340 190" role="img" aria-label="Crowned StrictBot routine companion">
  <defs><linearGradient id="metal" x2=".7" y2="1"><stop stop-color="#f8fbff"/><stop offset="1" stop-color="#718397"/></linearGradient><linearGradient id="gold" x2="1" y2="1"><stop stop-color="#ffe6a2"/><stop offset="1" stop-color="#ce8c21"/></linearGradient></defs>
  <ellipse cx="170" cy="174" rx="78" ry="7" fill="#030a12"/>
  <g id="robotPosition"><g id="robotBody">
  <path id="cape" d="M-22 65Q-60 105-32 147L8 105 30 144Q53 110 22 65" fill="#17263f" stroke="#dfb251" stroke-width="2"/>
  <g id="legL" transform="translate(-13 116)"><path d="M0 0L-3 19" stroke="#9aaaba" stroke-width="12" stroke-linecap="round"/><g id="kneeL" transform="translate(-3 19)"><path d="M0 0L1 23" stroke="url(#metal)" stroke-width="10" stroke-linecap="round"/><path d="M-7 23L11 23" stroke="#dfb251" stroke-width="8" stroke-linecap="round"/></g><circle cy="0" r="6" fill="#344557"/></g>
  <g id="legR" transform="translate(13 116)"><path d="M0 0L3 19" stroke="#9aaaba" stroke-width="12" stroke-linecap="round"/><g id="kneeR" transform="translate(3 19)"><path d="M0 0L-1 23" stroke="url(#metal)" stroke-width="10" stroke-linecap="round"/><path d="M-11 23L7 23" stroke="#dfb251" stroke-width="8" stroke-linecap="round"/></g><circle r="6" fill="#344557"/></g>
  <g id="armL" transform="translate(-30 80)"><path d="M0 0L-5 20" stroke="url(#metal)" stroke-width="11" stroke-linecap="round"/><g id="elbowL" transform="translate(-5 20)"><path d="M0 0L-3 18" stroke="#9eafbf" stroke-width="9" stroke-linecap="round"/><circle cy="19" cx="-3" r="6" fill="url(#metal)"/></g><circle r="7" fill="url(#gold)"/></g>
  <g id="armR" transform="translate(30 80)"><path d="M0 0L5 20" stroke="url(#metal)" stroke-width="11" stroke-linecap="round"/><g id="elbowR" transform="translate(5 20)"><path d="M0 0L3 18" stroke="#9eafbf" stroke-width="9" stroke-linecap="round"/><circle cy="19" cx="3" r="6" fill="url(#metal)"/></g><circle r="7" fill="url(#gold)"/></g>
  <path d="M-23 75Q0 68 23 75L21 110Q0 127-21 110Z" fill="url(#metal)" stroke="#d7a637" stroke-width="2"/><circle cy="93" r="10" fill="#0b1d30" stroke="#d7a637" stroke-width="3"/><path d="M-5 96L0 87 5 96Z" fill="#44dfff"/>
  <g id="head" transform="translate(0 42)"><rect x="-32" y="-24" width="64" height="52" rx="21" fill="url(#metal)" stroke="#e4b957" stroke-width="2"/><rect x="-25" y="-16" width="50" height="34" rx="13" fill="#07172b"/><ellipse cx="-12" cy="-1" rx="5" ry="8" fill="#46e3ff"/><ellipse cx="12" cy="-1" rx="5" ry="8" fill="#46e3ff"/><path id="mouth" d="M-7 11Q0 14 7 11" fill="none" stroke="#4bdef4" stroke-width="2"/><path d="M-20-24L-26-43-10-35 0-47 10-35 26-43 20-24Z" fill="url(#gold)" stroke="#ffda7d" stroke-width="2"/><circle cy="-34" r="3" fill="#22b7df"/></g>
  </g></g></svg>`;
  const $=id=>document.getElementById(id),reduce=matchMedia('(prefers-reduced-motion: reduce)');let frame,started=performance.now(),speakingUntil=0,visible=true,lastDraw=0;
  const rotate=(id,at,angle)=>$(id).setAttribute('transform',`translate(${at}) rotate(${angle.toFixed(2)})`);
  function draw(t){if(document.hidden||!visible)return;if(t-lastDraw<33){frame=requestAnimationFrame(draw);return;}lastDraw=t;const seconds=(t-started)/1000,travel=reduce.matches?1:Math.min(seconds/3.2,1),ease=travel*travel*(3-2*travel),walk=travel<1?Math.sin(travel*Math.PI)*.75+.25:0,phase=seconds*8,swing=Math.sin(phase)*walk;
    root.dataset.state=travel<.7?'walking':travel<1?'slowing':t<speakingUntil?'speaking':'idle';
    $('robotPosition').setAttribute('transform',`translate(${70+100*ease} ${reduce.matches?0:Math.abs(Math.sin(phase))*walk*-2})`);
    $('robotBody').setAttribute('transform',`rotate(${swing*1.8} 0 95)`);
    rotate('legL','-13 116',swing*24);rotate('legR','13 116',-swing*24);
    rotate('kneeL','-3 19',Math.max(0,-swing)*34);rotate('kneeR','3 19',-Math.max(0,swing)*34);
    const speaking=t<speakingUntil&&!reduce.matches,gesture=speaking?-.5+.5*Math.sin(seconds*2):0;
    rotate('armL','-30 80',-swing*24);rotate('armR','30 80',swing*24-(speaking?35+gesture*8:0));
    rotate('elbowL','-5 20',-8-Math.abs(swing)*10);rotate('elbowR','5 20',speaking?-50+gesture*7:8+Math.abs(swing)*10);
    rotate('head','0 42',reduce.matches?0:Math.sin(seconds*1.5)*(speaking?2:1));
    $('mouth').setAttribute('d',speaking?`M-7 11Q0 ${14+Math.abs(Math.sin(seconds*12))*7} 7 11`:'M-7 11Q0 14 7 11');
    $('cape').setAttribute('transform',`rotate(${reduce.matches?0:Math.sin(seconds*3)*2*walk} 0 75)`);
    if(!document.hidden)frame=requestAnimationFrame(draw);
  }
  document.addEventListener('visibilitychange',()=>{cancelAnimationFrame(frame);if(!document.hidden)frame=requestAnimationFrame(draw);});
  new IntersectionObserver(entries=>{visible=entries[0]?.isIntersecting===true;cancelAnimationFrame(frame);if(visible&&!document.hidden)frame=requestAnimationFrame(draw);}).observe(root);
  document.addEventListener('strictbot-message',()=>{speakingUntil=performance.now()+3500;});frame=requestAnimationFrame(draw);
})();
