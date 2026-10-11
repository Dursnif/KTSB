"""P116 B33 — Mechanic's shell may only read (Stian 2026-10-01: «kun lese!!»)."""

import pytest

from kaare_core.agents.mechanic.shell_guard import bound_follow, readonly_violation

ALLOWED = [
    "journalctl -u kaare.service -n 100",
    "journalctl -u kaare.service -n 50 --no-pager | grep -i error",
    "sudo journalctl -u ssh -n 20",
    "systemctl status kaare.service",
    "systemctl is-active kaare-argus",
    "cat /kaare/configs/settings.yaml | head -20",
    "grep -rn 'def ask_with_tools' /kaare/kaare_core",
    "find /kaare/logs -name '*.log' -size +10M",
    "ls -la /kaare/state",
    "tail -n 200 /kaare/logs/llm_calls.log",
    "df -h",
    "free -m",
    "uptime",
    "hostname",
    "nvidia-smi",
    "nvidia-smi --query-gpu=memory.used --format=csv",
    "docker ps",
    "docker logs --tail 50 kaare_agents2",
    "ip addr",
    "ip route",
    "ip -br link",
    "dpkg -l | grep ollama",
    "ps aux | sort -k3 | tail -5",
    "ha core info",
    "pihole status",
]

FORBIDDEN = [
    "ls; unlink /kaare/x",  # chaining
    "cat x && rm y",
    "ls || rm y",
    "echo $(rm -rf /kaare)",  # substitution
    "echo `rm x`",
    "echo hi > /kaare/configs/x.yaml",  # redirection
    "cat < /etc/passwd",
    "rm -rf /kaare/state",
    "mv a b",
    "cp a b",
    "touch x",
    "mkdir y",
    "sed -i s/a/b/ f",
    "awk 'BEGIN{system(\"rm x\")}'",
    "python3 -c 'import os'",
    "bash -c ls",
    "sh x.sh",
    "xargs rm",
    "cat f | tee out.txt",
    "find /kaare -name '*.tmp' -delete",
    "find / -exec unlink {} ;",
    "journalctl --vacuum-size=1M",
    "dmesg -C",
    "date -s '2020-01-01'",
    "hostname newname",
    "env rm file",
    "ifconfig eth0 down",
    "ip link set eth0 down",
    "ip addr add 1.2.3.4/24 dev eth0",
    "nvidia-smi -pl 100",
    "nvidia-smi --gpu-reset",
    "systemctl restart kaare",
    "systemctl stop kaare-argus",
    "docker rm kaare_agents1",
    "docker exec x ls",
    "apt install vim",
    "dpkg -i x.deb",
    "ha core restart",
    "ha addons stop x",
    "sudo rm x",
    "sudo -u root ls",
    "sort -o /kaare/x f",
    "",
]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_read_commands_allowed(cmd):
    assert readonly_violation(cmd) is None, readonly_violation(cmd)


@pytest.mark.parametrize("cmd", FORBIDDEN)
def test_changing_commands_rejected(cmd):
    assert readonly_violation(cmd) is not None


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("journalctl -u kaare.service -f", "journalctl -u kaare.service -n 100"),
        ("journalctl -u kaare.service -f -n 30", "journalctl -u kaare.service -n 30"),
        ("tail -f /kaare/logs/llm_calls.log", "tail /kaare/logs/llm_calls.log -n 100"),
        ("docker logs -f kaare_agents2", "docker logs kaare_agents2 --tail 100"),
    ],
)
def test_follow_mode_bounded(cmd, expected):
    bounded, changed = bound_follow(cmd)
    assert changed and bounded == expected
    assert readonly_violation(bounded) is None
