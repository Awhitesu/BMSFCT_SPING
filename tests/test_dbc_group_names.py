"""Automatic parent prefixes, incomplete editor drafts, and strict recipe saves."""
from copy import deepcopy
from unittest.mock import Mock, patch
import time

import pytest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from core.dbc_groups import (DBCGroupError, plan_groups, sync_group_name,
                             test_item_prefix as item_prefix, validate_group_names)
from core.dbc_signals import decode_step, encode_step
from ui.tabs.config_tab import ConfigTab
from ui.widgets.dbc_forms import DBCStepForm
from test_dbc_signals import asset, config

app = QApplication.instance() or QApplication([])


def recipe(asset, names, roles, group='old name'):
    items = []
    for name, role in zip(names, roles):
        c = dict(config(asset), new_frame=False)
        if role:c.update(group=group, group_role=role)
        items.append(dict(name=name, min='0', max='100', sub_steps=[dict(
            name='read', action='读取DBC信号', device='CANDBC 交互', type='CANDBC交互',
            params=encode_step(c)+' / PCH:1', is_judgment=True)]))
    return dict(name='test', dbc=asset, can_config=dict(channel='PCAN_USBBUS1'), items=items)


def page_for(data):
    db = Mock()
    db.load_sys_config.return_value = {}
    db.list_recipes.return_value = []
    page = ConfigTab(db)
    page.load_recipe_to_tree(data)
    return page, db


@pytest.mark.parametrize('name,expected', [
    ('[2.5V]_单体电压 58', '[2.5V]'),
    ('#[3.65V]_单体电压 1', '[3.65V]'),
    ('条件A_单体电压_01', '条件A'),
    ('板载温感测试 1', ''),
    ('[2.5V]单体电压 58', ''),
    ('_单体电压', ''),
])
def test_prefix_excludes_separator_and_skip_marker(name, expected):
    assert item_prefix(name) == expected


def test_sync_preserves_all_other_parameters_and_middle_reads(asset):
    c = dict(config(asset), group='old', group_role='start', new_frame=False, variable='RESULT')
    text = encode_step(c)+' / PCH:3'
    synced = sync_group_name(text, '#[2.5V]_单体电压 58')
    assert decode_step(synced) == dict(decode_step(text), group='[2.5V]')
    assert synced.endswith(' / PCH:3')
    assert sync_group_name(synced, '#[2.5V]_单体电压 58') == synced
    middle = encode_step(dict(config(asset), new_frame=False))+' / PCH:3'
    assert sync_group_name(middle, '[2.5V]_单体电压 2') == middle


def test_unprefixed_item_keeps_its_manual_group_and_exact_parameter_text(asset):
    c = dict(config(asset), group='板载温感', group_role='single', new_frame=False)
    text = encode_step(c)+' / PCH:3'
    assert sync_group_name(text, '板载温感测试 1') == text
    assert sync_group_name(text, '#板载温感测试 1') == text


@pytest.mark.parametrize('role', ['start', 'end', 'single'])
def test_auto_form_loads_a_blank_draft_but_default_decoder_stays_strict(asset, role):
    c = dict(config(asset), group='', group_role=role, new_frame=False)
    text = encode_step(c)+' / PCH:1'
    with pytest.raises(ValueError, match='必须填写组名'):decode_step(text)
    form = DBCStepForm(asset, test_item_name='板载温感测试 1')
    try:
        form.load(text)
        deadline = time.monotonic()+3
        while not form._loaded:
            assert time.monotonic() < deadline
            app.processEvents()
            time.sleep(.001)
        assert not form.group.isReadOnly() and form.group.isEnabled() and not form.group.text()
        assert form.config()['group_role'] == role and form.config()['group'] == ''
        assert not form.new_frame.isEnabled() and not form.new_frame.isChecked()
        form.group.setText('板载温感')
        assert form.config()['group'] == '板载温感'
    finally:form.close()


