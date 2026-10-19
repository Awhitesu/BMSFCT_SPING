import threading
import time
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from ui.tabs.board_debug_tab import BoardDebugTab


app=QApplication.instance() or QApplication([])


def pump(predicate):
    end=time.monotonic()+4
    while not predicate() and time.monotonic()<end:
        app.processEvents()
        time.sleep(.002)
    assert predicate()


@pytest.fixture
def boards():
    db=Mock()
    db.load_sys_config.return_value={'rn_boards':{
        'hsd1':{'enabled':True,'ip':'192.0.2.1'},
        'hsd2':{'enabled':True,'ip':'192.0.2.2'}}}
    page=BoardDebugTab(Mock(db_manager=db),'hsd')
    first,second=page.boards['hsd1'],page.boards['hsd2']
    first.is_connected=second.is_connected=True
    entered,release=threading.Event(),threading.Event()
    def read(board):
        assert board is first
        entered.set()
        assert release.wait(4)
        return [False]*15
    try:
        page.run('poll',read,poll=True)
        assert entered.wait(2)
        yield page,first,second,release
    finally:
        release.set()
        page.shutdown()
        page.close()


@pytest.mark.parametrize('switch_back',[False,True])
def test_queued_output_never_moves_to_another_board(boards,switch_back):
    page,first,second,release=boards
    writes=[]
    page.run('relay on',lambda board:writes.append(board))
    assert page.pending_action is not None
    page.select.setCurrentIndex(page.select.findData('hsd2'))
    assert page.current() is second
    assert page.pending_action is None
    assert '已取消' in page.status.text()
    if switch_back:
        page.select.setCurrentIndex(page.select.findData('hsd1'))
        assert page.current() is first
    release.set()
    pump(lambda:not page.task.isRunning())
    assert writes==[]


def test_queued_output_runs_on_original_board_when_selection_unchanged(boards):
    page,first,_,release=boards
    writes=[]
    page.run('relay on',lambda board:writes.append(board))
    release.set()
    pump(lambda:bool(writes) and not page.task.isRunning())
    assert writes==[first]
    assert page.pending_action is None


def test_old_poll_result_does_not_change_new_boards_display(boards):
    page,_,second,release=boards
    page.select.setCurrentIndex(page.select.findData('hsd2'))
    page.show_relay_states([True]*15)
    release.set()
    pump(lambda:not page.task.isRunning())
    assert page.current() is second
    assert all(button.isChecked() for button in page.relay_buttons)


def test_shutdown_cancels_queued_output(boards):
    page,_,_,release=boards
    writes=[]
    page.run('relay on',lambda board:writes.append(board))
    release.set()
    page.shutdown()
    app.processEvents()
    assert writes==[]
    assert page.pending_action is None


def test_action_clicked_after_switch_targets_new_selection(boards):
    page,_,second,release=boards
    writes=[]
    page.select.setCurrentIndex(page.select.findData('hsd2'))
    page.run('relay on',lambda board:writes.append(board))
    release.set()
    pump(lambda:bool(writes) and not page.task.isRunning())
    assert writes==[second]


def test_reconfigured_shared_object_cancels_queued_command(boards):
    page,first,_,release=boards
    writes=[]
    page.run('relay on',lambda board:writes.append(board))
    first.setting=dict(first.setting,ip='192.0.2.99')
    first.ip='192.0.2.99'
    release.set()
    pump(lambda:not page.task.isRunning())
    assert writes==[]
    assert '已取消' in page.status.text()
