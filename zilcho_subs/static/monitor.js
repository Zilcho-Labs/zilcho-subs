// Monitor library: the status card in the sidebar (stays until dismissed), the
// Monitor view, and its settings. Uses $, node, action, api, toast, refresh,
// state, view and settingsLoaded from app.js.
let monitorBusy=false;
const MONITOR_LINES={scanning:'Scanning your library…',watching:'Watching…',off:'Monitor off',paused:'Paused',quiet:'Waiting for processing hours'};
const MONITOR_PILLS={complete:'completed',missing:'skipped',queued:'queued',failed:'failed',waiting:'paused'};
const MONITOR_STATES={complete:'done',missing:'missing',queued:'in the queue',failed:'failed',waiting:'settling'};

function loadMonitorSettings(){const s=state.settings; for(const [id,key] of [['scan-interval','scan_interval'],['stable-seconds','stable_seconds'],['schedule-start','schedule_start'],['schedule-end','schedule_end']])$('#'+id).value=s[key];
 for(const [id,key] of [['watcher-enabled','watcher_enabled'],['auto-lyrics','auto_lyrics'],['skip-existing','skip_existing']])$('#'+id).checked=s[key];
 $('#monitor-root').textContent=state.media_root;}

function when(seconds){const d=Math.round(Math.abs(seconds));if(d<60)return d+' s';if(d<3600)return Math.round(d/60)+' min';if(d<86400)return Math.round(d/3600)+' h';return Math.round(d/86400)+' days';}
function plural(n,one,many){return n+' '+(n===1?one:many);}

function renderMonitorBadge(sum){
 const badge=$('#monitor-badge');badge.hidden=false;badge.dataset.status=sum.status;
 const results=[];if(sum.generated)results.push(plural(sum.generated,'item','items')+' generated');if(sum.failed)results.push(sum.failed+' failed');
 badge.classList.toggle('alert',sum.failed>0);badge.classList.toggle('news',results.length>0);
 $('#monitor-line').textContent=results.length?results.join(', '):MONITOR_LINES[sum.status];
 // The always-visible line: what's missing, what's waiting in the queue, when it last looked.
 const parts=[];
 if(results.length)parts.push(MONITOR_LINES[sum.status]);
 if(sum.missing)parts.push(sum.missing+' missing');
 if(sum.pending_jobs)parts.push(plural(sum.pending_jobs,'job','jobs')+' in the queue');
 if(sum.status==='off'&&!sum.last_scan)parts.push('Open to set it up');
 else parts.push(sum.last_scan?'checked '+when(Date.now()/1000-sum.last_scan)+' ago':'not checked yet');
 for(const srv of sum.servers||[])if(srv.enabled&&srv.error)parts.push(srv.name+(srv.error.startsWith('None of the new')?': check the folder':' not reachable'));
 const detail=parts.join(' · ');$('#monitor-detail').textContent=detail.charAt(0).toUpperCase()+detail.slice(1);
 $('#monitor-badge').title=sum.last_full_scan?'Last full check: '+new Date(sum.last_full_scan*1000).toLocaleString():'';
 // Like a tray icon: the tab title counts results you haven't dismissed yet.
 const unseen=(sum.generated||0)+(sum.failed||0);document.title=(unseen?'('+unseen+') ':'')+'Zilcho Subs';
 $('#monitor-dismiss').hidden=!results.length;}

function renderMonitorView(data){
 const sum=data.summary,c=sum.counts;
 $('#monitor-pill').textContent=MONITOR_LINES[sum.status];$('#monitor-pill').className='pill '+({watching:'running',scanning:'running',off:'cancelled',paused:'paused',quiet:'paused'})[sum.status];
 const total=k=>c.video[k]+c.song[k];
 $('#mon-video-missing').textContent=c.video.missing;$('#mon-song-missing').textContent=c.song.missing;
 $('#mon-queued').textContent=total('queued');$('#mon-complete').textContent=total('complete');$('#mon-failed').textContent=total('failed');$('#mon-waiting').textContent=total('waiting');
 const now=Date.now()/1000,times=[];
 times.push(sum.last_scan?'Last check '+when(now-sum.last_scan)+' ago.':'No check yet.');
 if(sum.watcher_enabled&&sum.next_scan)times.push(sum.next_scan>now?'Next in about '+when(sum.next_scan-now)+'.':'Next check any moment.');
 if(!sum.watcher_enabled)times.push('Watching is off, so it only checks when you press a button.');
 $('#monitor-times').textContent=times.join(' ');
 $('#monitor-error').hidden=!sum.last_error;$('#monitor-error').textContent=sum.last_error?'Last check failed: '+sum.last_error:'';
 $('#monitor-toggle').textContent=sum.watcher_enabled?'Stop watching':'Start watching';
 $('#monitor-make-subs').disabled=!(c.video.missing+c.video.failed);$('#monitor-make-lyrics').disabled=!(c.song.missing+c.song.failed);
}

