from agentguard.gateway.models import ShellExecAction
from agentguard.policy.models import PolicyDecision, PolicyEffect


class ShellPolicy:
    _allowlisted_executables = frozenset({"echo", "python", "python3", "pytest"})
    _blocked_executables = frozenset(
        {"sudo", "su", "docker", "kubectl", "chmod", "chown", "mkfs", "mount", "umount"}
    )
    _blocked_fragments = (
        "rm -rf /",
        "rm -rf /*",
        "rm -fr /",
        "rm -fr /*",
        "Remove-Item -Recurse -Force C:\\",
    )

    def evaluate(self, action: ShellExecAction) -> PolicyDecision:
        executable = action.argv[0]
        command_text = " ".join(action.argv)

        if executable in self._blocked_executables or self._contains_blocked_fragment(command_text):
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                risk_level="critical",
                rule_id="shell.dangerous_command",
                reason="Command is explicitly blocked.",
            )

        if executable in self._allowlisted_executables:
            return PolicyDecision(
                effect=PolicyEffect.ALLOW,
                risk_level="low",
                rule_id="shell.allowlisted_command",
                reason="Command is allowlisted for MVP execution.",
            )

        return PolicyDecision(
            effect=PolicyEffect.REQUIRE_APPROVAL,
            risk_level="medium",
            rule_id="shell.default_requires_approval",
            reason="Command is not allowlisted for automatic execution.",
        )

    def _contains_blocked_fragment(self, command_text: str) -> bool:
        return any(fragment in command_text for fragment in self._blocked_fragments)
