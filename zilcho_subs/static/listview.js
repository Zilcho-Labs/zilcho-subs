// Shared file list for Media library, Song lyrics and Monitor library:
// search as you type (fuzzy), a filter, sorting, pages with a page-size gear,
// alternating row colours, and checkboxes with a "Queue selected" bar.
// Uses node() from app.js.

// Lower is a better match; null is no match. Same rules as status.fuzzy_rank on the server.
function fuzzyRank(query,name,path=''){
 const q=query.toLocaleLowerCase().trim().replace(/\s+/g,' ');if(!q)return 0;const n=name.toLocaleLowerCase();
 if(n.startsWith(q))return 0;if(n.includes(q))return 1;
 if(q.length>=2&&path.toLocaleLowerCase().split('/').slice(0,-1).some(f=>f.includes(q)))return 2;
 let i=0;for(const ch of q.replace(/ /g,'')){i=n.indexOf(ch,i);if(i<0)return null;i++;}return 3;}

function readPref(key,fallback){try{const v=localStorage.getItem('zilcho-subs.'+key);return v===null?fallback:JSON.parse(v);}catch{return fallback;}}
function savePref(key,value){try{localStorage.setItem('zilcho-subs.'+key,JSON.stringify(value));}catch{}}

const ICONS={gear:'<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M19.4 13a7.6 7.6 0 0 0 0-2l2.1-1.6-2-3.5-2.5 1a7.4 7.4 0 0 0-1.7-1L15 3.3h-4l-.4 2.6a7.4 7.4 0 0 0-1.7 1l-2.5-1-2 3.5L6.6 11a7.6 7.6 0 0 0 0 2l-2.1 1.6 2 3.5 2.5-1a7.4 7.4 0 0 0 1.7 1l.4 2.6h4l.4-2.6a7.4 7.4 0 0 0 1.7-1l2.5 1 2-3.5zM13 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z" transform="translate(-1 0)"/></svg>',
 up:'<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>'};
function iconButton(icon,label,cls='quiet icon-button'){const b=node('button',undefined,cls);b.type='button';b.innerHTML=ICONS[icon];b.setAttribute('aria-label',label);b.title=label;return b;}

// Status chips: "Subs: en, es" / "Missing: fr" / "Lyrics: yes" / "Enhanced: yes".
function chip(text,tone){return node('span',text,'chip'+(tone?' '+tone:''));}
// Embedded subtitles and lyrics (inside the file) are shown next to the file ones:
// "Subs: en (embedded), es (file)", "Embedded only", "Lyrics: embedded", "Scan failed" + Retry.
// onRetry is optional; the Library page passes it so a failed scan can be tried again.
function subtitleLabel(status){
 const files=status.external||status.subs,inside=status.embedded||[];
 const all=[...new Set([...files,...inside])].sort();
 if(!status.external)return all.join(', ');// older answers: no source information
 return all.map(l=>l+' ('+(inside.includes(l)&&files.includes(l)?'embedded + file':inside.includes(l)?'embedded':'file')+')').join(', ');}
function lyricsLabel(status){
 const where=status.lyrics_source;
 return 'Lyrics: '+(status.lyrics_file&&status.lyrics_embedded?'file + embedded':status.lyrics_embedded?'embedded':where?'file':'yes');}
function statusChips(kind,status,onRetry){
 const out=node('span',undefined,'chips');if(!status)return out;
 if(kind==='video'){
  const label=subtitleLabel(status);
  out.append(label?chip('Subs: '+label,'ok'):chip('Subs: missing','warn'));
  if(status.source==='both')out.append(chip('Embedded + file'));
  else if(status.source==='embedded')out.append(chip('Embedded only'));
  if(status.unknown)out.append(chip('Language unknown: '+status.unknown+(status.unknown===1?' track':' tracks'),'warn'));
  if(status.bitmap&&status.bitmap.length)out.append(chip('Image subtitles: '+status.bitmap.join(', ')));
  if((status.subs.length||status.unknown)&&status.missing.length)out.append(chip('Missing: '+status.missing.join(', '),'warn'));
 }else if(status.lyrics){out.append(chip(lyricsLabel(status),'ok'),status.enhanced?chip('Enhanced: yes','ok'):chip('Enhanced: no'));}
 else out.append(chip('Lyrics: missing','warn'),...(status.enhanced?[chip('Enhanced: yes','ok')]:[]));
 if(status.error){const failed=chip('Scan failed','warn');failed.title=status.error;out.append(failed);
  if(onRetry){const b=node('button','Retry scan','quiet small');b.type='button';b.onclick=onRetry;out.append(b);}}
 return out;}

/* o: {el, name, placeholder, filters:()=>[[value,label]], sorts:[[value,label]],
   key(item), label(item), path(item), folder(item), match(item,filter), compare(sort)->(a,b)=>n,
   selectable(item), row(item)->element, bulk(keys,view)->[buttons], all(view)->[buttons],
   server(params)->{total,items} (Monitor, Library: the page code searches and pages),
   serverKeys(params)->[keys] (server: every matching key, for Select all),
   pickableTotal()->n (server: how many matching items can be ticked, when total also counts folders),
   noun:['file','files']} */
