"""服务层地基单元测试：异常体系 / Service 基类 / AppContext / 事件总线 / UI 端口。

这些测试不依赖 tkinter、不依赖 PySide6、不读磁盘、不联网，
是阶段 1 全部服务抽取的公共地基。
"""

import logging

import pytest

from app.context import AppContext
from app.events import EventBus
from app.ports import Choice, NullUIPort, ProgressReport, RecordingUIPort, UIPort
from services.base import Service
from services.errors import (
    AlreadyExists,
    NetworkError,
    OperationCancelled,
    PermissionDenied,
    ServiceError,
    ServiceNotAvailable,
    wrap,
)


# ─── 异常体系 ───────────────────────────────────────────────


class TestServiceError:
    def test_default_message_and_code(self):
        err = ServiceError()
        assert err.code == "service_error"
        assert err.message == "操作失败"
        assert str(err) == "操作失败"

    def test_detail_included_in_str_and_to_dict(self):
        err = PermissionDenied("没有权限", detail="token 过期")
        assert "token 过期" in str(err)
        assert err.to_dict() == {
            "code": "permission_denied",
            "message": "没有权限",
            "detail": "token 过期",
        }

    def test_extra_fields_merge_into_dict(self):
        err = NetworkError("超时", url="https://example.invalid")
        assert err.to_dict()["url"] == "https://example.invalid"

    def test_subclass_codes_are_distinct(self):
        codes = {
            ServiceError.code,
            ServiceNotAvailable.code,
            AlreadyExists.code,
            PermissionDenied.code,
            OperationCancelled.code,
            NetworkError.code,
        }
        assert len(codes) == 6, "各异常子类必须有不同的 code，便于测试断言"

    def test_log_returns_self_for_one_line_raise(self, caplog):
        with caplog.at_level(logging.ERROR):
            err = NetworkError("连不上", detail="dns").log()
        assert isinstance(err, NetworkError)
        assert "连不上" in caplog.text
        assert "dns" in caplog.text

    def test_cause_is_preserved(self):
        original = ValueError("底层炸了")
        err = ServiceError("包装后", cause=original)
        assert err.cause is original


class TestWrap:
    def test_passes_through_control_flow_exceptions(self):
        with pytest.raises(OperationCancelled):
            with wrap("不该包装"):
                raise OperationCancelled("用户取消了")

    def test_converts_unexpected_exception_and_chains_cause(self):
        with pytest.raises(NetworkError) as info:
            with wrap("请求失败", exc=NetworkError):
                raise ValueError("连接重置")
        err = info.value
        assert isinstance(err.cause, ValueError)
        assert "连接重置" in (err.detail or "")
        assert isinstance(err.__cause__, ValueError)

    def test_no_exception_is_a_noop(self):
        with wrap("不会触发"):
            pass


# ─── Service 基类 ───────────────────────────────────────────


class DummyService(Service):
    name = "dummy"
    label = "占位服务"


class TestServiceBase:
    def test_unattached_context_raises(self):
        svc = DummyService()
        assert svc.attached is False
        with pytest.raises(ServiceNotAvailable):
            _ = svc.context

    def test_attach_sets_context(self):
        ctx = AppContext()
        svc = DummyService()
        svc.attach(ctx)
        assert svc.attached is True
        assert svc.context is ctx

    def test_dependency_lookup_delegates_to_context(self):
        ctx = AppContext()
        ctx.register_instance("config_like", "配置对象")
        svc = ctx.register(DummyService())
        assert svc.require("config_like") == "配置对象"
        assert svc.try_get("missing") is None
        assert svc.has("config_like") is True
        with pytest.raises(ServiceNotAvailable):
            svc.require("missing")

    def test_start_stop_flag_and_default_noop(self):
        svc = DummyService()
        assert svc.started is False
        svc.start()
        assert svc.started is True
        svc.stop()
        assert svc.started is False

    def test_ui_property_is_null_port_by_default(self):
        svc = AppContext().register(DummyService())
        assert isinstance(svc.ui, NullUIPort)

    def test_publish_and_subscribe_route_through_event_bus(self):
        ctx = AppContext()
        svc = ctx.register(DummyService())
        seen = []
        svc.subscribe("thing.done", lambda source, value: seen.append((source, value)))
        svc.publish("thing.done", value=7)
        assert seen == [("dummy", 7)]
        svc.unsubscribe("thing.done", seen.append)  # 不存在的 handler 静默忽略

    def test_describe_shape(self):
        ctx = AppContext()
        svc = ctx.register(DummyService())
        info = svc.describe()
        assert info["name"] == "dummy"
        assert info["class"] == "DummyService"
        assert info["requires"] == []
        assert info["started"] is False
        assert info["attached"] is True


