from agentguard.gateway.models import ShellExecAction
from agentguard.policy.models import PolicyEffect
from agentguard.policy.shell import ShellPolicy


def test_allows_mvp_development_command() -> None:
    action = ShellExecAction(argv=["python3", "-c", "print('hello')"])

    decision = ShellPolicy().evaluate(action)

    assert decision.effect is PolicyEffect.ALLOW
    assert decision.risk_level == "low"
    assert decision.rule_id == "shell.allowlisted_command"


def test_denies_explicitly_blocked_command() -> None:
    action = ShellExecAction(argv=["sudo", "whoami"])

    decision = ShellPolicy().evaluate(action)

    assert decision.effect is PolicyEffect.DENY
    assert decision.risk_level == "critical"
    assert decision.rule_id == "shell.dangerous_command"


def test_requires_approval_for_unknown_command() -> None:
    action = ShellExecAction(argv=["git", "status"])

    decision = ShellPolicy().evaluate(action)

    assert decision.effect is PolicyEffect.REQUIRE_APPROVAL
    assert decision.risk_level == "medium"
    assert decision.rule_id == "shell.default_requires_approval"
