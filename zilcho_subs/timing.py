"""Anchor generated cues to original, bounded speech intervals, never text-length slices."""
import math
import json
import re
from pathlib import Path

SAMPLE_RATE=16000
MAX_UTTERANCE=8.0
TIMING_VERSION='speech-intervals-v3'  # v3: cues keep word times for sentence splits

# Reading time. Cue *starts* stay on the detected speech; only how long a cue
# stays on screen is extended so it can be read. Measured on a real episode:
# speech-bounded cues lasted ~1.9 s against ~2.8 s for the broadcaster's own
# subtitles and vanished ~0.5 s early, which reads as "out of sync".
READING_CPS=17.0      # characters per second, a common subtitling guideline
MIN_CUE=1.0           # shortest time any line stays up
LINGER=0.4            # keep a line briefly after the speaker stops
MAX_HOLD=2.0          # never keep a line more than this past its speech
MAX_CUE=7.0           # reading time never stretches a cue beyond this
CUE_GAP=0.084         # leave ~2 frames before the next line appears

# Phrases speech models invent over silence or music (video-site outros), not
# dialogue. Matched only when they are essentially the whole line.
_HALLUCINATION=re.compile(
    r"^(?:thank(?:s| you)(?: (?:so|very) much)? for (?:watching|viewing|your viewing|listening)"
    r"|(?:please )?(?:like and )?subscribe(?: to (?:my|our|the) channel)?(?: and .*)?"
    r"|(?:please )?subscribe to (?:my|our|the) channel.*"
    r"|see you (?:in the )?next (?:video|time)"
    r"|(?:subtitles?|captions?|translated|transcribed) by .*"
    r"|ご視聴ありがとうございました|チャンネル登録.*|高評価.*)$")


def is_hallucination(text):
    """True for stock phrases a speech model makes up over silence or music."""
    normalized=re.sub(r"[\s\.,!?…。、！？「」『』\"'~〜]+"," ",str(text).lower()).strip()
    return bool(normalized) and len(normalized)<=90 and bool(_HALLUCINATION.match(normalized))


def low_confidence(segment):
    """faster-whisper's own signals that a segment is probably not real speech."""
    no_speech=float(getattr(segment,'no_speech_prob',0) or 0)
    logprob=float(getattr(segment,'avg_logprob',0) or 0)
    compression=float(getattr(segment,'compression_ratio',0) or 0)
    return (no_speech>.6 and logprob<-1.0) or compression>2.4


def readable_cues(cues,duration=None):
    """Drop made-up lines and give each line enough time on screen to read.

    Starts never move. An end only grows: to at least the reading time for its
    text (with a short linger), never past the next line, never more than
    MAX_HOLD after the speech, and never past the end of the media.
    """
    kept=[c for c in cues if not is_hallucination(c['text'])]
    result=[]
    for i,cue in enumerate(kept):
        start,end=float(cue['start']),float(cue['end'])
        chars=len(' '.join(str(cue['text']).split()))
        want=max(end+LINGER,start+max(MIN_CUE,chars/READING_CPS))
        want=min(want,end+MAX_HOLD,max(end+LINGER,start+MAX_CUE))
        if i+1<len(kept):want=min(want,float(kept[i+1]['start'])-CUE_GAP)
        if duration:want=min(want,float(duration))
        result.append(dict(cue,end=max(end,want)))
    return result


# Splitting. A cue holding several sentences that runs past one subtitle line is
# split at sentence ends, as broadcast subtitles are. Measured on an English
# track: ~45% of cues were longer than one line and stayed up ~3.3 s against
# ~2.5 s for the official subtitles.
LINE_WIDTH=42         # one subtitle line; shorter cues are left alone
MIN_PIECE=10          # a sentence shorter than this stays with its neighbour
_SENTENCE_END=re.compile(r"(?:[.!?]|\.\.\.|…|[。！？])[\"'”’)\]」』]*$")
_CLAUSE_END=re.compile(r"[,;:.!?…、。！？][\"'”’)\]」』]*$")
_JOINING_WORD=re.compile(r"(?:and|but|or|so|because|as|if|when|while|until|that|which|who|where|with|without|before|after|than)$",re.I)
_ABBREVIATION=re.compile(r"(?:^|\s)(?:mr|mrs|ms|dr|st|sr|jr|vs|etc|prof|gen|capt|lt|sgt|no)\.$",re.I)


