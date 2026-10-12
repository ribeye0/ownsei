import json
from collections import Counter

import pytest

from onsei.domain import Cue, Track
from onsei.export import export_track
from onsei.review import review
from onsei.semantic_contract import parse_review, semantic_grammar
from onsei.storage import Store
from onsei.translation_pipeline import process_translation
from onsei.translation_reference import build_reference
from onsei.webvtt import read_vtt


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path/'p', 'semantic')
    track = Track(id='t',name='test',source='/fake',sha256='h',duration_ms=60000,sample_rate=16000,channels=1)
    s.save_track(track)
    s.replace_cues(track,'v',[Cue(id=f'c{i}',track_id='t',version='v',ordinal=i,start_ms=i*1000,
                                  end_ms=i*1000+900,ja_raw=f'台詞{i}') for i in range(34)])
    return s


class Engine:
    loads = []
    def __init__(self, model, directory=None):
        self.model = model
        self.counts = Counter()
        self.calls = []
        self.summary = None
    def __enter__(self):
        self.loads.append(self.model)
        return self
    def __exit__(self,*_):
        pass
    def record(self,event,**kw):
        self.counts[event] += kw.get('lines',1)
    def execution_metadata(self):
        return {'test': True}
    def reject(self,error):
        return False
    def generate(self,data):
        self.calls.append(data)
        return json.dumps([{'id':r['id'],'text':'好的'} for r in data['lines']])
    def complete_json(self,prompt,grammar,*,stage,**kw):
        self.calls.append(stage)
        if stage == 'reference':
            return '{"facts":[]}'
        data = json.loads(prompt.rsplit('\n',1)[1])
        return json.dumps([{'id':row['id'],'source':row['ja'],'action':'keep'}
                           for row in data['dialogue'] if row['id'] in data['target_ids']])


def translated_parent(store):
    identity = store.prepare_translation('t','index-translate-9b')
    for c in store.cues('t'):
        c.zh_auto,c.translation_status = '初譯','translated'
        assert store.save_generated(c,c.revision)
    return identity


def test_pipeline_reuses_one_model_and_completed_resume_without_loading(store,monkeypatch):
    import onsei.translation_pipeline as pipeline
    Engine.loads = []
    monkeypatch.setattr(pipeline,'Llama',Engine)
    snap = store.input_snapshot('t')
    out = process_translation(store,'t','index-translate-9b',lambda _:None,snapshot=snap,
                              after_review=True,use_reference=True,review_model="index-translate-9b")
    assert Engine.loads == ['index-translate-9b']
    meta = store.run_metadata(out['review'])
    assert meta['status']=='completed' and len(meta['coverage'])==34
    assert meta['parent_result_id']==out['draft'] and not meta['patches']
    def fail(*a,**k):
        pytest.fail('Completed/cached pipeline must not load GPU')
    monkeypatch.setattr(pipeline,'Llama',fail)
    assert process_translation(store,'t','index-translate-9b',lambda _:None,snapshot=snap,
                               after_review=True,use_reference=True,review_model="index-translate-9b",outputs=out)==out
    other = process_translation(store,'t','index-translate-9b',lambda _:None,review_only=True,review_model='index-translate-9b')
    assert other['review'] != out['review']


