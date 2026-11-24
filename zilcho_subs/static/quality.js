// Subtitle quality (Queue > Completed): words, lines (segments), how sure the speech model
// was, and a rating for every subtitle file a finished job wrote. Uses $, node, api and
// toast from app.js; app.js calls loadQuality() when Completed opens.
const QUALITY_PAGE=25;
const QUALITY_ORDER=['Excellent','Good','Fair','Poor','Not recorded','File missing'];
const QUALITY_CLASS={Excellent:'excellent',Good:'good',Fair:'fair',Poor:'poor','Not recorded':'none','File missing':'missing'};
let qualityOffset=0,qualityData=null,qualityBusy=false;

const fmtCount=n=>n===null||n===undefined?'—':Number(n).toLocaleString();
function fmtWhen(seconds){try{return new Date(seconds*1000).toLocaleString([], {dateStyle:'medium',timeStyle:'short'});}catch(_){return '';}}

async function loadQuality(offset=qualityOffset){
 if(qualityBusy)return;qualityBusy=true;
 const box=$('#quality-list');
 try{
  qualityData=await api(withLibrary('/quality?limit='+QUALITY_PAGE+'&offset='+Math.max(0,offset)));
  qualityOffset=qualityData.offset;renderQuality();
 }catch(e){toast(e.message);box.replaceChildren(node('p','Could not load the quality report: '+e.message,'error-text'));}
 finally{qualityBusy=false;}
}

function qualityCell(row,label,value,cls){const td=node('td',value,cls);td.dataset.label=label;row.append(td);return td;}

// Red (0) to green (120) by score. The meter is 0-100; 0% is a real score and shows an empty bar.
function confidenceColor(percent){return 'hsl('+Math.round(Math.max(0,Math.min(100,percent))*1.2)+',70%,52%)';}

function confidenceCell(row,f,job){
 const td=qualityCell(row,'Transcription confidence','', 'q-num');
 if(f.confidence===null||f.confidence===undefined){td.append(node('span','no score','muted small'));
  td.title=f.status==='File missing'?'The file is no longer there.':job.kind==='text'?'Translated from an existing SRT: no speech was heard, so there is no transcription confidence.':'Made before Zilcho Subs recorded confidence.';return;}
 const meter=node('span','','q-meter');meter.setAttribute('aria-hidden','true');
 const fill=node('span','','q-fill');fill.style.width=Math.min(100,f.confidence)+'%';fill.style.background=confidenceColor(f.confidence);meter.append(fill);
 const both=node('span','','q-conf');both.append(node('span',f.confidence.toFixed(1)+'%','q-pct'),meter);td.append(both);
 td.title='Transcription confidence: how sure the speech model was.'+(job.kind==='text'?'':' Translated languages reuse this number; it does not measure translation accuracy.');
}

function renderQuality(){
 const data=qualityData,filter=$('#quality-filter').value,box=$('#quality-list');
 for(const name of QUALITY_ORDER){const el=document.querySelector('[data-quality-count="'+name+'"]');if(el)el.textContent=fmtCount(data.counts[name]||0);}
 const first=data.total?data.offset+1:0,last=Math.min(data.total,data.offset+data.jobs.length);
 $('#quality-range').textContent=data.total?'Jobs '+first+'–'+last+' of '+data.total.toLocaleString():'';
 $('#quality-prev').disabled=data.offset<=0;$('#quality-next').disabled=last>=data.total;
 if(!data.total){box.replaceChildren(node('p','No finished subtitle jobs yet. Press Make subtitles on a video in Library; its quality shows up here when the job completes.','muted empty'));return;}
 const table=node('table','','q-table'),head=node('tr');
 for(const name of ['File','Language','Words','Segments','Transcription confidence','Status',''])head.append(node('th',name));
 table.append(node('thead'));table.tHead.append(head);
 const body=node('tbody');let shown=0;
 for(const job of data.jobs){
  const files=job.files.filter(f=>!filter||f.status===filter);if(!files.length)continue;
  const group=node('tr','','q-group'),cell=node('td');cell.colSpan=7;
  cell.append(node('span',job.source||'(uploaded SRT)','q-source mono'),node('span',(job.kind==='text'?'Translated SRT · ':'')+fmtWhen(job.finished),'muted small'));
  group.append(cell);body.append(group);
  for(const f of files){
   const row=node('tr','','q-row');shown++;
   const name=qualityCell(row,'File','','q-file');name.append(node('strong',f.file));name.title=f.path;
   qualityCell(row,'Language',f.language.toUpperCase(),'mono');
   qualityCell(row,'Words',fmtCount(f.words),'q-num');
   qualityCell(row,'Segments',fmtCount(f.segments),'q-num');
   confidenceCell(row,f,job);
   qualityCell(row,'Status','').append(node('span',f.status,'q-status '+QUALITY_CLASS[f.status]));
   const act=qualityCell(row,'Preview','','q-act');
   if(f.status!=='File missing'){const b=node('button','Preview','small');b.type='button';b.title='Preview '+f.language.toUpperCase()+' subtitles';b.onclick=()=>openEditor(job.id,f.language).catch(e=>toast(e.message));act.append(b);}
   body.append(row);
  }
 }
 table.append(body);
 box.replaceChildren(shown?table:node('p','No files on this page are rated '+filter+'.','muted empty'));
}

$('#quality-filter').onchange=()=>qualityData&&renderQuality();
$('#quality-refresh').onclick=()=>loadQuality();
$('#quality-prev').onclick=()=>loadQuality(qualityOffset-QUALITY_PAGE);
$('#quality-next').onclick=()=>loadQuality(qualityOffset+QUALITY_PAGE);