# ─── AppContext ─────────────────────────────────────────────


class NeedsDummy(Service):
    name = "needs_dummy"
    requires = ("dummy",)

    def __init__(self, context=None):
        super().__init__(context)
        self.started_after = None

    def start(self):
        self.started_after = self.context.get("dummy")
        super().start()


class ExplodingService(Service):
    name = "exploding"

    def start(self):
        raise RuntimeError("启动就炸")


class TestAppContext:
    def test_register_and_require(self):
        ctx = AppContext()
        svc = ctx.register(DummyService())
        assert ctx.require("dummy") is svc
        assert ctx.names() == ["dummy"]
        assert ctx.has("dummy") is True
        assert ctx.get("dummy") is svc
        assert ctx.get("nope") is None

    def test_explicit_name_overrides_class_name(self):
        ctx = AppContext()
        ctx.register(DummyService(), name="alias")
        assert ctx.names() == ["alias"]

    def test_duplicate_registration_rejected_unless_replace(self):
        ctx = AppContext()
        first = ctx.register(DummyService())
        with pytest.raises(AlreadyExists):
            ctx.register(DummyService())
        second = ctx.register(DummyService(), replace=True)
        assert ctx.require("dummy") is second
        assert second is not first

    def test_register_instance_and_unregister(self):
        ctx = AppContext()
        ctx.register_instance("shared", object())
        assert ctx.has("shared")
        assert ctx.unregister("shared") is True
        assert ctx.unregister("shared") is False

    def test_require_missing_lists_what_exists(self):
        ctx = AppContext()
        ctx.register(DummyService())
        with pytest.raises(ServiceNotAvailable) as info:
            ctx.require("nope")
        assert "dummy" in (info.value.detail or "")

    def test_register_auto_attaches(self):
        ctx = AppContext()
        svc = ctx.register(DummyService())
        assert svc.context is ctx

    def test_find_by_type(self):
        ctx = AppContext()
        svc = ctx.register(DummyService())
        assert ctx.find(DummyService) == [svc]
        assert ctx.find(ExplodingService) == []

    def test_start_all_respects_dependency_order(self):
        ctx = AppContext()
        dummy = ctx.register(DummyService())
        needs = ctx.register(NeedsDummy())
        order = ctx.start_all()
        assert order == ["dummy", "needs_dummy"], "被依赖者必须先启动"
        assert needs.started_after is dummy

    def test_start_all_detects_dependency_cycle(self):
        class A(Service):
            name = "a"
            requires = ("b",)

        class B(Service):
            name = "b"
            requires = ("a",)

        ctx = AppContext()
        ctx.register(A())
        ctx.register(B())
        with pytest.raises(ServiceNotAvailable) as info:
            ctx.start_all()
        assert "循环" in info.value.message

    def test_one_failing_service_does_not_block_the_rest(self):
        ctx = AppContext()
        ctx.register(ExplodingService())
        ctx.register(DummyService())
        started = ctx.start_all()
        assert "exploding" not in started
        assert "dummy" in started
        assert isinstance(ctx._start_failures["exploding"], RuntimeError)

    def test_stop_all_is_reverse_order_and_swallows_errors(self):
        events = []

        class Recorder(Service):
            def __init__(self, context=None, tag=""):
                super().__init__(context)
                self.tag = tag

            def start(self):
                events.append(f"start:{self.tag}")
                super().start()

            def stop(self):
                events.append(f"stop:{self.tag}")
                if self.tag == "b":
                    raise RuntimeError("停止时炸了")
                super().stop()

        ctx = AppContext()
        ctx.register(Recorder(tag="a"), name="a")
        ctx.register(Recorder(tag="b"), name="b")
        ctx.start_all()
        ctx.stop_all()
        assert events == ["start:a", "start:b", "stop:b", "stop:a"]

    def test_ui_defaults_to_null_and_switching_marks_ready(self):
        ctx = AppContext()
        assert isinstance(ctx.ui, NullUIPort)
        assert ctx.ui_ready is False
        port = RecordingUIPort()
        ctx.set_ui(port)
        assert ctx.ui is port
        assert ctx.ui_ready is True
        ctx.set_ui(None)
        assert isinstance(ctx.ui, NullUIPort)

    def test_scheduler_injection_reaches_task_runner(self):
        ctx = AppContext()
        assert ctx.tasks.has_scheduler is False
        ctx.set_scheduler(lambda fn: fn())
        assert ctx.tasks.has_scheduler is True

    def test_legacy_callbacks_merge_and_warn_on_duplicates(self, caplog):
        class ProviderA(Service):
            name = "pa"

            def legacy_callbacks(self):
                return {"get_x": lambda: 1, "shared": lambda: "a"}

        class ProviderB(Service):
            name = "pb"

            def legacy_callbacks(self):
                return {"set_y": lambda v: v, "shared": lambda: "b"}

        ctx = AppContext()
        ctx.register(ProviderA())
        ctx.register(ProviderB())
        with caplog.at_level(logging.WARNING):
            cbs = ctx.legacy_callbacks()
        assert sorted(cbs) == ["get_x", "set_y", "shared"]
        assert cbs["shared"]() == "b", "后注册者覆盖前者"
        assert "shared" in caplog.text

    def test_legacy_callbacks_survives_broken_provider(self, caplog):
        class Broken(Service):
            name = "broken"

            def legacy_callbacks(self):
                raise RuntimeError("汇总炸了")

        class Good(Service):
            name = "good"

            def legacy_callbacks(self):
                return {"ok": lambda: True}

        ctx = AppContext()
        ctx.register(Broken())
        ctx.register(Good())
        with caplog.at_level(logging.ERROR):
            cbs = ctx.legacy_callbacks()
        assert cbs["ok"]() is True
        assert "汇总回调失败" in caplog.text

    def test_describe_is_json_friendly(self):
        import json

        ctx = AppContext()
        ctx.register(DummyService())
        json.dumps(ctx.describe())  # 不抛异常即为合格