def test_review_manual_lock_evidence_patches_and_explicit_export(store,monkeypatch):
    import onsei.review as module
    parent = translated_parent(store)
    store.edit('c0',{'zh_override':'人工稿'})
    snap = store.input_snapshot('t')
    child = store.new_translation_run(snap,'index-translate-9b',stage='review')
    with pytest.raises(ValueError,match='尚未完成'):
        export_track(store,'t',store.path/'exports',result_id=child)
    def response(engine,prompt,grammar,parser,*a,**k):
        data = json.loads(prompt.rsplit('\n',1)[1])
        patches = [{'id':i,'text':'修改稿。','kind':'meaning','reason':'對照原文','evidence_ids':[i]}
                   for i in data['target_ids'] if i in ('1','2')]
        rows = []
        for cue in data['dialogue']:
            if cue['id'] not in data['target_ids']:
                continue
            patch = next((p for p in patches if p['id'] == cue['id']), None)
            rows.append({'source':cue['ja'], 'action':'change', **patch} if patch else
                        {'id':cue['id'], 'source':cue['ja'], 'action':'keep'})
        return parser(json.dumps(rows))
    monkeypatch.setattr(module,'structured_request',response)
    review(store,snap,child,Engine('index-translate-9b'),lambda _:None)
    assert store.cue('c0').zh=='人工稿' and store.cue('c1').zh=='修改稿'
    meta = store.run_metadata(child)
    assert len(meta['coverage'])==34 and meta['status']=='completed'
    assert [p['applied'] for p in meta['patches']]==[False,True]
    assert meta['issues'][0]['id']=='c0'
    store.activate_translation('t',parent)
    paths = export_track(store,'t',store.path/'exports',version='v',result_id=child)
    assert read_vtt(next(p for p in paths if p.name.endswith('.zh-TW.vtt'))).cues[1].text=='修改稿'
    assert store.cue('c1').zh=='初譯'


def test_cancelled_review_resumes_covered_windows(store,monkeypatch):
    import onsei.review as module
    translated_parent(store)
    snap = store.input_snapshot('t')
    child = store.new_translation_run(snap,'index-translate-9b',stage='review')
    calls = []
    def request(engine,prompt,*a,**kw):
        ids = json.loads(prompt.rsplit('\n',1)[1])['target_ids']
        calls.append(ids)
        if len(calls)==2:
            raise KeyboardInterrupt
        return {'patches':[],'issues':[]}
    monkeypatch.setattr(module,'structured_request',request)
    with pytest.raises(KeyboardInterrupt):
        review(store,snap,child,Engine('index-translate-9b'),lambda _:None)
    assert len(store.run_metadata(child)['coverage'])==16
    assert store.run_metadata(child)['status']=='partial'
    review(store,snap,child,Engine('index-translate-9b'),lambda _:None)
    assert len(store.run_metadata(child)['coverage'])==34
    assert calls[0]!=calls[2]


def test_parent_modified_during_review_rejects_before_writing(store,monkeypatch):
    import onsei.review as module
    parent = translated_parent(store)
    snap = store.input_snapshot('t')
    child = store.new_translation_run(snap,'index-translate-9b',stage='review')
    def changed(*a,**k):
        store.activate_translation('t',parent)
        store.edit('c0',{'zh_override':'新版原稿'})
        return {'patches':[{'id':'1','text':'錯誤覆寫','kind':'meaning','reason':'test','evidence_ids':['1']}],'issues':[]}
    monkeypatch.setattr(module,'structured_request',changed)
    with pytest.raises(ValueError,match='來源已更新'):
        review(store,snap,child,Engine('index-translate-9b'),lambda _:None)
    assert store.input_snapshot('t','v',child)['cues'][0]['zh_auto']=='初譯'
    assert store.run_metadata(child)['status'] in ('partial','stale')


def test_reference_checkpoint_resumes_partial_and_contains_no_chinese_answers(store):
    snapshot = store.input_snapshot('t')
    engine = Engine('index-translate-9b')
    def interrupt(event):
        if event.get('fraction',0)>0:
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        build_reference(store,snapshot,engine,interrupt)
    assert engine.calls==['reference']
    build_reference(store,snapshot,engine,lambda _:None)
    assert engine.calls==['reference','reference']
    build_reference(store,snapshot,engine,lambda _:None)
    assert len(engine.calls)==2


