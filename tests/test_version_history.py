import json
from tools import version_history as history


def metadata(version='2026.10.21.1'):
    return dict(app_version=version,built_at='2026-10-21T12:00:00+08:00',sha256='a'*64,
                source_commit='b'*40,source_dirty=False,source_subject='修改继电器',
                source_changes=['修复界面','增加100点回归'])


def test_build_records_notes_source_hash_and_updates_one_page(tmp_path):
    history.record_build(tmp_path,metadata(),['批量断开加速','按Excel点位控制'])
    rows=history.load(tmp_path)
    assert rows[0]['notes']==['批量断开加速','按Excel点位控制']
    assert rows[0]['sha256']=='a'*64 and rows[0]['source_commit']=='b'*40
    text=(tmp_path/'VERSION_HISTORY.md').read_text(encoding='utf-8')
    assert '批量断开加速' in text and '按Excel点位控制' in text and 'b'*40 in text
    history.record_build(tmp_path,metadata(),['补充说明'])
    assert len(history.load(tmp_path))==1
    assert '补充说明' in (tmp_path/'VERSION_HISTORY.md').read_text(encoding='utf-8')


def test_dirty_build_does_not_claim_exact_commit_rollback(tmp_path):
    item=metadata();item['source_dirty']=True
    history.record_build(tmp_path,item)
    text=(tmp_path/'VERSION_HISTORY.md').read_text(encoding='utf-8')
    assert '不能仅按此提交精确还原EXE' in text
    assert history.load(tmp_path)[0]['notes'][:2]==['修复界面','增加100点回归']


def test_source_page_is_newest_first_with_immutable_commit_links(tmp_path,monkeypatch):
    def git(root,*args):
        return 'a'*40+'\t2026-10-19\t首次版本\n'+'b'*40+'\t2026-10-21\t继电器|电阻卡'
    monkeypatch.setattr(history,'git',git)
    history.render(tmp_path)
    text=(tmp_path/'VERSION_HISTORY.md').read_text(encoding='utf-8')
    assert text.index('S2026.10.21.1')<text.index('S2026.10.19.1')
    assert '继电器／电阻卡' in text and f'/archive/{"b"*40}.zip' in text
