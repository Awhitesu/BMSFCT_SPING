from types import SimpleNamespace
from core.engine import ChannelWorker, TestStep as Step, StepType
from test_test_page_result_retention import completed_page, app


def test_variable_judgment_uses_value_and_displays_same_target(completed_page):
    page, _, _ = completed_page
    step = Step('read date', StepType.CUSTOM)
    step.standard_type = '\u53d8\u91cf'
    step.min_limit = '\u4e3b\u677f\u5e8f\u5217\u53f7'
    worker = ChannelWorker(page.channel_id, [step])
    worker.variables[step.min_limit] = 'LIMOCN2610194613'
    worker.current_step_results = [('LIMOCN2610194613', True)]
    worker.current_step_index = 0
    worker.is_running = True
    worker.variable_target_resolved.connect(page.on_variable_target_resolved)
    results=[]
    worker.step_finished.connect(lambda *args:results.append(args))
    worker.on_step_complete()
    assert results[-1][3] is True
    assert page.step_tree.topLevelItem(0).text(2) == 'LIMOCN2610194613'
    assert page.serialize_tree_to_dict()['items'][0]['name'] == 'voltage'


def test_missing_variable_is_explicit_in_display(completed_page):
    page, _, _ = completed_page
    page.on_variable_target_resolved(page.channel_id,0,'key',None)
    assert page.step_tree.topLevelItem(0).text(2) == '\u53d8\u91cf\u672a\u8d4b\u503c'
