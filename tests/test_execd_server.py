from agentguard.execd.server import ExecdHandler


def test_execd_handler_has_command_route() -> None:
    assert hasattr(ExecdHandler, "do_POST")
