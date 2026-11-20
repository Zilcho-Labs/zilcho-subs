// Libraries: named sets of folders. This file has the library manager (on the Monitor
// page) and the library dropdown on Media library, Song lyrics, Monitor, Coverage and
// Quality. The choice is shared by those pages and remembered between visits; empty
// means "All libraries". Uses $, node, api, toast, action, refresh, state, view,
// readPref and savePref from the other scripts. Files outside every library are
// hidden by the server, so nothing here has to filter them.
const WHOLE_WARNING='Searching this library scans the entire folder.';
const NO_LIBRARIES='No libraries yet. Add one on the Monitor page; files outside every library never show here.';
let librariesLoaded=false,libraries=[],libraryChoice=readPref('library',''),libraryEditing=null;

// ----- helpers (no page needed) -----
function noLibraries(){return librariesLoaded&&!libraries.length;}
function libraryParam(){return libraryChoice?'library='+encodeURIComponent(libraryChoice):'';}
function withLibrary(path){const p=libraryParam();return p?path+(path.includes('?')?'&':'?')+p:path;}
// A remembered choice only counts while that library still exists.
function pickLibrary(list,saved){return saved&&pickableLibraries(list).some(l=>l.id===saved)?saved:'';}
// The library made by an upgrade covers the whole media folder, which is what "All
// libraries" already shows, so the dropdowns leave it out (it stays in the manager).
function isStarterLibrary(l){return l.id==='default'&&l.whole_folder;}
function pickableLibraries(list){return list.filter(l=>!isStarterLibrary(l));}
function libraryLabel(l){return isStarterLibrary(l)&&l.name==='Library'?'Everything (main folder)':l.name;}
function setLibraryChoice(id){libraryChoice=id||'';savePref('library',libraryChoice);}
function parseFolders(text){const seen=new Set();for(const line of String(text).split('\n')){const f=line.trim().replace(/^\/+|\/+$/g,'');if(f)seen.add(f);}return [...seen];}
// Form values → request body, or {error}. "Whole main folder" is the empty folder.
function libraryBody(name,foldersText,whole){
 name=String(name).trim();if(!name)return {error:'Give the library a name.'};
 const folders=whole?['']:parseFolders(foldersText);
 if(!folders.length)return {error:'Add at least one folder, or use the whole main folder.'};
 return {body:{name,folders,whole_folder:!!whole}};}
function describeLibrary(l){return l.whole_folder?'Whole main folder':l.folders.filter(Boolean).join(', ');}

// ----- dropdowns -----
function renderLibraryPickers(){
 const current=libraries.find(l=>l.id===libraryChoice);
 for(const select of document.querySelectorAll('.library-select')){
  select.replaceChildren(...[['','All libraries'],...pickableLibraries(libraries).map(l=>[l.id,l.name])].map(([value,label])=>{const o=node('option',label);o.value=value;return o;}));
  select.value=libraryChoice;}
 for(const note of document.querySelectorAll('.library-note')){note.hidden=!(current&&current.whole_folder);note.textContent=WHOLE_WARNING;}}
// Each page reloads its own list when the library changes; folders reset because the
// old folder may not be in the new library.
function reloadForLibrary(){
 for(const id of ['browse-path','music-path'])$('#'+id).value='';
 if(view==='library')browse().catch(e=>toast(e.message));
 else if(view==='music')browseMusic().catch(e=>toast(e.message));
 else if(view==='monitor'){monitorList.clear();quietly();}
 else if(view==='quality')loadQuality(0);
 else if(view==='coverage')loadCoverage();
 else if(typeof quietly==='function')quietly();}
function chooseLibrary(id){setLibraryChoice(id);renderLibraryPickers();reloadForLibrary();}

// ----- manager -----
async function loadLibraries(){
 const data=await api('/libraries');libraries=data.libraries;librariesLoaded=true;
 const kept=pickLibrary(libraries,libraryChoice);if(kept!==libraryChoice)setLibraryChoice(kept);
 renderLibraryPickers();renderLibraryList();}
function renderLibraryList(){
 const list=$('#library-list');list.replaceChildren();
 for(const lib of libraries){
  const row=node('div',undefined,'file-row library-row'),text=node('div',undefined,'file-main');
  text.append(node('strong',libraryLabel(lib)),node('span',describeLibrary(lib),'mono small muted'));
  if(isStarterLibrary(lib))text.append(node('span','Shows everything, so no folders are hidden. Delete it once your own libraries are set up. "All libraries" then shows just your libraries.','small library-warning'));
  else if(lib.whole_folder)text.append(node('span',WHOLE_WARNING,'small library-warning'));
  const edit=node('button','Rename or change folders','secondary');edit.type='button';edit.onclick=()=>editLibrary(lib);
  const del=node('button','Delete','quiet');del.type='button';del.onclick=()=>deleteLibrary(lib);
  row.append(text,edit,del);list.append(row);}
 if(!libraries.length)list.append(node('p','No libraries yet. Until you make one, nothing shows on the list pages.','muted empty'));}