// The list: the server searches, filters and pages (the library can be large).
function monitorRow(row){
 const card=node('div',undefined,'file-row monitor-row');const text=node('div',undefined,'monitor-path');
 const slash=row.path.lastIndexOf('/');const name=node('span',undefined,'mono');
 if(slash>=0)name.append(node('span',row.path.slice(0,slash+1),'muted'));name.append(row.path.slice(slash+1));text.append(name);
 let detail='';
 if(row.state==='waiting')detail='New or changed; waiting for it to stop changing.';
 else if(!row.missing.length&&!row.found.length&&row.state==='complete')detail='A track inside the file already covers it.';
 if(row.job_status&&row.state!=='queued')detail+=(detail?' · ':'')+'last job '+row.job_status;
 const line=statusChips(row.kind,row.status);if(detail)line.append(node('span',detail,'small muted'));text.append(line);
 card.append(node('span',row.kind==='video'?'video':'song','pill'),text,node('span',MONITOR_STATES[row.state]||row.state,'pill '+(MONITOR_PILLS[row.state]||'')));
 if(['missing','failed'].includes(row.state))card.append(action(row.kind==='video'?'Make subtitles':'Make lyrics',async()=>{const r=await api('/audit',{path:row.path,generate:true,kinds:[row.kind]});toast(r.queued.length?'Added to the queue.':'Nothing to make for this file.');await refreshMonitor();},'quiet'));
 return card;}
function monitorQuery(p,extra){return '/state/audit?state='+encodeURIComponent($('#monitor-filter').value)+'&kind='+encodeURIComponent($('#monitor-kind').value)+
 '&q='+encodeURIComponent(p.q)+'&missing='+encodeURIComponent(p.filter)+'&sort='+encodeURIComponent(p.sort)+(libraryParam()?'&'+libraryParam():'')+extra;}
let monitorSummary=null;
const monitorList=listView({el:$('#monitor-items'),name:'monitor',placeholder:'Search your library as you type',filterLabel:'Missing',noun:['file','files'],
 filters:()=>[['','Anything'],...(state?state.settings.target_languages:[]).map(t=>[t,langName(t)+' subtitles']),['lyrics','Lyrics']],
 sorts:[['status','Needs attention first'],['name','Path A–Z'],['name_desc','Path Z–A']],
 key:r=>r.path,label:r=>r.path,selectable:r=>['missing','failed'].includes(r.state),
 selectAllLabel:()=>'Select all that need it',
 emptyTitle:()=>monitorSummary&&monitorSummary.last_scan?'Nothing here.':'No check yet.',
 emptyText:()=>monitorSummary&&monitorSummary.last_scan?'Nothing matches this filter.':'Press Scan now to look through your library.',
 row:monitorRow,
 server:async p=>{const data=await api(monitorQuery(p,'&items=true&limit='+p.limit+'&offset='+p.offset));monitorSummary=data.summary;renderMonitorBadge(data.summary);renderMonitorView(data);return data;},
 serverKeys:async p=>(await api(monitorQuery(p,'&paths=true'))).paths,
 bulk:(keys,view)=>[action('Queue selected ('+keys.length+')',async()=>{
   let count=0;const errors=[];for(let i=0;i<keys.length;i+=1000){const r=await api('/audit/batch',{paths:keys.slice(i,i+1000)});count+=r.queued;errors.push(...r.errors);if(r.errors.some(e=>/Queue is full/.test(e.error)))break;}
   view.clear();toast(queuedMessage(count,keys.length,errors,'file'));await refreshMonitor();},'primary')]});

