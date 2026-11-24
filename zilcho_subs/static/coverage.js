// Subtitle & lyrics coverage (the Coverage tab of Library status): share of the library
// with subtitles in each language, lyrics and enhanced (word-by-word) lyrics, with
// "Generate missing ..." buttons that say how many files they will queue. Uses $, node,
// api, toast and state from app.js; monitor.js calls loadCoverage() when the tab opens.
let coverageData=null,coverageBusy=false;

const covLanguage=code=>state&&state.languages&&state.languages[code]?state.languages[code]:code.toUpperCase();
const covTone=p=>p===null?'none':p>=90?'high':p>=50?'mid':'low';
const covPlural=(n,one,many)=>n.toLocaleString()+' '+(n===1?one:many);

async function loadCoverage(refresh=false){
 if(coverageBusy)return;coverageBusy=true;$('#coverage-refresh').disabled=true;
 try{coverageData=await api(withLibrary('/coverage'+(refresh?'?refresh=true':'')));renderCoverage();}
 catch(e){toast(e.message);$('#coverage-subs').replaceChildren(node('p','Could not count coverage: '+e.message,'error-text'));}
 finally{coverageBusy=false;$('#coverage-refresh').disabled=false;}
}

function coverageRow(label,share,button,unit){
 const row=node('div','','cov-row'),name=node('span','','cov-label');
 name.append(node('span',label),node('span',share.total?share.have.toLocaleString()+' of '+covPlural(share.total,unit,unit+'s'):'No '+unit+'s found','cov-count small muted'));
 row.append(name);
 const bar=node('span','','cov-bar'),pct=share.percent;
 bar.setAttribute('role','progressbar');bar.setAttribute('aria-label',label);
 bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax','100');
 if(pct!==null)bar.setAttribute('aria-valuenow',String(pct));
 const fill=node('span','','cov-fill '+covTone(pct));fill.style.width=(pct||0)+'%';bar.append(fill);
 row.append(bar,node('strong',pct===null?'—':Math.round(pct)+'%','cov-pct'));
 row.append(button);
 return row;
}

// "Generate missing English subtitles (12 videos)" / "Generate missing lyrics (3 songs)".
function generateLabel(what,language,missing,noun){return 'Generate missing '+(language?language+' ':'')+what+' ('+covPlural(missing,noun,noun+'s')+')';}

function generateButton(text,missing,body,noun){
 const button=node('button',text,missing?'secondary':'quiet');button.type='button';
 if(!missing){button.disabled=true;button.title='Nothing missing';return button;}
 button.title=covPlural(missing,noun,noun+'s')+' missing';
 button.onclick=async()=>{
  if(missing>10&&!confirm(text+'?\n\nThis adds up to '+covPlural(missing,'job','jobs')+' to the queue (one runs at a time).'))return;
  button.disabled=true;
  try{
   const r=await api(withLibrary('/coverage/generate'),body);
   let message=r.queued?covPlural(r.queued,'job','jobs')+' added to the queue.':'Nothing new to queue.';
   if(r.already_queued)message+=' '+covPlural(r.already_queued,'file is','files are')+' already waiting in the queue.';
   if(r.queue_full)message+=' The queue is full; '+covPlural(r.left,'file','files')+' left. Click again when jobs finish.';
   if(r.errors&&r.errors.length)message+=' '+covPlural(r.errors.length,'file','files')+' could not be queued: '+r.errors[0].error;
   toast(message);coverageData=r.coverage;renderCoverage();
   if(typeof refresh==='function')refresh().catch(()=>{});
  }catch(e){toast(e.message);button.disabled=false;}
 };
 return button;
}

// "Inside files: 40 of 50 checked, 2 could not be read (counted by their files only)."
function insideNote(inside){
 if(!inside)return '';
 let text='Inside files: '+inside.checked.toLocaleString()+' of '+inside.total.toLocaleString()+' checked';
 if(inside.failed)text+=', '+covPlural(inside.failed,'file','files')+' could not be read (counted by their files only)';
 text+='.';
 if(inside.unchecked)text+=' Files not checked yet count by their files only; use Check inside files.';
 if(!inside.used)text+=' Subtitle tracks inside videos are not counted while "skip existing" is off in Settings.';
 return text;}

let insideTimer=null;
async function pollInside(){
 try{
  const p=await api('/coverage/inside');
  $('#coverage-inside-check').disabled=p.running;
  if(p.running){$('#coverage-inside').textContent='Checking inside files: '+p.done.toLocaleString()+' of '+p.total.toLocaleString()+'…';insideTimer=setTimeout(pollInside,1500);return;}
  insideTimer=null;
  if(p.error)toast('Checking inside files stopped: '+p.error);
  await loadCoverage(true);
 }catch(e){insideTimer=null;$('#coverage-inside-check').disabled=false;toast(e.message);}}
$('#coverage-inside-check').onclick=async()=>{
 $('#coverage-inside-check').disabled=true;
 try{await api(withLibrary('/coverage/inside'),{});await pollInside();}
 catch(e){$('#coverage-inside-check').disabled=false;toast(e.message);}};

function renderCoverage(){
 const d=coverageData;
 $('#coverage-inside').textContent=insideNote(d.inside);
 $('#cov-videos').textContent=d.videos.toLocaleString();
 $('#cov-songs').textContent=d.songs.toLocaleString();
 $('#cov-targets').textContent=d.targets.map(t=>t.toUpperCase()).join(', ')||'—';
 const where=d.folders.filter(Boolean);
 let note='Counted '+new Date(d.checked*1000).toLocaleTimeString([], {timeStyle:'short'})+' in '+(where.length?where.join(', '):'the whole media folder')+(libraryChoice?' ('+libraryScopeName()+')':'')+'.';
 if(d.truncated)note+=' Stopped after 100,000 files; the numbers cover only those.';
 $('#coverage-note').textContent=note;
 const subs=$('#coverage-subs');
 if(!d.videos)subs.replaceChildren(node('p','No videos found in this collection. Check your collections in Settings.','muted empty'));
 else subs.replaceChildren(...d.subtitles.map(s=>{
  const name=covLanguage(s.language);
  const row=coverageRow(name+' Subs',s,generateButton(generateLabel('subtitles',name,s.missing,'video'),s.missing,{what:'subtitles',language:s.language},'video'),'video');
  if(!s.target)row.querySelector('.cov-label').append(node('span','not in your languages','cov-tag'));
  return row;
 }));
 const lyrics=$('#coverage-lyrics');
 if(!d.songs){lyrics.replaceChildren(node('p','No songs found in this collection.','muted empty'));return;}
 lyrics.replaceChildren(
  coverageRow('Lyrics',d.lyrics,generateButton(generateLabel('lyrics','',d.lyrics.missing,'song'),d.lyrics.missing,{what:'lyrics'},'song'),'song'),
  coverageRow('Enhanced LRC',d.enhanced,generateButton(generateLabel('enhanced lyrics','',d.enhanced.missing,'song'),d.enhanced.missing,{what:'enhanced'},'song'),'song'));
}

$('#coverage-refresh').onclick=()=>loadCoverage(true);