function editLibrary(lib){
 libraryEditing=lib?lib.id:null;
 $('#library-name').value=lib?libraryLabel(lib):'';$('#library-folders').value=lib?lib.folders.filter(Boolean).join('\n'):'';
 $('#library-whole').checked=!!(lib&&lib.whole_folder);renderChosen();syncWhole();
 $('#library-save').textContent=lib?'Save changes':'Add library';$('#library-cancel').hidden=!lib;
 $('#library-name').focus();}
function syncWhole(){const whole=$('#library-whole').checked;$('#library-add-folder').disabled=whole;$('#library-chosen').classList.toggle('muted',whole);$('#library-whole-warning').hidden=!whole;}

// ----- folder picker: choose folders by browsing instead of typing their names -----
// The chosen folders live in the hidden #library-folders box, one per line.
let pickerPath='';
function chosenFolders(){return parseFolders($('#library-folders').value);}
function addChosenFolder(path){const list=chosenFolders();
 // A folder already covered by a chosen parent adds nothing.
 if(list.some(f=>path===f||path.startsWith(f+'/')))return false;
 // A parent swallows chosen folders inside it.
 $('#library-folders').value=[...list.filter(f=>!f.startsWith(path+'/')),path].join('\n');renderChosen();return true;}
function removeChosenFolder(path){$('#library-folders').value=chosenFolders().filter(f=>f!==path).join('\n');renderChosen();}
function renderChosen(){
 const box=$('#library-chosen'),list=chosenFolders();box.replaceChildren();
 for(const f of list){const chip=node('span',undefined,'library-chip'),x=node('button','×','quiet');x.type='button';x.setAttribute('aria-label','Remove '+f);x.onclick=()=>removeChosenFolder(f);chip.append(node('span',f,'mono'),x);box.append(chip);}
 if(!list.length)box.append(node('span','No folders yet. Click + Add folder.','small muted'));
 $('#library-add-folder').textContent=list.length?'+ Add another folder':'+ Add folder';}
async function loadPicker(path){
 const data=await api('/folders?path='+encodeURIComponent(path));pickerPath=data.path;
 $('#folder-current').textContent='/'+(pickerPath?pickerPath:'')+(pickerPath?'':' (main media folder)');
 $('#folder-up').disabled=!pickerPath;$('#folder-add').disabled=!pickerPath;
 const list=$('#folder-list');list.replaceChildren();
 for(const f of data.folders){const row=node('div',undefined,'file-row'),open=node('button',f.name+'/','secondary');open.type='button';open.onclick=()=>loadPicker(f.path).catch(e=>toast(e.message));row.append(open);list.append(row);}
 if(!data.folders.length)list.append(node('p','No folders in here. Add this folder, or go up.','muted empty'));
 if(data.truncated)list.append(node('p','Only the first 2,000 folders are shown.','small muted'));}
function openPicker(){loadPicker('').then(()=>$('#folder-picker').showModal()).catch(e=>toast(e.message));}
async function deleteLibrary(lib){
 if(!confirm('Delete the library "'+lib.name+'"?\n\nYour files are never deleted; they just stop showing on the list pages unless another library has their folder.'))return;
 try{await api('/libraries/'+encodeURIComponent(lib.id),undefined,'DELETE');if(libraryEditing===lib.id)editLibrary(null);
  await afterLibraryChange();toast('Deleted the library "'+lib.name+'". No files were touched.');}catch(e){toast(e.message);}}
async function afterLibraryChange(){await loadLibraries();settingsLoaded=false;await refresh();reloadForLibrary();}

function initLibraryUi(){
 $('#library-whole').onchange=syncWhole;
 $('#library-add-folder').onclick=openPicker;
 $('#folder-up').onclick=()=>loadPicker(pickerPath.split('/').slice(0,-1).join('/')).catch(e=>toast(e.message));
 $('#folder-add').onclick=()=>{const path=pickerPath;if(!path)return;
  if(!addChosenFolder(path))toast('"'+path+'" is already included.');$('#folder-picker').close();};
 $('#library-cancel').onclick=()=>editLibrary(null);
 $('#library-form').onsubmit=async e=>{e.preventDefault();
  const r=libraryBody($('#library-name').value,$('#library-folders').value,$('#library-whole').checked);
  if(r.error){toast(r.error);return;}
  try{const saved=libraryEditing?await api('/libraries/'+encodeURIComponent(libraryEditing),r.body,'PUT'):await api('/libraries',r.body);
   editLibrary(null);await afterLibraryChange();toast('Saved the library "'+saved.name+'".');}catch(error){toast(error.message);}};
 for(const select of document.querySelectorAll('.library-select'))select.onchange=()=>chooseLibrary(select.value);
 renderChosen();syncWhole();}
if(typeof document!=='undefined'&&document.getElementById('library-form')){initLibraryUi();
 // Wait for the login: lists load only after the app does.
 const libraryWait=setInterval(()=>{if(typeof state!=='undefined'&&state){clearInterval(libraryWait);loadLibraries().catch(e=>toast(e.message));}},150);}
