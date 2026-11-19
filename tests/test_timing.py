from types import SimpleNamespace as N
import pytest
from zilcho_subs.core import Settings,render_srt,validate_srt
from zilcho_subs.timing import recognize_utterances,speech_cues,SAMPLE_RATE


def segment(text,start=0,end=200,words=None):return N(text=text,start=start,end=end,words=words)
def word(text,start,end):return N(word=text,start=start,end=end)


class Model:
    def __init__(self,segments,language='ja'):self.segments=segments;self.calls=[];self.detections=0;self.language=language
    def detect_language(self,**kwargs):self.detections+=1;return self.language,.99,[]
    def transcribe(self,audio,**kwargs):self.calls.append(kwargs);return iter(self.segments),N(language=self.language)


def test_translation_cannot_bridge_intro_gap_or_trust_huge_model_span():
    model=Model([segment('How are things? Any clues?')])
    intervals=[{'start':int(9.5*SAMPLE_RATE),'end':13*SAMPLE_RATE},{'start':172*SAMPLE_RATE,'end':177*SAMPLE_RATE}]
    result=recognize_utterances(model,range(180*SAMPLE_RATE),Settings(source_language='ja'),'translate',lambda _:None,lambda *a,**k:intervals)
    assert [(s['start'],s['end']) for s in result['segments']]==[(9.5,13),(172,177)]
    assert all(c['task']=='translate' and c['word_timestamps'] is False and c['vad_filter'] is False for c in model.calls)
    output=render_srt(result['segments']);validate_srt(output)
    assert '00:00:09,500 --> 00:00:13,000' in output
    assert '00:00:19,210 --> 00:02:52,870' not in output


def test_english_uses_source_word_alignment_and_preserves_pauses():
    model=Model([segment('Hello again',words=[word('Hello',.4,1.1),word(' again',2.4,3)])],language='en')
    result=recognize_utterances(model,range(20*SAMPLE_RATE),Settings(source_language='en'),'translate',lambda _:None,
        lambda *a,**k:[{'start':10*SAMPLE_RATE,'end':14*SAMPLE_RATE}])
    assert all(c['task']=='transcribe' and c['word_timestamps'] for c in model.calls)
    assert model.detections==0
    assert result['segments'][0]['start']==pytest.approx(10.4)
    assert result['segments'][0]['end']==pytest.approx(11.1)
    assert result['segments'][1]['start']==pytest.approx(12.4)


def test_language_detected_once_and_reused_for_all_speech():
    model=Model([segment('Translated')])
    recognize_utterances(model,range(20*SAMPLE_RATE),Settings(),'translate',lambda _:None,
        lambda *a,**k:[{'start':3*SAMPLE_RATE,'end':5*SAMPLE_RATE},{'start':10*SAMPLE_RATE,'end':12*SAMPLE_RATE}])
    assert model.detections==1
    assert [c['language'] for c in model.calls]==['ja','ja']


def test_long_text_wraps_without_inventing_temporal_slices():
    output=render_srt([{'start':3.31,'end':16.21,'text':'Some long translated words '*12}])
    validate_srt(output)
    assert output.count('-->')==1
    assert '00:00:03,310 --> 00:00:16,210' in output
    assert ' '.join(output.splitlines()[2:])==' '.join(('Some long translated words '*12).split())


def test_renderer_rejects_overlap_rather_than_moving_dialogue():
    with pytest.raises(ValueError,match='silently shift'):
        render_srt([{'start':1,'end':5,'text':'First'},{'start':4,'end':6,'text':'Second'}])


@pytest.mark.parametrize('words',[[word('Hello',0,1)], [word('Hello',1,2),word(' again',1.5,3)], [word('Hello',-1,1),word(' again',2,3)]])
def test_bad_word_alignment_preserves_complete_text_in_bounded_utterance(words):
    cues=speech_cues([segment('Hello again',words=words)],10,14,'transcribe')
    assert cues==[{'start':10,'end':14,'text':'Hello again'}]


def test_no_speech_does_not_generate_silence_cues():
    with pytest.raises(ValueError,match='No speech detected'):
        recognize_utterances(Model([]),range(20*SAMPLE_RATE),Settings(),'translate',lambda _:None,lambda *a,**k:[])


def test_unbounded_detector_interval_fails_closed():
    with pytest.raises(ValueError,match='unbounded'):
        recognize_utterances(Model([segment('Words')]),range(180*SAMPLE_RATE),Settings(source_language='ja'),'translate',lambda _:None,
            lambda *a,**k:[{'start':19*SAMPLE_RATE,'end':172*SAMPLE_RATE}])