function listView(o){
 const v={items:[],total:0,page:1,selected:new Set(),shown:[],matching:[],seq:0};
 const tools=node('div',undefined,'list-tools'),search=node('input');search.type='search';search.placeholder=o.placeholder||'Search';search.setAttribute('aria-label',o.placeholder||'Search');search.className='list-search';
 const filter=node('select');filter.setAttribute('aria-label','Filter');const filterLabel=node('label',undefined,'list-select');filterLabel.append(o.filterLabel||'Show',filter);
 const sort=node('select');sort.setAttribute('aria-label','Sort');for(const [value,label] of o.sorts){const opt=node('option',label);opt.value=value;sort.append(opt);}
 const sortLabel=node('label',undefined,'list-select');sortLabel.append('Sort',sort);
 const gear=iconButton('gear','Results per page'),pop=node('div',undefined,'list-pop');pop.hidden=true;
 const sizeInput=node('input');sizeInput.type='number';sizeInput.min='0';sizeInput.max='100000';sizeInput.step='1';
 const sizeLabel=node('label','Results per page');sizeLabel.append(sizeInput,node('span','0 = no limit (everything on one page).','small muted'));pop.append(sizeLabel);
 const gearWrap=node('div',undefined,'list-gear');gearWrap.append(gear,pop);
 tools.append(search,filterLabel,sortLabel,gearWrap);
 const bar=node('div',undefined,'list-bar'),rows=node('div',undefined,'list-rows'),pager=node('nav',undefined,'pager');pager.setAttribute('aria-label','Pages');
 o.el.replaceChildren(tools,bar,rows,pager);
 v.size=readPref('pageSize.'+o.name,20);sizeInput.value=v.size;
 sort.value=readPref('sort.'+o.name,o.sorts[0][0]);if(!sort.value)sort.value=o.sorts[0][0];
 gear.onclick=()=>{pop.hidden=!pop.hidden;if(!pop.hidden)sizeInput.focus();};
 document.addEventListener('click',e=>{if(!gearWrap.contains(e.target))pop.hidden=true;});
 pop.addEventListener('keydown',e=>{if(e.key==='Escape'||e.key==='Enter'){pop.hidden=true;gear.focus();}});
 sizeInput.onchange=()=>{const n=Math.max(0,Math.floor(Number(sizeInput.value)||0));sizeInput.value=n;v.size=n;savePref('pageSize.'+o.name,n);v.page=1;update();};
 let timer;search.oninput=()=>{v.page=1;clearTimeout(timer);timer=setTimeout(update,o.server?200:0);};
 filter.onchange=()=>{v.page=1;update();};sort.onchange=()=>{savePref('sort.'+o.name,sort.value);v.page=1;update();};
 const params=()=>({q:search.value.trim(),filter:filter.value,sort:sort.value,offset:v.size?(v.page-1)*v.size:0,limit:v.size});
 function setFilters(){const options=o.filters(),sig=JSON.stringify(options);if(sig===v.filterSig)return;v.filterSig=sig;const was=filter.value;filter.replaceChildren();for(const [value,label] of options){const opt=node('option',label);opt.value=value;filter.append(opt);}
  filter.value=[...filter.options].some(x=>x.value===was)?was:filter.options[0].value;}

 // Client lists: search, filter and sort here, folders first.
 function clientPage(){const q=search.value.trim(),f=filter.value,cmp=o.compare(sort.value);
  const ranked=[];for(const item of v.items){if(!o.folder(item)&&!o.match(item,f))continue;if(o.folder(item)&&f&&o.hideFoldersWhenFiltered)continue;
   const rank=fuzzyRank(q,o.label(item));if(rank!==null)ranked.push([o.folder(item)?0:1,q?rank:0,item]);}
  ranked.sort((a,b)=>a[0]-b[0]||a[1]-b[1]||cmp(a[2],b[2]));
  v.matching=ranked.map(r=>r[2]);v.total=v.matching.length;
  const start=v.size?(v.page-1)*v.size:0;return v.size?v.matching.slice(start,start+v.size):v.matching;}

 async function update(){
  const seq=++v.seq;let page;
  if(o.server){const r=await o.server(params());if(seq!==v.seq)return;v.total=r.total;page=r.items;}
  else page=clientPage();
  const pages=v.size?Math.max(1,Math.ceil(v.total/v.size)):1;
  if(v.page>pages){v.page=pages;return update();}
  const sig=JSON.stringify([page,v.total,v.page,v.size]);if(o.server&&sig===v.sig)return;v.sig=sig;
  v.shown=page;render(pages);}

 function render(pages){
  rows.replaceChildren();
  for(const item of v.shown){const r=o.row(item);r.classList.add('list-row');
   if(o.selectable(item)){const key=o.key(item),pick=node('input');pick.type='checkbox';pick.className='row-pick';pick.checked=v.selected.has(key);
    pick.setAttribute('aria-label','Select '+o.label(item));r.classList.toggle('picked',pick.checked);
    pick.onchange=()=>{pick.checked?v.selected.add(key):v.selected.delete(key);r.classList.toggle('picked',pick.checked);renderBar();};r.prepend(pick);}
   else r.prepend(node('span',undefined,'row-pick-space'));
   rows.append(r);}
  if(!v.shown.length){const empty=node('div',undefined,'empty');empty.append(node('strong',o.emptyTitle?o.emptyTitle():'Nothing here.'),node('p',search.value.trim()||filter.value?'Nothing matches your search or filter.':(o.emptyText?o.emptyText():''),'muted'));rows.append(empty);}
  renderPager(pages);renderBar();}

 function pageButton(label,page,current,aria){const b=node('button',label,current?'secondary current':'quiet');b.type='button';if(aria)b.setAttribute('aria-label',aria);if(current)b.setAttribute('aria-current','page');
  b.onclick=()=>{v.page=page;update().catch(e=>toast(e.message));rows.scrollIntoView({block:'nearest'});};return b;}
 function renderPager(pages){
  pager.replaceChildren();const noun=o.noun||['file','files'];
  const first=v.total?(v.size?(v.page-1)*v.size+1:1):0,last=v.size?Math.min(v.total,v.page*v.size):v.total;
  pager.append(node('span',v.total?first+'–'+last+' of '+v.total+' '+(v.total===1?noun[0]:noun[1]):'','small muted pager-count'));
  if(pages<=1)return;
  const prev=pageButton('‹ Previous',v.page-1,false,'Previous page');prev.disabled=v.page===1;pager.append(prev);
  const want=new Set([1,pages,v.page-2,v.page-1,v.page,v.page+1,v.page+2]);let gap=false;
  for(let p=1;p<=pages;p++){if(!want.has(p)){if(!gap)pager.append(node('span','…','muted pager-gap'));gap=true;continue;}gap=false;pager.append(pageButton(String(p),p,p===v.page,'Page '+p));}
  const next=pageButton('Next page ›',v.page+1,false);next.disabled=v.page===pages;
  const end=pageButton('Last page »',pages,false,'Jump to the last page');end.disabled=v.page===pages;pager.append(next,end);}

 async function allKeys(){if(o.server)return o.serverKeys(params());return v.matching.filter(o.selectable).map(o.key);}
 const pickableCount=()=>o.server?(o.pickableTotal?o.pickableTotal():v.total):v.matching.filter(o.selectable).length;
 function renderBar(){
  bar.replaceChildren();const pickable=v.shown.filter(o.selectable);
  if(pickable.length){const all=node('input');all.type='checkbox';all.className='row-pick';all.setAttribute('aria-label','Select every file on this page');
   const on=pickable.filter(i=>v.selected.has(o.key(i))).length;all.checked=on===pickable.length;all.indeterminate=on>0&&on<pickable.length;
   all.onchange=()=>{for(const i of pickable)all.checked?v.selected.add(o.key(i)):v.selected.delete(o.key(i));redraw();};
   const label=node('label',undefined,'check list-all');label.append(all,v.selected.size?v.selected.size+' selected':'Select page');bar.append(label);}
  if(v.selected.size){bar.append(...o.bulk([...v.selected],api_v));
   bar.append(action('Clear selection',async()=>{v.selected.clear();redraw();},'quiet'));}
  else{const total=pickableCount();
   if(total>pickable.length)bar.append(action(o.selectAllLabel?o.selectAllLabel(total):'Select all '+total,async()=>{for(const k of await allKeys())v.selected.add(k);redraw();},'quiet'));}
  bar.append(...(o.all?o.all(api_v):[]));}
 const redraw=()=>render(v.size?Math.max(1,Math.ceil(v.total/v.size)):1);

 const api_v={
  set(items){v.items=items;v.selected.clear();v.sig=null;v.page=1;setFilters();return update();},
  update(){return update();},
  refilter(){setFilters();},
  clear(){v.selected.clear();v.sig=null;redraw();},
  redraw(){redraw();},
  keys:allKeys,
  get total(){return v.total;},
  get pickable(){return pickableCount();},
  resetSearch(){search.value='';v.page=1;},
  get filter(){return filter.value;},
  get query(){return search.value.trim();},
  get selected(){return v.selected;}};
 setFilters();
 return api_v;}

// Queue many files in chunks; stops if the queue is full. Returns [queued, errors].
async function queueMany(path,paths,extra={}){
 let count=0;const errors=[];
 for(let i=0;i<paths.length;i+=1000){const r=await api(path,{paths:paths.slice(i,i+1000),...extra});count+=r.count??r.queued;errors.push(...r.errors);
  if(r.errors.some(e=>/Queue is full/.test(e.error)))break;}
 return [count,errors];}
function queuedMessage(count,total,errors,noun){
 let text=count===total?(count===1?'1 '+noun+' added to the queue.':count+' '+noun+'s added to the queue.'):count+' of '+total+' '+noun+'s added to the queue.';
 if(errors.length)text+=' '+errors.length+' could not be queued: '+errors[0].error+(errors.length>1?' (and others)':'')+'.';
 return text;}
