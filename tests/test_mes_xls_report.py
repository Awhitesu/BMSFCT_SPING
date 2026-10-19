import json
import sqlite3
from pathlib import Path
import xlrd
import pytest
from core.excel_recorder import export_test_record
from core.run_context import build_run_context
from test_excel_recorder import _make_db

REFERENCE=Path(__file__).resolve().parents[1]/'SCUx03DP050201B12610174596_Pass(20261018_20-39-59).xls'


def test_independent_mes_delivery_is_flat_identical_and_frozen(tmp_path):
    db=tmp_path/'test.db';identity=_make_db(str(db))
    archive=tmp_path/'archive';mes=tmp_path/'mes'
    context=build_run_context({},dict(report_root_dir=str(archive),mes_report_dir=str(mes)))
    with sqlite3.connect(db) as conn:
        conn.execute('ALTER TABLE test_main ADD COLUMN context_json TEXT')
        conn.execute('UPDATE test_main SET context_json=?',(json.dumps(context),))
    path=Path(export_test_record(str(db),identity,settings=dict(report_root_dir=str(tmp_path/'wrong'),mes_report_dir=str(tmp_path/'wrong_mes'))))
    assert path.is_relative_to(archive)
    assert (mes/path.name).read_bytes()==path.read_bytes()
    assert not (tmp_path/'wrong_mes').exists()
    assert Path(export_test_record(str(db),identity))==path


def test_mes_failure_keeps_complete_archive_and_reports_error(tmp_path,monkeypatch):
    import core.excel_recorder as module
    db=tmp_path/'test.db';identity=_make_db(str(db))
    archive=tmp_path/'archive';mes=tmp_path/'mes'
    original=module._publish_report
    def publish(folder,*args,**kwargs):
        if folder==mes:raise OSError('MES share unavailable')
        return original(folder,*args,**kwargs)
    monkeypatch.setattr(module,'_publish_report',publish)
    with pytest.raises(RuntimeError,match='测试报告已保存.*MES文件输出失败'):
        export_test_record(str(db),identity,settings=dict(report_root_dir=str(archive),mes_report_dir=str(mes)))
    reports=list(archive.rglob('*.xls'));assert len(reports)==1
    assert xlrd.open_workbook(str(reports[0])).sheet_names()==['Sheet1']

def test_reference_headers_merges_fonts_palette_and_row_dimensions_match(tmp_path):
    db=tmp_path/'test.db';identity=_make_db(str(db))
    actual=xlrd.open_workbook(export_test_record(str(db),identity,str(tmp_path/'reports')),formatting_info=True)
    reference=xlrd.open_workbook(str(REFERENCE),formatting_info=True)
    layout=json.loads((REFERENCE.parent/'resources/test_report_layout.json').read_text(encoding='utf-8'))
    a=actual.sheet_by_index(0);r=reference.sheet_by_index(0)
    assert actual.sheet_names()==reference.sheet_names()==['Sheet1']
    assert actual.font_list[0].name==reference.font_list[0].name
    assert actual.font_list[0].height==reference.font_list[0].height
    assert a.ncols==r.ncols==6 and sorted(a.merged_cells)==sorted(r.merged_cells)
    for row in range(18):
        assert a.rowinfo_map[row].height==r.rowinfo_map[row].height
        assert a.rowinfo_map[row].height_mismatch==r.rowinfo_map[row].height_mismatch
        assert a.cell_value(row,0)==r.cell_value(row,0)
        for col in range(6):
            ax=actual.xf_list[a.cell_xf_index(row,col)];rx=reference.xf_list[r.cell_xf_index(row,col)]
            af=actual.font_list[ax.font_index];rf=reference.font_list[rx.font_index]
            assert (af.name,af.height,af.bold,af.colour_index)==(rf.name,rf.height,rf.bold,rf.colour_index)
            assert actual.colour_map[ax.background.pattern_colour_index]==tuple(layout['display_fills'][row][col])
    assert a.row_values(17)==r.row_values(17)
    assert all(a.colinfo_map[c].width==r.colinfo_map[c].width for c in range(6))

