// Library: one page for videos and songs. A library dropdown (libraries.js), a search box
// over file names in the whole library, the folder browser, and All / Videos / Songs
// buttons. Every row has its own button: Make subtitles for a video, Make lyrics for a
// song. Folders come from the two existing browse endpoints (merged); search comes from
// GET /api/library/search. Uses $, node, action, api, toast, chooseView, byName and
// langName from app.js, listView/queueMany/queuedMessage/statusChips/chip from
// listview.js, and withLibrary/noLibraries/libraryChoice from libraries.js.
const LIBRARY_KINDS=['','video','song'];
const LIBRARY_SEARCH_LIMIT=500;
let libraryEntries=[],librarySearchCache=null,libraryBrowseToken=0,libraryCall=0,libraryCounts={video:0,song:0};
let libraryKind=(k=>LIBRARY_KINDS.includes(k)?k:'')(readPref('libraryKind',''));
const libraryFileKinds=new Map();// path -> 'video' | 'song' for every file this page has listed

// ----- pure helpers -----
// The two browse answers (videos + subtitle files, songs) become one list; each entry gets a
// kind (folder, video, subtitle, song). Folders appear in both answers, so they are joined by path.
function mergeEntries(videos,songs){
 const out=[],seen=new Set();
 for(const e of videos.entries||[]){
  if(e.directory){seen.add(e.path);out.push({...e,kind:'folder'});}
  else out.push({...e,kind:e.subtitle?'subtitle':'video'});}
 for(const e of songs.entries||[]){
  if(e.directory){if(!seen.has(e.path)){seen.add(e.path);out.push({...e,kind:'folder'});}}
  else out.push({...e,kind:'song'});}
 return out;}
const isPickable=f=>f.kind==='video'||f.kind==='song';
// The chips: All, Videos (and their subtitle files), Songs. Folders stay so you can keep browsing.
function kindAllows(f,kind){return !kind||f.kind==='folder'||(kind==='video'?f.kind==='video'||f.kind==='subtitle':f.kind==='song');}
// Each filter only applies to the kind it makes sense for: v: filters are for videos, s: for songs.
function libraryMatch(f,flt){
 if(!flt||f.kind==='folder')return true;
 const s=f.status;if(!s)return false;
 if(flt==='v:missing')return f.kind==='video'&&s.missing.length>0;
 if(flt==='v:none')return f.kind==='video'&&!s.subs.length;
 if(flt.startsWith('v:missing:'))return f.kind==='video'&&s.missing.includes(flt.slice(10));
 if(flt==='s:missing')return f.kind==='song'&&!s.lyrics;
 if(flt==='s:plain')return f.kind==='song'&&!s.enhanced;
 if(flt==='s:has')return f.kind==='song'&&!!s.lyrics;
 if(flt==='v:embedded')return f.kind==='video'&&s.source==='embedded';
 if(flt==='s:embedded')return f.kind==='song'&&!!s.lyrics_embedded&&!s.lyrics_file;
 if(flt==='x:failed')return !!s.error;
 return false;}

// Embedded tracks and lyrics: the lists show what's already known; the files on the page
// that nobody has looked inside yet are checked in the background (one request, at most 100
// files, remembered by the server). Failed scans are not asked again until Retry scan.
const libraryAsked=new Set();
async function libraryCheckInside(items,force=false){
 const todo=items.filter(f=>isPickable(f)&&f.status&&f.status.checked===false&&(force||!libraryAsked.has(f.path))).slice(0,100);
 if(!todo.length)return;
 for(const f of todo)libraryAsked.add(f.path);
 try{const r=await api(withLibrary('/embedded/check'),{paths:todo.map(f=>f.path),force});
  for(const f of todo)if(r.statuses&&r.statuses[f.path])f.status=r.statuses[f.path];
  libraryList.redraw();}
 catch{/* the chips just stay as they were */}}
async function libraryRetryScan(file){
 try{const r=await api(withLibrary('/embedded/check'),{paths:[file.path],force:true});
  if(r.statuses&&r.statuses[file.path]){file.status=r.statuses[file.path];libraryList.redraw();}}
 catch(e){toast(e.message);}}
// How much a file still needs (bigger = more): videos by missing languages, songs by lyrics.
const needScore=f=>!f.status?-1:f.kind==='song'?(!f.status.lyrics?2:!f.status.enhanced?1:0):f.status.missing.length+(f.status.subs.length?0:1);
function libraryCompare(sort){return sort==='name_desc'?(a,b)=>byName(b,a):sort==='missing'?(a,b)=>(needScore(b)-needScore(a))||byName(a,b):byName;}
// Filter, then sort: folders first, search results keep the server's best-match order on the default sort.
function libraryArrange(items,{filter='',sort='name',searching=false}={}){
 const out=items.filter(f=>libraryMatch(f,filter));
 if(searching&&sort==='name')return out;
 const cmp=libraryCompare(sort);
 return out.sort((a,b)=>((a.kind==='folder'?0:1)-(b.kind==='folder'?0:1))||cmp(a,b));}