def display_width(text):
    """Subtitle width: full-width (CJK) characters count double."""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in ' '.join(str(text).split()))


def _ends_sentence(text):
    text=text.strip()
    return bool(_SENTENCE_END.search(text)) and not _ABBREVIATION.search(text)


def _join_short(pieces):
    """Merge sentences too short to stand alone into a neighbour (prefer the one before)."""
    merged=[]
    for piece in pieces:
        if merged and (display_width(piece[0])<MIN_PIECE or display_width(merged[-1][0])<MIN_PIECE):
            merged[-1]=(merged[-1][0]+' '+piece[0],merged[-1][1]+piece[1])
        else:merged.append(piece)
    return merged


def split_sentences(cues):
    """Split long multi-sentence cues at sentence ends.

    Each piece starts and ends on its own recognized words, so only cues shown in
    the language they were spoken in (with word times) are split.
    """
    result=[]
    for cue in cues:
        text=' '.join(str(cue['text']).split())
        if display_width(text)<=LINE_WIDTH:result.append(cue);continue
        words=cue.get('words')
        if words and ''.join(''.join(w[2] for w in words).split())==''.join(text.split()):
            pieces=[];group=[]
            for i,word in enumerate(words):
                group.append(word)
                if i+1<len(words) and _ends_sentence(word[2]):pieces.append(group);group=[]
            if group:pieces.append(group)
            pieces=_join_short([(''.join(w[2] for w in g).strip(),g) for g in pieces])
            if len(pieces)<2:result.append(cue);continue
            for _,group in pieces:
                result.append({'start':float(group[0][0]),'end':float(group[-1][1]),'text':''.join(w[2] for w in group).strip(),'words':group})
            continue
        # No word times (a translation, or text that no longer matches its words):
        # leave it whole. Timing comes from real speech, never a length guess.
        result.append(cue)
    return result


def speech_cues(segments,start,end,task):
    """Use source-word alignment for transcription; translation keeps the speech envelope.

    Cross-language word alignment is not trustworthy. Each translated utterance is
    decoded separately, so it cannot bridge a long silent gap in the source audio.
    """
    # Skip what the model itself marks as non-speech, and stock made-up phrases.
    segments=[s for s in segments if not low_confidence(s) and not is_hallucination(s.text)]
    text=' '.join(s.text.strip() for s in segments if s.text.strip())
    if not text:return []
    fallback=[{'start':start,'end':end,'text':text}]
    if task=='translate':return fallback
    words=[];previous=0.0
    for segment in segments:
        aligned=getattr(segment,'words',None)
        if segment.text.strip() and not aligned:return fallback
        for word in aligned or []:
            a,b=float(word.start),float(word.end)
            if not math.isfinite(a) or not math.isfinite(b) or not 0<=a<b<=end-start+.02:
                return fallback
            # Compare the model's own relative times. Converting to absolute time and
            # back adds float error, so touching words (the usual case) looked like
            # overlaps and almost every line fell back to the whole utterance.
            if a<previous-1e-6:return fallback
            previous=b
            if word.word.strip():words.append({'start':start+a,'end':min(end,start+b),'text':word.word})
    if not words:return fallback
    # Never discard recognized words if a backend provides incomplete alignment.
    if ''.join(''.join(w['text'] for w in words).split())!=''.join(text.split()):return fallback
    cues=[];group=[]
    def flush():
        # Word times travel with the cue so lines can later be split at sentence ends.
        if group:cues.append({'start':group[0]['start'],'end':group[-1]['end'],'text':''.join(w['text'] for w in group).strip(),
            'words':[[round(w['start'],3),round(w['end'],3),w['text']] for w in group]})
    for word in words:
        if group and word['start']-group[-1]['end']>.6:
            flush();group=[]
        elif group and (word['end']-group[0]['start']>6 or len(''.join(w['text'] for w in group)+word['text'])>84):
            # Too long for one cue: cut after the last clause end (comma, sentence end)
            # in the second half rather than between any two words.
            # Failing that, cut before the first joining word ("as", "and", "because"...)
            # there, so the clause it starts stays whole.
            # Candidate cuts are before word i, never in the first half.
            half=range(max(1,len(group)//2),len(group))
            cut=next((i for i in reversed(half) if _CLAUSE_END.search(group[i-1]['text'].strip())),None) \
                or next((i for i in half if _JOINING_WORD.match(group[i]['text'].strip())),len(group))
            rest=group[cut:];group=group[:cut];flush();group=rest
        group.append(word)
    flush()
    return cues


class SpeechCheckpoint:
    def __init__(self,path,key):self.path=Path(path);self.key=key
    def load(self):
        if not self.path.exists():return None
        state=json.loads(self.path.read_text(encoding='utf-8'))
        if state.get('key')!=self.key:raise ValueError('Paused source/model changed; queue a new job rather than reusing its checkpoint')
        return state
    def save(self,state):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(state,key=self.key),ensure_ascii=False),encoding='utf-8');temp.replace(self.path)