@pytest.mark.parametrize('verdict,folder,label',[('PASS','PASS','Pass'),('FAIL','FAIL','Fail'),('NG','FAIL','Fail')])
def test_uniform_root_filename_and_snapshot_metadata(verdict,folder,label,tmp_path):
    db=tmp_path/'test.db';identity=_make_db(str(db));root=tmp_path/'MES'
    settings=dict(report_root_dir=str(root),report_station_name='APP',report_equipment_no='GS-APP-08')
    recipe=dict(name='VF7L600_4D_APP',project_no='AP-DJ2403',model_name='VF7L600_4D(9850-11)_APP')
    context=build_run_context(recipe,settings)
    with sqlite3.connect(db) as conn:
        conn.execute('ALTER TABLE test_main ADD COLUMN context_json TEXT')
        conn.execute('ALTER TABLE test_main ADD COLUMN operator TEXT')
        conn.execute('UPDATE test_main SET master_code=?,start_time=?,end_time=?,result=?,operator=?,recipe_name=?,context_json=?',
                     ('SCUx03DP050201B12610174596','2026-10-18 20:39:59','2026-10-18 20:43:46',verdict,'P0026658',recipe['name'],json.dumps(context)))
    path=Path(export_test_record(str(db),identity,settings={'report_root_dir':str(tmp_path/'wrong')}))
    assert path==root/'AP-DJ2403'/'2026-10-18'/folder/f'SCUx03DP050201B12610174596_{label}(20261018_20-39-59).xls'
    assert path.read_bytes()[:8]==bytes.fromhex('D0 CF 11 E0 A1 B1 1A E1')
    sheet=xlrd.open_workbook(str(path)).sheet_by_index(0)
    for row,value in [(2,'SCUx03DP050201B12610174596'),(3,'2026/10/18'),(4,'20:39:59'),(5,'03:47'),
                      (6,label),(8,'AP-DJ2403'),(9,'P0026658'),(10,recipe['model_name']),(11,recipe['name']+'.json'),(14,'GS-APP-08'),(15,'APP')]:
        assert sheet.cell_value(row,1)==value
    assert Path(export_test_record(str(db),identity))==path
    with sqlite3.connect(db) as conn:conn.execute("UPDATE test_main SET recipe_name='OTHER_x',context_json=NULL")
    other=Path(export_test_record(str(db),identity,settings=settings))
    assert other.is_relative_to(root/'OTHER')

def test_no_half_xls_published_and_temp_removed_on_failure(tmp_path,monkeypatch):
    import core.xls_report as module
    db=tmp_path/'test.db';identity=_make_db(str(db));root=tmp_path/'reports'
    def failed(self,stream):
        stream.write(b'incomplete');raise OSError('disk full')
    monkeypatch.setattr(module.xlwt.Workbook,'save',failed)
    with pytest.raises(OSError):export_test_record(str(db),identity,str(root))
    assert not list(root.rglob('*.xls')) and not list(root.rglob('*.tmp'))

def test_duplicate_second_does_not_overwrite_either_record(tmp_path):
    db=tmp_path/'test.db';identity=_make_db(str(db));root=tmp_path/'reports'
    with sqlite3.connect(db) as conn:conn.execute('INSERT INTO test_main SELECT 2,channel_id,shelf_code,master_code,slave_codes,recipe_name,start_time,end_time,result FROM test_main')
    first=Path(export_test_record(str(db),identity,str(root)));content=first.read_bytes()
    second=Path(export_test_record(str(db),2,str(root)))
    assert first.name==second.name and first!=second and '重复记录' in second.parts
    assert first.read_bytes()==content and Path(export_test_record(str(db),2,str(root)))==second

def test_database_and_xls_preserve_version_and_single_hex_byte_leading_zeros(tmp_path):
    from data.db_manager import DBManager
    db=DBManager(str(tmp_path/'test.db'))
    try:
        identity=db.start_new_test(1,'CH1','SN','PROJECT_A')
        db.log_item_result(identity,'version','','0113','02','PASS')
        db.log_item_result(identity,'hex','','00','00','PASS')
        db.finish_test(identity,'PASS')
        path=export_test_record(db.db_path,identity,str(tmp_path/'reports'))
        sheet=xlrd.open_workbook(path).sheet_by_index(0)
        assert sheet.cell_value(18,1)=='02' and sheet.cell_value(18,3)=='0113'
        assert sheet.cell_value(19,1)=='00'
    finally:db.close()
