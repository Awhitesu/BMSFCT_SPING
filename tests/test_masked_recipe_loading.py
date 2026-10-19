import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

from test_test_page_result_retention import completed_page, app
from core.engine import ChannelWorker


def test_masked_rows_are_excluded_from_display_and_execution(completed_page):
    page, path, data = completed_page
    active = data['items'][0]
    active['sub_steps'][0]['name'] = 'read'
    active['sub_steps'].insert(0, dict(name='#disabled child', action='do not run'))
    masked = copy.deepcopy(active)
    masked['name'] = ' #disabled item'
    data['items'].insert(0, masked)
    path.write_text(json.dumps(data), encoding='utf-8')
    assert page.load_recipe_from_file(str(path))
    assert page.step_tree.topLevelItemCount() == 1
    row = page.step_tree.topLevelItem(0)
    assert row.text(0) == '1' and row.childCount() == 1
    assert row.child(0).text(0) == '1.1'
    snapshot = page.serialize_tree_to_dict()
    assert [s['name'] for s in snapshot['items']] == ['voltage']
    assert [s['name'] for s in snapshot['items'][0]['sub_steps']] == ['read']
    assert page.current_recipe_data == data
    # Unmasking in the editor/file restores the item on the next load.
    data['items'][0]['name'] = 'restored'
    path.write_text(json.dumps(data), encoding='utf-8')
    assert page.load_recipe_from_file(str(path))
    assert page.step_tree.topLevelItemCount() == 2


def test_worker_never_executes_masked_child():
    worker = SimpleNamespace(is_running=True, channel_id=1, current_step_index=0,
                             current_sub_step_index=2, sub_step_finished=Mock(),
                             on_sub_step_complete=Mock(), _execute_sub_step_logic=Mock())
    ChannelWorker.execute_sub_step(worker, SimpleNamespace(params={'name': '#disabled'}))
    worker._execute_sub_step_logic.assert_not_called()
    worker.on_sub_step_complete.assert_called_once()
    worker.sub_step_finished.emit.assert_called_once_with(1, 0, 2, '跳过', None)
