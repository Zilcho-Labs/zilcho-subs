// Video picker for Translate subtitles: browse the collections' folders, click a video, its path fills #srt-video.
// Typing the path still works, and leaving the box empty queues the translation without a video.
let videoPickerPath='';
async function loadVideoPicker(path){
 const data=await api(withLibrary('/browse?path='+encodeURIComponent(path)));
 // Only after the server answered: a failed folder leaves the last good one showing.
 videoPickerPath=data.path;
 $('#video-current').textContent='/'+(videoPickerPath?videoPickerPath:'')+(videoPickerPath?'':' (main media folder)');
 $('#video-up').disabled=!videoPickerPath;
 const list=$('#video-list');list.replaceChildren();
 const folders=data.entries.filter(e=>e.directory),videos=data.entries.filter(e=>!e.directory&&!e.subtitle);
 for(const f of folders){const row=node('div',undefined,'file-row'),open=node('button',f.name+'/','secondary');open.type='button';open.onclick=()=>loadVideoPicker(f.path).catch(e=>toast(e.message));row.append(open);list.append(row);}
 for(const v of videos){const row=node('div',undefined,'file-row'),pick=node('button',v.name,'primary');pick.type='button';pick.onclick=()=>chooseVideo(v.path);row.append(pick);list.append(row);}
 if(!videos.length)list.append(node('p','No videos in this folder.','muted empty'));
 if(data.truncated)list.append(node('p','Only the first 5,000 entries are shown.','small muted'));}
function chooseVideo(path){$('#srt-video').value=path;$('#video-picker').close();toast('Video chosen: '+path);}
function videoPickerStart(){
 // Start beside the chosen SRT when there is one; if that folder isn't available, start at the top.
 const srt=$('#srt-path').value.trim().replace(/^\/+/,'');
 return srt.includes('/')?srt.split('/').slice(0,-1).join('/'):'';}
async function openVideoPicker(){
 try{try{await loadVideoPicker(videoPickerStart());}catch(e){await loadVideoPicker('');}
  $('#video-picker').showModal();}catch(e){toast(e.message);}}
function initVideoPicker(){
 $('#srt-video-browse').onclick=openVideoPicker;
 $('#srt-video-clear').onclick=()=>{$('#srt-video').value='';};
 $('#video-up').onclick=()=>loadVideoPicker(videoPickerPath.split('/').slice(0,-1).join('/')).catch(e=>toast(e.message));}
if(typeof document!=='undefined'&&document.getElementById('srt-video-browse'))initVideoPicker();