def recognize_utterances(model,audio,settings,task,progress,get_intervals,checkpoint=None):
    intervals=get_intervals(audio,max_speech_duration_s=MAX_UTTERANCE,min_silence_duration_ms=400,min_speech_duration_ms=100,speech_pad_ms=150)
    if not intervals:raise ValueError('No speech detected; no subtitle file was written')
    saved=checkpoint.load() if checkpoint else None
    intervals=[{'start':int(i['start']),'end':int(i['end'])} for i in intervals]
    if saved and (saved.get('intervals')!=intervals or saved.get('samples')!=len(audio) or saved.get('task')!=task):
        raise ValueError('Audio differs from paused checkpoint; queue a new job')
    language=saved['language'] if saved else settings.source_language
    if language=='auto':
        # Detect from speech, rather than a potentially silent opening. Decode all
        # later utterances with that same source language; do not re-detect each line.
        first=intervals[0]['start'];last=min(len(audio),first+30*SAMPLE_RATE)
        language=model.detect_language(audio=audio[first:last])[0]
    actual_task='transcribe' if language=='en' and task=='translate' else task
    completed=int(saved['completed']) if saved else 0
    if not 0<=completed<=len(intervals):raise ValueError('Invalid speech checkpoint')
    result=list(saved['segments']) if saved else [];previous=intervals[completed-1]['end'] if completed else 0
    def save(done):
        if checkpoint:checkpoint.save({'completed':done,'segments':result,'language':language,'task':task,'samples':len(audio),'intervals':intervals})
    save(completed)
    for index in range(completed,len(intervals)):
        interval=intervals[index]
        a,b=int(interval['start']),int(interval['end'])
        if not 0<=a<b<=len(audio) or a<previous or (b-a)/SAMPLE_RATE>MAX_UTTERANCE+1:
            raise ValueError('Speech detector returned invalid or unbounded intervals')
        progress(a/SAMPLE_RATE)
        segments,_=model.transcribe(audio[a:b],language=language,task=actual_task,vad_filter=False,
            word_timestamps=actual_task=='transcribe',condition_on_previous_text=False,beam_size=5)
        result.extend(speech_cues(list(segments),a/SAMPLE_RATE,b/SAMPLE_RATE,actual_task))
        previous=b;save(index+1);progress(b/SAMPLE_RATE)
    return {'language':language,'segments':result,'timing_strategy':TIMING_VERSION,
        'alignment':'source words / speech-envelope translation','speech_interval_count':len(intervals)}