function countKinds(list){const n={video:0,song:0};for(const f of list)if(isPickable(f))n[f.kind]++;return n;}
function splitKeys(keys,kinds){return {videos:keys.filter(k=>kinds.get(k)==='video'),songs:keys.filter(k=>kinds.get(k)==='song')};}
function makeLabel(videos,songs){return videos&&songs?'Make subtitles & lyrics':songs?'Make lyrics':'Make subtitles';}

// ----- the list -----
function libraryOptions(){const stream=$('#audio-stream').value;return{audio_stream:stream===''?null:Number(stream),regenerate:$('#regenerate').checked};}
// Queues the picked files: videos as subtitle jobs, songs as lyrics jobs. Returns one message
// that counts only the files actually queued.
async function libraryQueue(keys){
 const {videos,songs}=splitKeys(keys,libraryFileKinds),parts=[];
 if(videos.length){const [n,errors]=await queueMany('/jobs/batch',videos,libraryOptions());parts.push(queuedMessage(n,videos.length,errors,'video'));}
 if(songs.length){const [n,errors]=await queueMany('/lyrics/batch',songs,{regenerate:$('#lyrics-regenerate').checked});parts.push(queuedMessage(n,songs.length,errors,'song'));}
 return parts.join(' ');}

async function librarySearch(q){
 const key=[libraryChoice,libraryKind,q].join('\u0000');
 if(librarySearchCache&&librarySearchCache.key===key)return librarySearchCache;
 const data=await api(withLibrary('/library/search?q='+encodeURIComponent(q)+'&kind='+encodeURIComponent(libraryKind)+'&limit='+LIBRARY_SEARCH_LIMIT));
 return librarySearchCache={key,items:data.entries.map(e=>({...e})),total:data.total,truncated:data.truncated,max:data.max_files};}

// The whole filtered, sorted list for the current search, filter and sort (not just one page).
async function libraryArranged(p){
 const searching=!!p.q;let items,note='';
 if(searching){const r=await librarySearch(p.q);items=r.items;
  if(r.total>r.items.length)note='Showing the best '+r.items.length+' of '+r.total.toLocaleString()+' matches. Type more of the name to narrow it down.';
  if(r.truncated)note+=(note?' ':'')+'This collection has more than '+r.max.toLocaleString()+' files; only the first '+r.max.toLocaleString()+' were searched.';}
 else items=libraryEntries.filter(f=>kindAllows(f,libraryKind));
 const list=libraryArrange(items,{filter:p.filter,sort:p.sort,searching});
 for(const f of list)if(isPickable(f))libraryFileKinds.set(f.path,f.kind);
 return {list,note};}
async function libraryServer(p){
 const call=++libraryCall;
 try{const {list,note}=await libraryArranged(p);
  if(call===libraryCall){libraryCounts=countKinds(list);$('#search-note').textContent=note;$('#search-note').hidden=!note;}
  const page=p.limit?list.slice(p.offset,p.offset+p.limit):list;
  libraryCheckInside(page);// not awaited: the page shows first, the chips fill in
  return {total:list.length,items:page};}
 catch(e){toast(e.message);return {total:0,items:[]};}}

