// Song lyrics: browse songs, queue lyrics jobs, and preview/edit the .lrc files they make.
// Playing along is left to music apps (Jellyfin, Navidrome, Plexamp, karaoke players).
// Uses $, node, action, api and toast from app.js.
let editingLyrics=null;

// Same list as Media library: status, search, filter, pages and Make lyrics for selected songs.
const musicList=listView({el:$('#music-files'),name:'music',placeholder:'Search this folder as you type',noun:['item','items'],
 filters:()=>[['','All songs'],['missing','Missing lyrics'],['plain','Missing word-by-word (enhanced) lyrics'],['has','Has lyrics']],
 sorts:[['name','Name A–Z'],['name_desc','Name Z–A'],['missing','Missing lyrics first']],
 key:f=>f.path,label:f=>f.name,path:f=>f.path,folder:f=>f.directory,
 match:(f,flt)=>{const s=f.status;if(!flt)return true;if(!s)return false;return flt==='missing'?!s.lyrics:flt==='plain'?!s.enhanced:s.lyrics;},
 compare:sort=>sort==='name_desc'?(a,b)=>byName(b,a):sort==='missing'?(a,b)=>(lyricScore(a)-lyricScore(b))||byName(a,b):byName,
 selectable:f=>!f.directory,
 emptyText:()=>noLibraries()?NO_LIBRARIES:'No songs or folders here.',
 row:musicRow,
 bulk:(keys,view)=>[action('Make lyrics for selected ('+keys.length+')',async()=>{const [n,errors]=await queueMany('/lyrics/batch',keys,{regenerate:$('#lyrics-regenerate').checked});view.clear();toast(queuedMessage(n,keys.length,errors,'song'));},'primary')],
 all:view=>view.pickable?[action('Make lyrics for all '+view.pickable+(view.filter||view.query?' shown':''),async()=>{const keys=await view.keys();if(keys.length>10&&!confirm('Make lyrics for '+keys.length+' songs?'))return;const [n,errors]=await queueMany('/lyrics/batch',keys,{regenerate:$('#lyrics-regenerate').checked});toast(queuedMessage(n,keys.length,errors,'song'));},'secondary')]:[]});
function lyricScore(f){return !f.status?3:!f.status.lyrics?0:!f.status.enhanced?1:2;}
function musicRow(file){const row=node('div',undefined,'file-row'),text=node('div',undefined,'file-main');
 text.append(node('span',file.directory?file.name+'/':file.name,file.directory?'folder-name':'file-name'));
 if(file.status)text.append(statusChips('song',file.status));
 row.append(text);
 if(file.directory)row.append(action('Open',async()=>{$('#music-path').value=file.path;await browseMusic();}));
 else row.append(action('Make lyrics',async()=>{await api('/lyrics',{path:file.path,regenerate:$('#lyrics-regenerate').checked});toast('Added to the queue. The lyrics files appear next to the song when it finishes.');}));
 return row;}
async function browseMusic(){
 if(noLibraries()){$('#music-note').hidden=true;return musicList.set([]);}
 const path=$('#music-path').value.trim(),data=await api(withLibrary('/music/browse?path='+encodeURIComponent(path)));
 $('#music-up').disabled=!path.split('/').filter(Boolean).length;$('#music-note').hidden=!data.truncated;await musicList.set(data.entries);
}
$('#music-form').onsubmit=e=>{e.preventDefault();browseMusic().catch(e=>toast(e.message));};
$('#music-up').onclick=()=>{const parts=$('#music-path').value.split('/').filter(Boolean);parts.pop();$('#music-path').value=parts.join('/');browseMusic().catch(e=>toast(e.message));};

const LYRIC_FILES={lrc:'Download .lrc',txt:'Download .txt',words:'Download word-by-word .lrc'};
function lyricLinks(id,files){return files.filter(f=>LYRIC_FILES[f]).map(f=>{const a=node('a',LYRIC_FILES[f],'download-link');a.href='/api/lyrics/'+id+'/file/'+f+'?download=true';return a;});}
function lyricsControls(job,controls){
 if(job.status!=='completed'||!job.outputs.lrc)return;
 controls.append(action('Preview & edit lyrics',()=>openLyrics(job),'secondary'),...lyricLinks(job.id,Object.keys(job.outputs)));
}
async function openLyrics(job){
 const r=await fetch('/api/lyrics/'+job.id+'/file/lrc',{credentials:'same-origin'});
 if(!r.ok)throw Error('Could not load the lyrics file');
 // Show just the words: word times (<01:23.45>) are kept for lines you don't change.
 $('#lyrics-text').value=(await r.text()).split('\n').map(l=>l.replace(/<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>/g,'').replace(/^(\[[^\]]*\])\s+/,'$1').replace(/ {2,}/g,' ').trimEnd()).join('\n');editingLyrics=job.id;
 $('#lyrics-files').replaceChildren(...lyricLinks(job.id,Object.keys(job.outputs)));
 $('#lyrics-editor').showModal();
}
$('#save-lyrics').onclick=async()=>{try{const r=await api('/lyrics/'+editingLyrics,{text:$('#lyrics-text').value});$('#lyrics-editor').close();toast('Saved '+r.lrc.split('/').pop()+' and the plain lyrics.');await refresh();}catch(e){toast(e.message);}};
