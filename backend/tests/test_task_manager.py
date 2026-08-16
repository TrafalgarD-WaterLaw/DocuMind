"""任务管理器测试——状态机、保留上限、按来源查询、JSONL 持久化（U6a）"""
import pytest

from documents.application.task_manager import TaskManager, TaskStatus


@pytest.fixture(autouse=True)
def _tmp_persist(tmp_path, monkeypatch):
    """持久化路径隔离——测试不写真实 data/tasks.jsonl"""
    monkeypatch.setattr(
        "documents.application.task_manager.DEFAULT_PERSIST", tmp_path / "tasks.jsonl"
    )
    yield


def test_create_and_update():
    tm = TaskManager()
    tid = tm.create_task(file_name="a.pdf")
    t = tm.get_task(tid)
    assert t.status == TaskStatus.QUEUED
    assert t.progress == 0
    assert t.file_name == "a.pdf"
    tm.update_task(tid, status=TaskStatus.PARSING, progress=20, stage_text="解析中")
    assert tm.get_task(tid).status == TaskStatus.PARSING
    assert tm.get_task(tid).progress == 20


def test_failed_holds_error():
    tm = TaskManager()
    tid = tm.create_task(file_name="b.pdf")
    tm.update_task(tid, status=TaskStatus.FAILED, error="boom")
    assert tm.get_task(tid).error == "boom"


def test_list_orders_newest_first():
    tm = TaskManager()
    ids = [tm.create_task(file_name=f"f{i}.pdf") for i in range(3)]
    listed = tm.list_tasks()
    assert [t.task_id for t in listed] == list(reversed(ids))


def test_cap_keeps_recent():
    tm = TaskManager(max_tasks=3)
    for i in range(5):
        tm.create_task(file_name=f"f{i}.pdf")
    assert len(tm.list_tasks()) == 3
    assert tm.list_tasks()[0].file_name == "f4.pdf"


def test_latest_and_remove_by_source():
    tm = TaskManager()
    tm.create_task(file_name="c.pdf", source="123_c.pdf")
    tm.update_task(tm.list_tasks()[0].task_id, status=TaskStatus.DONE, progress=100)
    latest = tm.latest_by_source("123_c.pdf")
    assert latest is not None and latest.status == TaskStatus.DONE
    tm.remove_by_source("123_c.pdf")
    assert tm.latest_by_source("123_c.pdf") is None


# ── U6a 任务持久化（JSONL 恢复）───────────────────────────

def test_persist_and_recover(tmp_path):
    """任务写入 JSONL 后，新实例可恢复（DONE 原样保留）"""
    from documents.application.task_manager import TaskManager, TaskStatus

    path = tmp_path / "tasks.jsonl"
    tm1 = TaskManager(persist_path=path)
    tid = tm1.create_task("a.pdf", source="s1")
    tm1.update_task(tid, status=TaskStatus.DONE, progress=100, chunks=5)

    tm2 = TaskManager(persist_path=path)  # 模拟重启
    t = tm2.get_task(tid)
    assert t is not None
    assert t.status == TaskStatus.DONE
    assert t.chunks == 5


def test_active_task_marked_failed_on_recover(tmp_path):
    """重启恢复：进行中任务标记「服务重启中断」"""
    from documents.application.task_manager import TaskManager, TaskStatus

    path = tmp_path / "tasks.jsonl"
    tm1 = TaskManager(persist_path=path)
    tid = tm1.create_task("a.pdf")
    tm1.update_task(tid, status=TaskStatus.PARSING, progress=10)

    tm2 = TaskManager(persist_path=path)
    t = tm2.get_task(tid)
    assert t.status == TaskStatus.FAILED
    assert "中断" in t.stage_text


def test_removed_task_not_recovered(tmp_path):
    from documents.application.task_manager import TaskManager

    path = tmp_path / "tasks.jsonl"
    tm1 = TaskManager(persist_path=path)
    tid = tm1.create_task("a.pdf", source="s1")
    tm1.remove_by_source("s1")

    tm2 = TaskManager(persist_path=path)
    assert tm2.get_task(tid) is None


def test_timings_field_persisted(tmp_path):
    from documents.application.task_manager import TaskManager, TaskStatus

    path = tmp_path / "tasks.jsonl"
    tm1 = TaskManager(persist_path=path)
    tid = tm1.create_task("a.pdf")
    tm1.update_task(tid, status=TaskStatus.DONE, timings={"parse_ms": 120, "index_ms": 30})

    tm2 = TaskManager(persist_path=path)
    assert tm2.get_task(tid).timings == {"parse_ms": 120, "index_ms": 30}