async function refreshMonitor(){
 if(!state||monitorBusy)return;monitorBusy=true;
 try{const open=view==='monitor';
  if(open){monitorList.refilter();await monitorList.update();if(view==='monitor')await loadServerStatus();}
  else renderMonitorBadge((await api(withLibrary('/state/audit?items=false'))).summary);
 }finally{monitorBusy=false;}}
const quietly=()=>refreshMonitor().catch(e=>toast(e.message));

async function monitorRequest(body,message){try{await api('/audit',body);toast(message);setTimeout(quietly,800);await quietly();}catch(e){toast(e.message);}}
$('#monitor-scan').onclick=()=>monitorRequest({generate:false},'Checking your library. Results appear below as it goes.');
$('#monitor-make-subs').onclick=()=>monitorRequest({generate:true,kinds:['video']},'Queueing subtitles for videos that are missing them.');
$('#monitor-make-lyrics').onclick=()=>monitorRequest({generate:true,kinds:['song']},'Queueing lyrics for songs that have none.');
$('#monitor-dismiss').onclick=async()=>{try{await api('/audit/dismiss',{});await quietly();}catch(e){toast(e.message);}};
$('#monitor-toggle').onclick=async()=>{try{await api('/settings',{...state.settings,watcher_enabled:!state.settings.watcher_enabled});settingsLoaded=false;await refresh();await quietly();}catch(e){toast(e.message);}};
$('#monitor-filter').onchange=()=>{monitorList.clear();quietly();};$('#monitor-kind').onchange=()=>{monitorList.clear();quietly();};
$('#monitor-form').onsubmit=async e=>{e.preventDefault();
 const settings={...state.settings};
 for(const [id,key] of [['scan-interval','scan_interval'],['stable-seconds','stable_seconds'],['schedule-start','schedule_start'],['schedule-end','schedule_end']])settings[key]=Number($('#'+id).value);
 for(const [id,key] of [['watcher-enabled','watcher_enabled'],['auto-lyrics','auto_lyrics'],['skip-existing','skip_existing']])settings[key]=$('#'+id).checked;
 try{await api('/settings',settings);settingsLoaded=false;await refresh();await quietly();toast('Monitor settings saved.');}catch(error){toast(error.message);}};
// ----- Media servers: Jellyfin, Emby, Plex (all optional) -----
const SERVERS={
 jellyfin:{name:'Jellyfin',port:8096,key:'API key',keyHelp:'Make one in Jellyfin: Dashboard → API Keys.',root:'often <span class="mono">/media</span> or <span class="mono">/data/media</span>'},
 emby:{name:'Emby',port:8096,key:'API key',keyHelp:'Make one in Emby: Settings → Advanced → API Keys.',root:'often <span class="mono">/media</span> or <span class="mono">/mnt/media</span>'},
 plex:{name:'Plex',port:32400,key:'Plex token',keyHelp:'In Plex Web open any item → ⋯ → Get Info → View XML; the token is the X-Plex-Token at the end of the address.',root:'often <span class="mono">/media</span> or <span class="mono">/data</span>; it must be inside one of your Plex library folders'}};
