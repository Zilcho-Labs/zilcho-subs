// Lyrics: preview/edit the .lrc files a lyrics job makes, and the download links. Songs are
// browsed and queued from the Library page (library.js); the Queue shows these controls on
// finished lyrics jobs. Playing along is left to music apps (Jellyfin, Navidrome, Plexamp,
// karaoke players). Uses $, node, action, api and toast from app.js.
let editingLyrics=null;

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