const libraryList=listView({el:$('#files'),name:'library',placeholder:'Search file names in this collection',noun:['item','items'],
 filters:()=>[['','All files'],
  ...(libraryKind==='song'?[]:[['v:missing','Videos missing any of your subtitle languages'],...(state?state.settings.target_languages:[]).map(t=>['v:missing:'+t,'Videos missing '+langName(t)+' subtitles']),['v:none','Videos with no subtitles at all'],['v:embedded','Videos with embedded subtitles only']]),
  ...(libraryKind==='video'?[]:[['s:missing','Songs missing lyrics'],['s:plain','Songs missing word-by-word (enhanced) lyrics'],['s:has','Songs with lyrics'],['s:embedded','Songs with embedded lyrics only']]),
  ['x:failed','Files where checking inside failed']],
 sorts:[['name','Name A–Z (best match first when searching)'],['name_desc','Name Z–A'],['missing','Needs subtitles or lyrics first']],
 key:f=>f.path,label:f=>f.name,path:f=>f.path,folder:f=>f.kind==='folder',
 selectable:isPickable,
 emptyText:()=>noLibraries()?NO_LIBRARIES:'No videos, songs or folders here.',
 row:libraryRow,
 server:libraryServer,
 serverKeys:async p=>(await libraryArranged({...p,offset:0,limit:0})).list.filter(isPickable).map(f=>f.path),
 pickableTotal:()=>libraryCounts.video+libraryCounts.song,
 bulk:(keys,view)=>{const {videos,songs}=splitKeys(keys,libraryFileKinds);
  return [action(makeLabel(videos.length,songs.length)+' for selected ('+keys.length+')',async()=>{const message=await libraryQueue(keys);view.clear();toast(message);},'primary')];},
 all:view=>{if(!view.pickable)return [];const {video,song}=libraryCounts;
  return [action(makeLabel(video,song)+' for all '+view.pickable+(view.filter||view.query?' shown':''),async()=>{
   const keys=await view.keys();
   if(keys.length>10&&!confirm('Queue '+keys.length+' files ('+splitKeys(keys,libraryFileKinds).videos.length+' videos, '+splitKeys(keys,libraryFileKinds).songs.length+' songs)?'))return;
   toast(await libraryQueue(keys));},'secondary')];}});

function libraryRow(file){
 const row=node('div',undefined,'file-row'),text=node('div',undefined,'file-main'),folder=file.kind==='folder';
 text.append(node('span',folder?file.name+'/':file.name,folder?'folder-name':'file-name'));
 if(file.folder)text.append(node('span',file.folder+'/','mono small muted'));// search results: where the file lives
 if(file.status)text.append(statusChips(file.kind,file.status,()=>libraryRetryScan(file)));
 else if(file.kind==='subtitle'){const c=node('span',undefined,'chips');c.append(chip('Subtitle file'));text.append(c);}
 row.append(text);
 if(file.kind==='subtitle')row.append(action('Translate SRT',async()=>{$('#srt-path').value=file.path;$('#srt-file').value='';const code=file.name.split('.').slice(-2,-1)[0];if(code&&code.length<=3){$('#srt-source').value=code;renderSrtTargets();}chooseView('subtitles');}));
 else if(folder)row.append(action('Open',async()=>{libraryList.resetSearch();$('#browse-path').value=file.path;await browse();}));
 else if(file.kind==='song')row.append(action('Make lyrics',async()=>{await api('/lyrics',{path:file.path,regenerate:$('#lyrics-regenerate').checked});toast('Added to the queue. The lyrics files appear next to the song when it finishes.');}));
 else row.append(action('Make subtitles',async()=>{await api('/jobs',{path:file.path,...libraryOptions()});toast('Added to the subtitle queue.');}));
 return row;}

// ----- browsing -----
async function browse(){
 const token=++libraryBrowseToken;
 if(noLibraries()){$('#files-note').hidden=true;libraryEntries=[];return libraryList.set([]);}
 const path=$('#browse-path').value.trim(),get=kind=>api(withLibrary(kind+'?path='+encodeURIComponent(path)));
 const [videos,songs]=await Promise.all([get('/browse'),get('/music/browse')]);
 if(token!==libraryBrowseToken)return;// the library or folder changed while this was loading
 libraryEntries=mergeEntries(videos,songs);
 $('#browse-up').disabled=!path.split('/').filter(Boolean).length;$('#files-note').hidden=!(videos.truncated||songs.truncated);
 await libraryList.set([]);}
// A new library: forget the folder and any search results from the old one.
function libraryReload(){librarySearchCache=null;libraryAsked.clear();$('#browse-path').value='';return browse().catch(e=>toast(e.message));}
function chooseKind(kind){
 libraryKind=LIBRARY_KINDS.includes(kind)?kind:'';savePref('libraryKind',libraryKind);librarySearchCache=null;
 document.querySelectorAll('[data-library-kind]').forEach(b=>{const on=b.dataset.libraryKind===libraryKind;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});
 return libraryList.set([]);}

if(typeof document!=='undefined'&&document.getElementById('browse-form')){
 $('#browse-form').onsubmit=e=>{e.preventDefault();libraryList.resetSearch();browse().catch(e=>toast(e.message));};
 $('#browse-up').onclick=()=>{const parts=$('#browse-path').value.split('/').filter(Boolean);parts.pop();$('#browse-path').value=parts.join('/');libraryList.resetSearch();browse().catch(e=>toast(e.message));};
 document.querySelectorAll('[data-library-kind]').forEach(b=>b.onclick=()=>chooseKind(b.dataset.libraryKind).catch(e=>toast(e.message)));
 chooseKind(libraryKind).catch(()=>{});}
