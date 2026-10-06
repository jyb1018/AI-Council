'use strict';
const $=id=>document.getElementById(id);
const token=location.hash.slice(1)||sessionStorage.getItem('council-token')||'';
if(location.hash){sessionStorage.setItem('council-token',token);history.replaceState(null,'',location.pathname);}
let state,catalog={},current=null,active=false,profile,lastSnapshot='',submitting=false;
const phaseNames={position:'독립 의견',critique:'상호 비평',revision:'답변 수정',synthesis:'최종 종합'};
const statusNames={queued:'대기 중',running:'토론 중',completed:'완료',failed:'실패',cancelled:'중지됨',interrupted:'중단됨',succeeded:'완료'};
function error(e){$('error').hidden=false;$('error').textContent=e.message||String(e);}
async function api(path,method='GET',data){const r=await fetch('/api'+path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});const v=await r.json();if(!r.ok)throw Error(v.error?.message||v.error||'요청 실패');return v;}
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function option(select,value,name){const o=el('option',name);o.value=value;select.append(o);}
function settings(){
 const root=$('providers');root.replaceChildren();
 for(const [name,cfg] of Object.entries(state.providers)){
  const card=el('div',undefined,'provider'),title=el('label',undefined,'provider-title'),check=document.createElement('input');check.type='checkbox';check.checked=profile.participants.includes(name);check.disabled=!cfg.enabled||active;check.setAttribute('aria-label',name+' 참여');title.append(check,el('span',name));card.append(title);
  const models=catalog[name]?.models||[],choice=profile.choices[name]||{model:null,effort:null};profile.choices[name]=choice;
  const model=document.createElement('select');model.id='model-'+name;model.setAttribute('aria-label',name+' 모델');option(model,'','CLI 기본 모델');for(const m of models)option(model,m.id,m.name+(m.hidden?' · 숨김':''));model.value=choice.model||'';
  if(choice.model&&!models.some(m=>m.id===choice.model)){option(model,choice.model,choice.model+' · 목록 확인 필요');model.value=choice.model;}
  const effort=document.createElement('select');effort.id='effort-'+name;effort.setAttribute('aria-label',name+' 추론 수준');const note=el('p',undefined,'note');
  function efforts(){effort.replaceChildren();option(effort,'','CLI 기본 추론');const m=models.find(m=>m.id===model.value);for(const v of m?.efforts||[])option(effort,v,v);effort.value=choice.effort||'';effort.disabled=active||!check.checked||!m?.efforts.length;note.textContent=m?.effort_scope==='cli'?'CLI 전체 옵션입니다. 이 CLI는 모델별 지원 목록을 제공하지 않습니다. 미지원 조합은 실행 오류로 표시됩니다.':(m&&!m.efforts.length?'이 모델은 추론 수준 선택을 제공하지 않습니다.':'');}
  check.onchange=()=>{profile.participants=check.checked?[...profile.participants,name]:profile.participants.filter(n=>n!==name);settings();};
  model.onchange=()=>{choice.model=model.value||null;choice.effort=null;efforts();};effort.onchange=()=>{choice.effort=effort.value||null;};model.disabled=active||!check.checked||!models.length;
  const ml=el('label','모델');ml.htmlFor=model.id;const ef=el('label','추론 수준');ef.htmlFor=effort.id;card.append(ml,model,ef,effort,note);efforts();
  if(!cfg.enabled)card.append(el('p','로컬 TOML에서 비활성화되어 있습니다.','note'));
  if(cfg.kind!=='mock'&&!cfg.subscription_confirmed)card.append(el('p','실행 전 TOML에서 구독 사용 설정을 확인해 주세요.','note'));
  if(catalog[name]?.error)card.append(el('p',catalog[name].error.message,'note'));root.append(card);
 }
 $('rounds').value=profile.rounds;$('rounds').disabled=active;$('chair').replaceChildren();option($('chair'),'','첫 참여자');for(const n of profile.participants)option($('chair'),n,n);$('chair').value=profile.chair||'';$('chair').disabled=active;$('save').disabled=active;$('refresh').disabled=active;
 budget();
}
function budget(){$('budget').textContent=`${profile.participants.length}명 · ${profile.rounds}라운드 · 예상 ${profile.participants.length*(1+2*profile.rounds)+1}회 호출`;}
async function save(){profile.rounds=Number($('rounds').value);profile.chair=$('chair').value||null;profile=await api('/profile','PUT',profile);$('saved').textContent='설정이 저장되었습니다.';budget();}
function render(snapshot){
 const serial=JSON.stringify(snapshot);if(serial===lastSnapshot)return;lastSnapshot=serial;
 const feed=$('feed'),nearBottom=feed.scrollHeight-feed.scrollTop-feed.clientHeight<90;feed.replaceChildren();
 const q=el('article',undefined,'message user');q.append(el('div','나의 질문','message-meta'),el('p',snapshot.question));feed.append(q);
 for(const a of snapshot.transcript){
  const card=el('article',undefined,'message'+(a.phase==='synthesis'?' final':'')+(a.status==='failed'?' error':''));
  const identity=snapshot.provider_configuration[a.provider],meta=el('div',undefined,'message-meta');meta.append(el('span',`${a.provider} · ${phaseNames[a.phase]||a.phase}`),el('small',`${a.round?`${a.round}라운드 · `:''}${statusNames[a.status]||a.status}`));card.append(meta);
  card.append(el('small',`${identity.model||'CLI 기본 모델'} · 추론 ${identity.reasoning_effort||'CLI 기본'}${a.reported_model?' · 응답 모델 '+a.reported_model:''}`,'eyebrow'));
  if(a.payload){for(const [key,value] of Object.entries(a.payload)){if(value==null||value===''||(Array.isArray(value)&&!value.length))continue;const labels={answer:'답변',rationale:'근거 요약',critiques:'상호 비평',risks:'위험',assumptions:'가정',next_steps:'다음 단계',confidence:'자기 평가',supporting_sources:'참고 자료',agreements:'합의',disagreements:'이견',limitations:'한계',changes:'수정 사항'};if(key!=='answer')card.append(el('h3',labels[key]||key));if(Array.isArray(value)){const ul=el('ul');for(const item of value)ul.append(el('li',typeof item==='object'?Object.entries(item).map(([k,v])=>`${k}: ${Array.isArray(v)?v.join('; '):v}`).join('\n'):String(item)));card.append(ul);}else card.append(el('p',String(value)));}}
  else card.append(el('p',a.error?.message||(a.status==='running'?'답변을 작성하고 있습니다…':statusNames[a.status]||a.status)));feed.append(card);
 }
 if(snapshot.error)feed.append(el('article',snapshot.error.message,'message error'));
 $('title').textContent=snapshot.question.length>42?snapshot.question.slice(0,42)+'…':snapshot.question;
 $('status').textContent=(snapshot.simulated?'모의 · ':'')+(statusNames[snapshot.status]||snapshot.status);$('cancel').hidden=!['running','queued'].includes(snapshot.status);if(nearBottom)feed.scrollTop=feed.scrollHeight;
}
async function poll(){state=await api('/state');const changed=active!==state.active;active=state.active;if(changed)settings();$('start').disabled=active||submitting;$('new').disabled=active;$('question').disabled=active;const nav=$('sessions');nav.replaceChildren();for(const s of state.sessions){const b=el('button',`${s.session_id.slice(0,8)} · ${s.participants.join(', ')}`,s.session_id===current?'selected':'');b.append(el('small',`${statusNames[s.status]||s.status} · ${s.created_at.slice(0,16).replace('T',' ')}`));b.onclick=()=>{current=s.session_id;lastSnapshot='';poll().catch(error);};nav.append(b);}if(current)render(await api('/sessions/'+current));}
$('refresh').onclick=async()=>{try{$('refresh').disabled=true;catalog=await api('/catalog','POST');settings();}catch(e){error(e);}finally{$('refresh').disabled=active;}};
$('save').onclick=()=>save().catch(error);$('rounds').onchange=()=>{profile.rounds=Number($('rounds').value);budget();};
$('composer').onsubmit=async e=>{e.preventDefault();if(submitting)return;submitting=true;$('start').disabled=true;$('error').hidden=true;try{await save();const s=await api('/sessions','POST',{question:$('question').value,idempotency_key:crypto.randomUUID()});current=s.session_id;lastSnapshot='';await poll();}catch(e){error(e);}finally{submitting=false;$('start').disabled=active;}};
$('cancel').onclick=async()=>{try{await api('/sessions/'+current+'/cancel','POST',{});await poll();}catch(e){error(e);}};
$('new').onclick=()=>{current=null;lastSnapshot='';$('feed').replaceChildren(el('div','새 질문을 입력해 주세요.','welcome'));$('title').textContent='새 토론';$('status').textContent='준비';$('cancel').hidden=true;$('question').value='';$('question').focus();};
(async()=>{try{if(!token)throw Error('서버가 안내한 인증 링크로 이 화면을 열어 주세요. ai-council web --open 명령을 사용할 수 있습니다.');state=await api('/state');profile=structuredClone(state.profile);active=state.active;catalog=await api('/catalog');settings();await poll();setInterval(()=>poll().catch(error),1000);}catch(e){error(e);}})();
