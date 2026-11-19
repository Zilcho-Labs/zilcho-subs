// Song lyrics: browse songs, queue lyrics jobs, and preview/edit the .lrc files they make.
// Playing along is left to music apps (Jellyfin, Navidrome, Plexamp, karaoke players).
// Uses $, node, action, api and toast from app.js.
let editingLyrics=null;

async function browseMusic(){
 const data=await api('/music/browse?path='+encodeURIComponent($('#music-path').value.trim()));
 const list=$('#music-files');list.replaceChildren();
 for(const file of data.entries){
  const row=node('div',undefined,'file-row');const name=node('span',file.name,file.directory?'folder-name':'');
  if(file.lyrics)name.append(' ',node('span','has lyrics','pill completed'));
  row.append(name);
  if(file.directory)row.append(action('Open',async()=>{$('#music-path').value=file.path;await browseMusic();}));
  else row.append(action('Make lyrics',async()=>{await api('/lyrics',{path:file.path,regenerate:$('#lyrics-regenerate').checked});toast('Added to the queue. The lyrics files appear next to the song when it finishes.');}));
  list.append(row);
 }
 if(!data.entries.length)list.append(node('p','No songs or folders here.','muted'));
 if(data.truncated)list.append(node('p','Showing a limited listing. Browse a smaller folder.','notice'));
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