# ─── 事件总线 ───────────────────────────────────────────────


class TestEventBus:
    def test_subscribe_publish_unsubscribe(self):
        bus = EventBus()
        seen = []
        handler = lambda **kw: seen.append(kw)  # noqa: E731
        bus.subscribe("e", handler)
        assert bus.publish("e", a=1) == 1
        bus.unsubscribe("e", handler)
        assert bus.publish("e", a=2) == 0
        assert len(seen) == 1

    def test_duplicate_subscribe_ignored(self):
        bus = EventBus()
        calls = []
        handler = lambda **kw: calls.append(1)  # noqa: E731
        bus.subscribe("e", handler)
        bus.subscribe("e", handler)
        bus.publish("e")
        assert len(calls) == 1

    def test_handler_error_does_not_stop_others(self, caplog):
        bus = EventBus()
        seen = []

        def bad(**kw):
            raise ValueError("坏订阅者")

        bus.subscribe("e", bad)
        bus.subscribe("e", lambda **kw: seen.append("ok"))
        with caplog.at_level(logging.ERROR):
            count = bus.publish("e")
        assert count == 2
        assert seen == ["ok"]
        assert "坏订阅者" in caplog.text or "抛出异常" in caplog.text

    def test_unsubscribe_all(self):
        bus = EventBus()
        handler = lambda **kw: None  # noqa: E731
        bus.subscribe("a", handler)
        bus.subscribe("b", handler)
        assert bus.unsubscribe_all(handler) == 2
        assert bus.events() == []

    def test_history_records_handler_count(self):
        bus = EventBus()
        bus.publish("nothing", x=1)
        history = bus.history()
        assert history[-1].event == "nothing"
        assert history[-1].handler_count == 0
        assert history[-1].payload == {"x": 1}

    def test_history_is_bounded(self):
        bus = EventBus(history_size=5)
        for i in range(20):
            bus.publish("e", i=i)
        assert len(bus.history()) == 5
        assert bus.history()[-1].payload == {"i": 19}

    def test_reentrant_publish_does_not_deadlock(self):
        bus = EventBus()
        seen = []

        def outer(**kw):
            seen.append("outer")
            bus.publish("inner")

        bus.subscribe("outer", outer)
        bus.subscribe("inner", lambda **kw: seen.append("inner"))
        bus.publish("outer")
        assert seen == ["outer", "inner"]

    def test_clear_and_handler_count(self):
        bus = EventBus()
        bus.subscribe("e", lambda **kw: None)
        assert bus.handler_count("e") == 1
        bus.clear()
        assert bus.handler_count("e") == 0