from zilcho_subs.timing import readable_cues,is_hallucination


def test_short_lines_stay_up_long_enough_to_read_without_moving_starts():
    # Measured case: speech-bounded lines vanished as the speaker stopped.
    cues=readable_cues([{'start':120.5,'end':121.5,'text':"You look like you've had a rough day, Asalar."},
        {'start':124.3,'end':124.9,'text':'Shedar...'}])
    assert [c['start'] for c in cues]==[120.5,124.3]
    assert cues[0]['end']==pytest.approx(120.5+45/17)   # reading time for 45 characters
    assert cues[1]['end']==pytest.approx(125.3)          # short line: linger after speech
    validate_srt(render_srt(cues))


def test_reading_time_never_runs_into_the_next_line_or_shortens_a_line():
    cues=readable_cues([{'start':10,'end':10.4,'text':'A fairly long line that needs reading time'},
        {'start':11,'end':14,'text':'Next'}])
    assert cues[0]['end']==pytest.approx(11-0.084)
    assert cues[1]['end']==pytest.approx(14.4)           # already long: only the short linger
    long=readable_cues([{'start':0,'end':9,'text':'x'*300}])
    assert long[0]['end']==pytest.approx(9.4)            # never shortened, hold capped
    validate_srt(render_srt(cues))


def test_reading_time_stops_at_the_end_of_the_media():
    assert readable_cues([{'start':99,'end':99.5,'text':'Last words of the episode'}],duration=100)[0]['end']==100


@pytest.mark.parametrize('text',['Thank you for watching!','Thanks for watching.','Thank you for your viewing.',
    'Please subscribe to my channel and give it a high rating.','ご視聴ありがとうございました','Subtitles by the community'])
def test_stock_made_up_phrases_are_dropped(text):
    assert is_hallucination(text)
    kept=readable_cues([{'start':1,'end':2,'text':text},{'start':5,'end':6,'text':'Real line'}])
    assert [c['text'] for c in kept]==['Real line'] and kept[0]['end']==pytest.approx(6.4)


@pytest.mark.parametrize('text',['Thank you.','I was watching you.','Thank you for watching over my mother all these years.',
    'Subscribe? To what?'])
def test_ordinary_dialogue_is_kept(text):
    assert not is_hallucination(text)


def test_segments_the_model_marks_as_non_speech_are_skipped():
    noise=N(text='Thank goodness',start=0,end=1,words=None,no_speech_prob=.9,avg_logprob=-1.4,compression_ratio=1.1)
    loop=N(text='la la la la la la la la',start=1,end=2,words=None,no_speech_prob=.1,avg_logprob=-.3,compression_ratio=3.1)
    real=N(text='How are you?',start=2,end=3,words=None,no_speech_prob=.2,avg_logprob=-.4,compression_ratio=1.2)
    assert speech_cues([noise,loop,real],10,14,'translate')==[{'start':10,'end':14,'text':'How are you?'}]
    assert speech_cues([noise],10,14,'translate')==[]


from zilcho_subs.timing import split_sentences


def words_of(text,start=10.0,step=.3,pause_after=None):
    """Word times like faster-whisper's: one word every `step` seconds, optional pause."""
    out=[];t=start
    for i,w in enumerate(text.split(' ')):
        out.append([round(t,3),round(t+step-.05,3),(' ' if i else '')+w]);t+=step
        if pause_after and w.endswith(pause_after):t+=.5
    return out


def test_recognized_cues_keep_their_word_times():
    model=Model([segment('Hello again',words=[word('Hello',.4,1.1),word(' again',1.2,1.8)])],language='en')
    result=recognize_utterances(model,range(20*SAMPLE_RATE),Settings(source_language='en'),'transcribe',lambda _:None,
        lambda *a,**k:[{'start':10*SAMPLE_RATE,'end':14*SAMPLE_RATE}])
    assert result['segments'][0]['words']==[[10.4,11.1,'Hello'],[11.2,11.8,' again']]


def test_long_cue_splits_at_sentence_end_on_real_word_times():
    text="It's such a pleasure to see you again. It's been some time."
    words=words_of(text,pause_after='again.')
    [first,second]=split_sentences([{'start':words[0][0],'end':words[-1][1],'text':text,'words':words}])
    assert first['text']=="It's such a pleasure to see you again."
    assert second['text']=="It's been some time."
    assert first['start']==words[0][0] and first['end']==words[7][1]
    assert second['start']==words[8][0] and second['end']==words[-1][1]
    validate_srt(render_srt([first,second]))