def test_auto_form_uses_parent_prefix_instead_of_old_group(asset):
    form = DBCStepForm(asset, test_item_name='#[2.5V]_单体电压 1')
    try:
        form.load(encode_step(dict(config(asset), group='old', group_role='single', new_frame=False)))
        deadline = time.monotonic()+3
        while not form._loaded:
            assert time.monotonic() < deadline
            app.processEvents()
            time.sleep(.001)
        assert form.group.isReadOnly() and form.config()['group'] == '[2.5V]'
        form.group_role.setCurrentIndex(0)
        assert not form.group.text() and 'group' not in form.config()
    finally:form.close()


def test_manual_form_reloads_and_retains_the_name_when_marker_role_changes(asset):
    c = dict(config(asset), group='板载温感', group_role='start', new_frame=False)
    form = DBCStepForm(asset, test_item_name='板载温感测试 1')
    try:
        form.load(encode_step(c))
        deadline = time.monotonic()+3
        while not form._loaded:
            assert time.monotonic() < deadline
            app.processEvents()
            time.sleep(.001)
        assert not form.group.isReadOnly() and form.group.isEnabled()
        assert form.config()['group'] == '板载温感'
        form.group.setText('温感采样')
        form.group_role.setCurrentIndex(form.group_role.findData('end'))
        assert form.config()['group'] == '温感采样'
    finally:form.close()


@pytest.mark.parametrize('save_method', ['save_recipe', 'save_recipe_as'])
def test_manual_group_saves_and_reloads_without_requiring_a_test_item_prefix(asset, save_method):
    data = recipe(asset, ['板载温感测试 1', '板载温感测试 3'], ['start', 'end'], group='板载温感')
    page, db = page_for(data)
    try:
        from PySide6.QtWidgets import QTreeWidgetItem
        selected = QTreeWidgetItem(['test'])
        page.recipe_tree.addTopLevelItem(selected)
        page.recipe_tree.setCurrentItem(selected)
        page.current_recipe_name = 'test'
        # Editing params fires tree synchronization, which must preserve the manual name.
        for i in range(2):
            parent = page.step_tree.topLevelItem(i)
            parent.setText(0, parent.text(0)+' 修改')
            node = parent.child(0)
            c = decode_step(node.text(2))
            node.setText(2, encode_step(dict(c, group='温感采样'))+' / PCH:1')
        with patch('PySide6.QtWidgets.QMessageBox.warning') as warning, \
             patch('PySide6.QtWidgets.QMessageBox.information'), \
             patch('PySide6.QtWidgets.QInputDialog.getText', return_value=('copy', True)):
            getattr(page, save_method)()
        warning.assert_not_called()
        db.save_recipe_json.assert_called_once()
        saved = db.save_recipe_json.call_args.args[1]
        assert all(decode_step(i['sub_steps'][0]['params'])['group'] == '温感采样' for i in saved['items'])
        page.load_recipe_to_tree(saved)
        result = page._collect_recipe_data('test')
        assert all(g['name'] == '温感采样' for g in plan_groups(result['items']).values())
        assert [i['name'] for i in result['items']] == ['板载温感测试 1 修改', '板载温感测试 3 修改']
    finally:page.close()


def test_removing_the_prefix_preserves_group_name_for_manual_editing(asset):
    page, _ = page_for(recipe(asset, ['[2.5V]_单体电压 1'], ['single']))
    try:
        parent = page.step_tree.topLevelItem(0)
        parent.setText(0, '单体电压 1')
        result = page._collect_recipe_data('test')
        assert decode_step(result['items'][0]['sub_steps'][0]['params'])['group'] == '[2.5V]'
    finally:page.close()


def test_editor_renames_boundaries_and_keeps_separate_ranges_with_the_same_prefix(asset):
    data = recipe(asset, ['[2.5V]_1', '中间读取', '[2.5V]_3', '[2.5V]_4'],
                  ['start', '', 'end', 'single'])
    page, _ = page_for(data)
    try:
        result = page._collect_recipe_data('test')
        plan = plan_groups(result['items'])
        assert len({g['id'] for g in plan.values()}) == 2
        assert all(g['name'] == '[2.5V]' for g in plan.values())
        assert 'group' not in decode_step(result['items'][1]['sub_steps'][0]['params'])
        for i in (0, 2):
            parent = page.step_tree.topLevelItem(i)
            parent.setText(0, f'#[3.0V]_单体电压 {i+1}')
            assert decode_step(parent.child(0).text(2))['group'] == '[3.0V]'
        result = page._collect_recipe_data('test')
        assert plan_groups(result['items'])[(1, 0)]['name'] == '[3.0V]'
    finally:page.close()