@pytest.mark.parametrize('bad',[
    {'id':'99','text':'好的','kind':'meaning','reason':'test','evidence_ids':['1']},
    {'id':'1','text':'好的','kind':'meaning','reason':'test','evidence_ids':['99']},
    {'id':'1','text':'。','kind':'meaning','reason':'test','evidence_ids':['1']},
    {'id':'1','text':'[{}]','kind':'meaning','reason':'test','evidence_ids':['1']},
])
def test_review_structural_contract_blocks_bad_patches(bad):
    with pytest.raises(ValueError):
        parse_review(json.dumps({'patches':[bad],'issues':[]}),['1'],{'1'})
    grammar = semantic_grammar(['1'],['1','2'])
    assert 'evidence-id' in grammar and '\\"patches\\"' in grammar


def test_distant_mentions_are_retrieved_without_forcing_same_honorific():
    from onsei.consistency import mention_groups,cross_context
    def c(i,ja,zh):
        return Cue(id=str(i),track_id='t',version='v',ordinal=i,start_ms=i*1000,end_ms=i*1000+900,
                   ja_raw=ja,zh_auto=zh)
    cues = [c(0,'ホームズくん','霍爾姆斯君'),c(50,'ホームズ先輩','霍爾姆斯學長')]
    groups = mention_groups(cues)
    assert groups == {'ホームズ':['0','50']}
    assert cross_context(cues,[cues[0]],[cues[0]],groups)==[cues[1]]


def test_ordered_review_decisions_require_exact_source_and_no_rewrite_for_keep():
    from onsei.semantic_contract import normalize_review
    lines = [{'id':'1','ja':'はい'},{'id':'2','ja':'おはよう'}]
    rows = [{'id':'1','source':'はい','action':'keep'},
            {'id':'2','source':'おはよう','action':'change','text':'早安','kind':'meaning',
             'reason':'原文是早安','evidence_ids':['2']}]
    assert normalize_review(json.dumps(rows),lines,['1','2'])['patches'][0]['id']=='2'
    for malformed in [rows[::-1], [rows[0]], [{**rows[0],'text':'好'},rows[1]],
                      [rows[0],{**rows[1],'source':'別的原文'}]]:
        with pytest.raises(ValueError):
            normalize_review(json.dumps(malformed),lines,['1','2'])


def test_parent_edit_marks_all_descendant_reviews_stale(store,monkeypatch):
    import onsei.translation_pipeline as pipeline
    monkeypatch.setattr(pipeline,'Llama',Engine)
    parent = translated_parent(store)
    first = process_translation(store,'t','index-translate-9b',lambda _:None,review_only=True)['review']
    second = process_translation(store,'t','index-translate-9b',lambda _:None,review_only=True)['review']
    store.activate_translation('t',parent)
    store.edit('c0',{'zh_override':'更新的人工稿'})
    assert store.run_metadata(first)['status']=='stale'
    assert store.run_metadata(second)['status']=='stale'
    with pytest.raises(ValueError,match='來源待更新'):
        export_track(store,'t',store.path/'exports',result_id=second)


def test_review_does_not_apply_new_language_residue_or_unresolved_changes(store,monkeypatch):
    import onsei.review as module
    translated_parent(store)
    snapshot = store.input_snapshot('t')
    child = store.new_translation_run(snapshot,'index-translate-9b',stage='review')
    def suggestions(*a,**kw):
        return {'patches':[
            {'id':'1','text':'初譯さん','kind':'entity','reason':'修改姓名','evidence_ids':['1']},
            {'id':'2','text':'不應覆寫','kind':'meaning','reason':'指代需確認','evidence_ids':['2']},
            {'id':'3','text':'初，譯','kind':'style','reason':'調整逗號','evidence_ids':['3']}], 'issues':[]}
    def first_window(*a,**kw):
        data=json.loads(a[1].rsplit('\n',1)[1])
        return suggestions() if '1' in data['target_ids'] else {'patches':[],'issues':[]}
    monkeypatch.setattr(module,'structured_request',first_window)
    review(store,snapshot,child,Engine('index-translate-9b'),lambda _:None)
    assert all(c['zh_auto']=='初譯' for c in store.input_snapshot('t','v',child)['cues'])
    assert len(store.run_metadata(child)['issues'])==2
    assert not any(p['applied'] for p in store.run_metadata(child)['patches'])