def test_three_sentences_and_a_short_tail_stays_attached():
    text='Given how much you hate my presence, it must be truly important. Ocelar.'
    [cue]=split_sentences([{'start':0,'end':5,'text':text,'words':words_of(text,0)}])
    assert cue['text']==text   # "Ocelar." alone would flash by; it stays with its sentence
    text='And did you do it by chance? I did not, not yet. General Marmite got in my way.'
    pieces=split_sentences([{'start':0,'end':7,'text':text,'words':words_of(text,0)}])
    assert [p['text'] for p in pieces]==['And did you do it by chance?','I did not, not yet.','General Marmite got in my way.']
    assert all(a['end']<=b['start'] for a,b in zip(pieces,pieces[1:]))


@pytest.mark.parametrize('text',[
    'Why? Why so long?',                                      # fits on one line
    'Mr. Smith and Dr. Jones went to see the general together today',  # abbreviations, one sentence
    'Your mother owes everything to me, and she does not pretend otherwise.',  # one long sentence
])
def test_cues_that_should_stay_whole(text):
    cue={'start':0,'end':4,'text':text,'words':words_of(text,0)}
    assert split_sentences([cue])==[cue]


def test_cues_without_word_times_are_never_split_by_guessing():
    text="It's such a pleasure to see you again. It's been some time."
    translated={'start':1,'end':4,'text':text}
    mismatched={'start':1,'end':4,'text':text,'words':[[1,2,'Different'],[2,3,' words']]}
    assert split_sentences([translated,mismatched])==[translated,mismatched]


def test_japanese_splits_at_full_stop_and_counts_full_width():
    words=[[0,1,'お主がここを訪ねてくるのは'],[1,2,'久しぶりだな。'],[2.6,3.5,'正直に言うと'],[3.5,4.4,'あなたのことが怖くてね']]
    pieces=split_sentences([{'start':0,'end':4.4,'text':''.join(w[2] for w in words),'words':words}])
    assert [p['text'] for p in pieces]==['お主がここを訪ねてくるのは久しぶりだな。','正直に言うとあなたのことが怖くてね']
    assert (pieces[1]['start'],pieces[1]['end'])==(2.6,4.4)


def test_touching_words_at_real_offsets_keep_word_alignment():
    # Words that start exactly where the previous one ended, deep into an episode.
    words=[word(" It's",0,.52),word(' such',.52,.68),word(' a',.68,.88),word(' pleasure',.88,1.08),word(' to',1.08,1.38),
        word(' see',1.38,1.52),word(' you',1.52,1.76),word(' again.',1.76,2.04),word(" It's",2.32,2.56),word(' been',2.56,2.74),
        word(' some',2.74,3.12),word(' time.',3.12,3.46)]
    [cue]=speech_cues([segment("It's such a pleasure to see you again. It's been some time.",words=words)],125.706,129.398,'transcribe')
    assert cue['start']==pytest.approx(125.706) and cue['end']==pytest.approx(129.166)
    assert len(cue['words'])==12


def test_overlong_utterance_is_cut_after_a_comma_not_mid_phrase():
    text='He has the power to just pop deep into your mind and scatter a few truths, little truths, and among a thousand lies.'
    ws=[word((' ' if i else '')+w,i*.3,i*.3+.28) for i,w in enumerate(text.split(' '))]
    cues=speech_cues([segment(text,words=ws)],100,100+len(ws)*.3,'transcribe')
    assert [c['text'] for c in cues]==['He has the power to just pop deep into your mind and scatter a few truths,','little truths, and among a thousand lies.']
    assert cues[0]['end']<=cues[1]['start']


def test_overlong_utterance_without_commas_is_cut_before_a_joining_word():
    text='And then the next thing you know you end up all fired up as if endowed with some divine mission.'
    ws=[word((' ' if i else '')+w,i*.3,i*.3+.28) for i,w in enumerate(text.split(' '))]
    cues=speech_cues([segment(text,words=ws)],0,len(ws)*.3,'transcribe')
    assert [c['text'] for c in cues]==['And then the next thing you know you end up all fired up','as if endowed with some divine mission.']


def test_overlong_run_of_words_with_no_good_cut_still_respects_the_limits():
    ws=[word((' ' if i else '')+'blah',i*.3,i*.3+.28) for i in range(60)]
    cues=speech_cues([segment(' '.join(['blah']*60),words=ws)],0,18,'transcribe')
    assert len(cues)>=3 and all(c['end']-c['start']<=6 and len(c['text'])<=84 for c in cues)
    assert ' '.join(c['text'] for c in cues)==' '.join(['blah']*60)