@pytest.mark.parametrize('save_method', ['save_recipe', 'save_recipe_as'])
@pytest.mark.parametrize('name', ['板载温感测试 1', '#板载温感测试 1'])
def test_both_save_actions_block_blank_groups_and_select_the_marker(asset, save_method, name):
    page, db = page_for(recipe(asset, [name], ['single'], group=''))
    try:
        # These methods require the loaded recipe to be selected before collection.
        from PySide6.QtWidgets import QTreeWidgetItem
        selected = QTreeWidgetItem(['test'])
        page.recipe_tree.addTopLevelItem(selected)
        page.recipe_tree.setCurrentItem(selected)
        page.current_recipe_name = 'test'
        node = page.step_tree.topLevelItem(0).child(0)
        saved_params = node.text(2)
        option = QStyleOptionViewItem()
        page.step_tree.itemDelegateForColumn(2).initStyleOption(option, page.step_tree.indexFromItem(node, 2))
        assert '名称为空' in option.text
        with patch('PySide6.QtWidgets.QMessageBox.warning') as warning, \
             patch('PySide6.QtWidgets.QInputDialog.getText', return_value=('copy', True)):
            getattr(page, save_method)()
        assert '采样组名称为空' in warning.call_args.args[2]
        assert page.step_tree.currentItem() is node
        assert node.text(2) == saved_params
        db.save_recipe_json.assert_not_called()
        page.step_tree.topLevelItem(0).setText(0, '[板载温感]_板载温感测试 1')
        result = page._collect_recipe_data('test')
        assert decode_step(result['items'][0]['sub_steps'][0]['params'])['group'] == '[板载温感]'
    finally:page.close()


def test_mismatched_prefixes_still_fail_and_ordinary_reads_need_no_prefix(asset):
    page, _ = page_for(recipe(asset, ['[2.5V]_1', '[3.0V]_2'], ['start', 'end']))
    try:
        with pytest.raises(DBCGroupError, match='没有匹配'):page._collect_recipe_data('test')
        assert page.step_tree.currentItem() is page.step_tree.topLevelItem(1).child(0)
    finally:page.close()
    ordinary = recipe(asset, ['板载温感测试 1'], [''])
    page, _ = page_for(ordinary)
    try:
        result = page._collect_recipe_data('test')
        assert 'group' not in decode_step(result['items'][0]['sub_steps'][0]['params'])
    finally:page.close()


def test_draft_load_retains_boundaries_until_the_manual_group_is_filled(asset):
    data = recipe(asset, ['板载温感测试 1', '板载温感测试 3'], ['start', 'end'], group='')
    for item in data['items']:
        step = item['sub_steps'][0]
        step['params'] = sync_group_name(step['params'], item['name'])
    original = deepcopy(data)
    with pytest.raises(ValueError, match='必须填写组名'):plan_groups(data['items'])
    page, _ = page_for(data)
    try:
        assert data == original
        with pytest.raises(DBCGroupError, match='采样组名称为空'):page._collect_recipe_data('test')
        for i in range(2):
            node = page.step_tree.topLevelItem(i).child(0)
            c = decode_step(node.text(2), allow_empty_group=True)
            node.setText(2, encode_step(dict(c, group='板载温感'))+' / PCH:1')
        result = page._collect_recipe_data('test')
        assert len(plan_groups(result['items'])) == 2
        validate_group_names(result['items'])
        assert [i['name'] for i in result['items']] == ['板载温感测试 1', '板载温感测试 3']
    finally:page.close()


def test_moving_a_marker_to_another_parent_updates_its_group(asset):
    page, _ = page_for(recipe(asset, ['[2.5V]_1', '[3.0V]_2'], ['single', '']))
    try:
        source = page.step_tree.topLevelItem(0)
        target = page.step_tree.topLevelItem(1)
        marker = source.takeChild(0)
        target.addChild(marker)
        assert decode_step(marker.text(2))['group'] == '[3.0V]'
        assert decode_step(marker.text(2))['group_role'] == 'single'
    finally:page.close()