const sid=(k,f)=>'#'+k+'-'+f;
function buildServerForms(){
 const list=$('#server-list');if(!list||list.childElementCount)return;
 for(const [k,s] of Object.entries(SERVERS)){
  const word=k==='plex'?'token':'key';
  const d=document.createElement('details');d.className='server-link';d.id=k+'-link';
  d.innerHTML=`<summary><strong>${s.name}</strong><span id="${k}-pill" class="pill">Not set up</span></summary>
<form id="${k}-form"><div class="form-grid"><label>${s.name} address<input id="${k}-url" type="url" placeholder="http://192.168.1.10:${s.port}" autocomplete="off" spellcheck="false"></label><label>${s.key}<input id="${k}-key" type="password" autocomplete="new-password" spellcheck="false"><span class="small muted">${s.keyHelp} It stays in this app's data and is never shown again.</span></label><label>Media folder as ${s.name} sees it<input id="${k}-root" placeholder="/media" spellcheck="false"><span class="small muted">The same media folder, as it's mapped in the ${s.name} container (${s.root}).</span></label></div>
<label class="check"><input id="${k}-notify" type="checkbox"> Tell ${s.name} when new subtitles or lyrics are made (only that folder is ${k==='plex'?'scanned':'refreshed'})</label>
<label class="check"><input id="${k}-refresh" type="checkbox"> Also ask ${s.name} to rescan all its libraries after a full Monitor check (at most once an hour while watching; heavier on big libraries)</label>
<p id="${k}-status" class="small muted"></p><p id="${k}-error" class="error-text" hidden></p>
<div class="job-actions"><button class="primary">Save ${s.name} link</button><button type="button" id="${k}-test" class="secondary">Test connection</button><button type="button" id="${k}-forget" class="quiet">Turn off and forget the ${word}</button></div></form>`;
  list.append(d);
  const form=()=>({url:$(sid(k,'url')).value.trim(),api_key:$(sid(k,'key')).value.trim(),media_root:$(sid(k,'root')).value.trim()||'/media',notify:$(sid(k,'notify')).checked,refresh_after_audit:$(sid(k,'refresh')).checked});
  $(sid(k,'form')).onsubmit=async e=>{e.preventDefault();
   try{const j=await api('/servers/'+k,form());renderServer(j);toast(j.enabled?s.name+' link saved. Press Test connection to check it.':j.url?'Saved. Add the '+s.key+' to turn it on.':s.name+' link turned off.');}catch(error){toast(error.message);}};
  $(sid(k,'test')).onclick=async()=>{const f=form(),button=$(sid(k,'test'));button.disabled=true;
   try{const r=await api('/servers/'+k+'/test',{url:f.url||null,api_key:f.api_key});toast('Connected to '+(r.server.name||s.name)+(r.server.version?' '+r.server.version:'')+'.');await loadServers();}
   catch(error){toast(error.message);}finally{button.disabled=false;}};
  $(sid(k,'forget')).onclick=async()=>{if(!confirm('Turn off the '+s.name+' link and forget the saved '+s.key+'?'))return;
   try{renderServer(await api('/servers/'+k+'/forget',{}));toast(s.name+' link turned off.');}catch(error){toast(error.message);}};}}
function renderServer(j){
 const k=j.kind;$(sid(k,'url')).value=j.url;$(sid(k,'root')).value=j.media_root;$(sid(k,'key')).value='';
 $(sid(k,'key')).placeholder=j.has_key?'Saved (leave empty to keep it)':'Paste the '+SERVERS[k].key;
 $(sid(k,'notify')).checked=j.notify;$(sid(k,'refresh')).checked=j.refresh_after_audit;
 if(j.url)$('#'+k+'-link').open=true;
 renderServerStatus(j);}
// Status only (pill, lines, error): safe to redraw while someone is typing in the form.
function renderServerStatus(j){
 const k=j.kind,s=SERVERS[k],pill=$(sid(k,'pill'));if(!pill)return;
 pill.textContent=!j.enabled?(j.url?'Needs the '+s.key:'Not set up'):j.last_error?(j.last_error.startsWith('None of the new')?'Check the folder':'Not reachable'):'Connected';
 pill.className='pill '+(!j.enabled?'':j.last_error?'failed':'completed');
 const now=Date.now()/1000,lines=[];
 if(j.server&&j.server.name)lines.push('Server: '+j.server.name+(j.server.version?' ('+j.server.version+')':'')+'.');
 if(j.last_notified)lines.push('Last told about new files '+when(now-j.last_notified)+' ago.');
 if(j.last_refresh)lines.push('Last full rescan asked '+when(now-j.last_refresh)+' ago.');
 if(!j.enabled)lines.push('Off. Nothing is sent to '+s.name+'.');
 $(sid(k,'status')).textContent=lines.join(' ');
 $(sid(k,'error')).hidden=!j.last_error;$(sid(k,'error')).textContent=j.last_error?'Last try failed: '+j.last_error:'';}
const loadServers=()=>{buildServerForms();return api('/servers').then(l=>l.forEach(renderServer)).catch(e=>toast(e.message));};
const loadServerStatus=()=>api('/servers').then(l=>l.forEach(renderServerStatus)).catch(()=>{});
buildServerForms();
document.querySelectorAll('[data-view="monitor"]').forEach(e=>e.addEventListener('click',()=>{quietly();loadServers();}));
setInterval(()=>{if(state&&!document.hidden)quietly();},3000);
// Show the card as soon as the app has loaded, not on the first 3-second tick.
const monitorFirst=setInterval(()=>{if(state){clearInterval(monitorFirst);quietly();}},200);