def test_local_retranslation_forks_source_and_keeps_unaffected_results(store,monkeypatch):
    import onsei.translation_pipeline as pipeline
    monkeypatch.setattr(pipeline,'Llama',Engine)
    parent=translated_parent(store)
    out=process_translation(store,'t','index-translate-9b',lambda _:None,selected=['c0'])
    rows=store.input_snapshot('t',result_id=out['draft'])['cues']
    assert all(c['zh_auto']=='好的' for c in rows[:24])
    assert all(c['zh_auto']=='初譯' for c in rows[24:])
    assert all(c['zh_auto']=='初譯' for c in store.input_snapshot('t',result_id=parent)['cues'])
    assert store.run_metadata(out['draft'])['scope']=='selected'
    assert len(export_track(store,'t',store.path/'exports',result_id=out['draft']))==13


def test_runner_pins_queued_version_without_stealing_new_view(store,monkeypatch,tmp_path):
    import onsei.runner as runner
    import onsei.translation_pipeline as pipeline
    monkeypatch.setattr(pipeline,'Llama',Engine)
    monkeypatch.setattr(runner,'STATE',tmp_path/'locks')
    job = store.new_job('translate',{'track_ids':['t'],'translation':'index-translate-9b','after_review':True})
    track = store.track('t')
    store.replace_cues(track,'new',[Cue(id='new-cue',track_id='t',version='new',ordinal=0,
                                      start_ms=0,end_ms=900,ja_raw='別の原文')])
    runner.run_job(store,job['id'])
    result = store.job(job['id'])
    assert result['status']=='completed'
    assert store.track('t').active_version=='new' and store.cue('new-cue').zh is None
    out = result['options']['processing_outputs']['t']
    assert len(store.input_snapshot('t','v',out['review'])['cues'])==34
    assert all(c['zh_auto']=='好的' for c in store.input_snapshot('t','v',out['draft'])['cues'])


def test_polling_excludes_large_input_snapshots_and_review_endpoint_is_bounded(store,monkeypatch,tmp_path):
    import onsei.api as service
    from fastapi.testclient import TestClient
    monkeypatch.setattr(service,'REGISTRY',tmp_path/'registry.db')
    with service.registry() as db:
        db.execute('INSERT INTO projects VALUES (?,?,NULL)',('p',str(store.path)))
    job=store.new_job('translate',{'track_ids':['t']})
    # No actual worker: expose a settled fixture instead of triggering recovery.
    job['status']='cancelled'
    store.save_job(job)
    parent=translated_parent(store)
    client=TestClient(service.app, base_url="http://127.0.0.1")
    data=client.get('/api/projects/p/jobs').json()
    assert 'input_snapshots' not in data[0]['options']
    assert data[0]['options']['input_fingerprints']['t']['version']=='v'
    assert client.get(f'/api/projects/p/tracks/t/translations/{parent}/review').status_code==200
    assert client.get(f'/api/projects/p/tracks/unknown/translations/{parent}/review').status_code==404
    assert client.post('/api/projects/p/jobs',json={'action':'review','cue_ids':['c0']}).status_code==422
    snapshot=store.input_snapshot('t',result_id=parent)
    child=store.new_translation_run(snapshot,'index-translate-9b',stage='review')
    store.update_run(child,patches=[{'id':'c0','evidence_ids':['c0'],'text':'建議'}])
    store.activate_translation('t',parent)
    store.edit('c0',{'ja_override':'後來改動的原文','zh_override':'後來改動的中文'})
    store.delete_translation('t',parent)
    details=client.get(f'/api/projects/p/tracks/t/translations/{child}/review').json()
    assert 'source_snapshot' not in details
    assert details['evidence']=={'c0':{'ordinal':0,'ja':'台詞0','zh':'初譯'}}