# ─── UI 端口 ────────────────────────────────────────────────


class TestPorts:
    def test_progress_report_fraction(self):
        assert ProgressReport(current=1, total=4).fraction == 0.25
        assert ProgressReport(current=1, total=4).determinate is True
        assert ProgressReport(current=-1, total=0).determinate is False
        assert ProgressReport(current=-1, total=0).fraction == 0.0
        assert ProgressReport(current=5, total=4).fraction == 1.0
        assert ProgressReport(current=-3, total=4).fraction == 0.0

    def test_null_port_never_raises_and_degrades_predictably(self):
        port = NullUIPort()
        assert port.is_available() is False
        assert port.confirm("t", "m", default=True) is True
        assert port.confirm("t", "m") is False
        assert port.ask_text("t", "p") is None
        assert port.choose("t", "p", []) is None
        assert port.set_clipboard("x") is False
        port.show_info("t", "m")
        port.show_warning("t", "m")
        port.show_error("t", "m")
        port.show_progress(ProgressReport(current=1, total=2))
        port.close_progress()
        port.notify("hi", "warning")

    def test_null_port_choice_returns_default(self):
        port = NullUIPort()
        options = [Choice(value="a", label="A"), Choice(value="b", label="B")]
        assert port.choose("t", "p", options, default="b") == "b"

    def test_recording_port_captures_and_answers(self):
        port = RecordingUIPort(confirm=True, text="输入值", choice="ocean", available=True)
        assert port.is_available() is True
        assert port.confirm("t", "m") is True
        assert port.ask_text("t", "p") == "输入值"
        assert port.choose("t", "p", [Choice("ocean", "海洋")]) == "ocean"
        assert port.set_clipboard("abc") is True
        port.notify("完成", "success")
        assert port.methods() == ["confirm", "ask_text", "choose", "set_clipboard", "notify"]
        assert port.find("choose")[0]["options"] == ["ocean"]

    def test_recording_port_falls_back_to_defaults(self):
        port = RecordingUIPort()
        assert port.confirm("t", "m", default=True) is True
        assert port.ask_text("t", "p") is None
        assert port.choose("t", "p", [Choice("a", "A")], default="a") == "a"
        port.clear()
        assert port.calls == []

    def test_recording_port_can_simulate_unavailable_shell(self):
        # available 与 clipboard 是两个独立开关，测试替身不该自作聪明地联动
        port = RecordingUIPort(available=False, clipboard=False)
        assert port.is_available() is False
        assert port.set_clipboard("x") is False

    def test_null_port_satisfies_protocol(self):
        assert isinstance(NullUIPort(), UIPort)
        assert isinstance(RecordingUIPort(), UIPort)
