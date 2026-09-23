import shlex

from git_autofetch.ssh import SshOptions, parse_ssh_g

OPTIONS = SshOptions("/tmp/git-autofetch-501/%r@%h:%p", "8h", 10)


def options(command: str) -> dict[str, str]:
    words = shlex.split(command)
    assert words[0] == "ssh"
    pairs = [words[i + 1] for i, word in enumerate(words) if word == "-o"]
    return dict(pair.split("=", 1) for pair in pairs)


def test_canary_opens_a_shared_connection():
    assert options(OPTIONS.canary_command()) == {
        "ControlMaster": "auto",
        "ControlPath": "/tmp/git-autofetch-501/%r@%h:%p",
        "ControlPersist": "8h",
        "ConnectTimeout": "10",
    }


def test_batch_can_only_ride_an_existing_connection():
    assert options(OPTIONS.batch_command()) == {
        "ControlMaster": "no",
        "ControlPath": "/tmp/git-autofetch-501/%r@%h:%p",
        "ProxyCommand": "false",
        "BatchMode": "yes",
    }


def test_commands_quote_paths_with_spaces():
    command = SshOptions("/tmp/with space/%C").batch_command()
    assert options(command)["ControlPath"] == "/tmp/with space/%C"


def test_parse_ssh_g():
    output = "user git\nhostname github.com\nport 22\nproxycommand none\n"
    resolved = parse_ssh_g(output)
    assert resolved is not None
    assert (resolved.hostname, resolved.port, resolved.proxied) == ("github.com", 22, False)


def test_parse_ssh_g_alias_and_port():
    resolved = parse_ssh_g("hostname 10.0.0.5\nport 2222\n")
    assert resolved is not None
    assert (resolved.hostname, resolved.port) == ("10.0.0.5", 2222)


def test_parse_ssh_g_notices_proxies():
    assert parse_ssh_g("hostname h\nproxyjump bastion\n").proxied
    assert parse_ssh_g("hostname h\nproxycommand nc %h %p\n").proxied


def test_parse_ssh_g_without_hostname():
    assert parse_ssh_g("port 22\n") is None
