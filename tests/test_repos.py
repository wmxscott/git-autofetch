import logging
import os
from pathlib import Path

import pytest
from conftest import make_repo

from git_autofetch.config import RepoEntry
from git_autofetch.repos import SshTarget, discover, parse_remotes, parse_ssh_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:owner/repo.git", SshTarget("github.com", "git")),
        ("github.com:owner/repo", SshTarget("github.com")),
        ("GitHub.com:owner/repo", SshTarget("github.com")),
        ("my-alias:repo.git", SshTarget("my-alias")),
        ("ssh://git@github.com/owner/repo.git", SshTarget("github.com", "git")),
        (
            "ssh://git@gitlab.example.com:2222/team/repo.git",
            SshTarget("gitlab.example.com", "git", 2222),
        ),
        ("git+ssh://host/repo", SshTarget("host")),
        ("ssh+git://me@host/repo", SshTarget("host", "me")),
    ],
)
def test_ssh_urls(url, expected):
    assert parse_ssh_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo.git",
        "http://host/repo",
        "git://host/repo",
        "file:///srv/repo.git",
        "/srv/repo.git",
        "./relative/repo",
        "../up:colon/repo",
        "ssh://host:notaport/repo",
        "",
    ],
)
def test_non_ssh_urls(url):
    assert parse_ssh_url(url) is None


def test_target_destination_and_args():
    target = SshTarget("host", "git", 2222)
    assert target.destination == "git@host"
    assert target.ssh_args() == ["-p", "2222", "git@host"]
    assert str(target) == "git@host:2222"
    assert SshTarget("host").ssh_args() == ["host"]


def test_parse_remotes_keeps_fetch_urls():
    output = (
        "origin\tgit@github.com:o/r.git (fetch)\n"
        "origin\tgit@github.com:o/r.git (push)\n"
        "mirror\thttps://example.com/r.git (fetch)\n"
        "odd\tpath with spaces (fetch)\n"
    )
    remotes = parse_remotes(output)
    assert [(r.name, r.url) for r in remotes] == [
        ("origin", "git@github.com:o/r.git"),
        ("mirror", "https://example.com/r.git"),
        ("odd", "path with spaces"),
    ]
    assert remotes[0].target == SshTarget("github.com", "git")
    assert remotes[1].target is None


def test_discover_single_repo(tmp_path: Path):
    repo = make_repo(tmp_path / "one")
    assert discover([RepoEntry(repo)]) == [repo.resolve()]


def test_discover_walks_to_max_depth(tmp_path: Path):
    make_repo(tmp_path / "a")
    make_repo(tmp_path / "group/b")
    make_repo(tmp_path / "group/deeper/c")
    make_repo(tmp_path / "a/nested")  # inside a repo: not searched
    found = discover([RepoEntry(tmp_path, recursive=True, max_depth=2)])
    assert [p.relative_to(tmp_path.resolve()).as_posix() for p in found] == ["a", "group/b"]


def test_discover_skips_symlinked_directories(tmp_path: Path):
    make_repo(tmp_path / "elsewhere/repo")
    (tmp_path / "root").mkdir()
    os.symlink(tmp_path / "elsewhere", tmp_path / "root/link")
    assert discover([RepoEntry(tmp_path / "root", recursive=True, max_depth=3)]) == []


def test_discover_accepts_a_git_file(tmp_path: Path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /elsewhere\n")
    assert discover([RepoEntry(worktree)]) == [worktree.resolve()]


def test_discover_deduplicates(tmp_path: Path):
    repo = make_repo(tmp_path / "one")
    entries = [RepoEntry(repo), RepoEntry(tmp_path, recursive=True, max_depth=1)]
    assert discover(entries) == [repo.resolve()]


def test_discover_warns_about_bad_paths(tmp_path: Path, caplog):
    (tmp_path / "plain").mkdir()
    with caplog.at_level(logging.WARNING):
        assert discover([RepoEntry(tmp_path / "missing"), RepoEntry(tmp_path / "plain")]) == []
    assert "does not exist" in caplog.text
    assert "recursive = true" in caplog.text
