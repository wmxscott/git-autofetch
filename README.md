# git-autofetch

[![CI](https://github.com/wmxscott/git-autofetch/actions/workflows/ci.yml/badge.svg)](https://github.com/wmxscott/git-autofetch/actions/workflows/ci.yml)

Keeps your git repositories' remote branches up to date in the background on macOS, without surprise login prompts.

`git status` then tells you when you're behind, and your prompt, lazygit and `git log origin/main` show what's actually on the remote, without you remembering to fetch. It's built for people whose ssh logins need them: a hardware security key that wants a touch, a passphrase, or an agent like 1Password that asks for approval. git-autofetch logs in at most once per host and fetches every repository over that one connection. It never pops up a prompt while you're away.

## Why not `git maintenance`?

Git's built-in `git maintenance` has a `prefetch` task, but it does a different job:

| | `git maintenance` prefetch | git-autofetch |
|---|---|---|
| Updates `origin/*` and "behind by N" | No: it fetches into hidden `refs/prefetch/` refs to make your next fetch faster | Yes, a real `git fetch --all --prune` |
| Prunes branches deleted on the remote | No | Yes |
| How often | Hourly at most | Every 5 minutes by default, as often as you like |
| Which repositories | Each one registered by hand | Whole folders, so new clones are picked up |
| One hung connection | Blocks the rest | Per-repository timeouts, fetched in parallel |
| Logins that need you | One per repository, every hour | At most one per host, then none until the connection drops |

They work fine together: maintenance also handles housekeeping like `gc` and the commit graph.

## How it works

Every cycle, for each ssh host your remotes use:

1. **Check the host is reachable.** If you're offline, the host is skipped quietly.
2. **Canary fetch.** One repository on that host fetches with your normal ssh setup and leaves a shared connection open (an ssh ControlMaster). If a connection is already open this needs no login at all. Otherwise this is the one place a login can happen.
3. **Fetch everything else over that connection.** The remaining repositories fetch in parallel with an ssh command that can only reuse the shared connection. It sets `ProxyCommand=false`, so without a live connection it fails instantly instead of trying your keys. **Only the canary can ever ask you for anything.**

A fresh login is only attempted when it can succeed without surprising you:

- If you set `hardware_key` and the key isn't plugged in, the host waits until it is.
- If nobody has touched the keyboard, mouse or trackpad for `idle_threshold` seconds, the host waits until you're back, so no prompt appears in an empty room.
- If a login starts but nobody finishes it, for example a touch that never comes, the host backs off for 1, 2, 5, then 10 minutes.

None of this applies while the shared connection is alive. With it, fetches carry on silently whether or not the key is plugged in or you're at your desk.

HTTPS and local remotes don't use ssh. They're fetched every cycle, with git's credential prompts turned off (`GIT_TERMINAL_PROMPT=0`), so a missing password fails instead of hanging.

## Install

### Homebrew

```sh
brew install wmxscott/tap/git-autofetch
```

Write a config (below), then start the service:

```sh
brew services start git-autofetch
```

It runs now and at every login, and restarts if it ever exits.

### From source

Needs Python 3.11 or later.

```sh
uv tool install git+https://github.com/wmxscott/git-autofetch
# or: pipx install git+https://github.com/wmxscott/git-autofetch
```

Run `git-autofetch run` from a launch agent, or just run `git-autofetch fetch` whenever you like.

## Configure

The config lives at `~/.config/git-autofetch/config.toml`, or `$XDG_CONFIG_HOME/git-autofetch/config.toml`. Set `GIT_AUTOFETCH_CONFIG` to use another path. Only `repos` is required:

```toml
[[repos]]
path = "~/src/website"          # one repository

[[repos]]
path = "~/src"                  # every repository under ~/src ...
recursive = true
max_depth = 2                   # ... up to two folders deep
```

Everything else is optional:

| Setting | Default | |
|---|---|---|
| `interval` | `300` | Seconds between fetch cycles |
| `parallel` | `4` | Repositories fetched at once |
| `fetch_timeout` | `60` | Seconds before a single fetch is abandoned |
| `connect_timeout` | `30` | Seconds the canary fetch has to finish, including any login |
| `idle_threshold` | `180` | Seconds without keyboard, mouse or trackpad input after which a fresh login waits for you to come back. Raise it to log in even while you're away |
| `hardware_key` | *(none)* | USB id of your security key, `"0xVID"` or `"0xVID:0xPID"` in hex. A fresh login waits until it's plugged in |
| `control_path` | *(own socket)* | ssh `ControlPath` for the shared connection. See below |
| `control_persist` | `"24h"` | How long a shared connection stays open once idle |
| `ssh_auth_sock` | *(inherited)* | ssh agent socket, for agents launchd services don't know about |
| `askpass` | *(none)* | Program ssh uses to ask for a passphrase or show a touch prompt |

A complete config with every setting. Leave out any line you don't need:

```toml
# ~/.config/git-autofetch/config.toml

interval = 300          # seconds between fetch cycles
parallel = 4            # repositories fetched at once
fetch_timeout = 60      # seconds before a single fetch is abandoned
connect_timeout = 30    # seconds the canary fetch has, including any login
idle_threshold = 180    # a fresh login waits if you've been away this long

# Only if you use a hardware security key: a fresh login waits until it's
# plugged in. "0xVID" or "0xVID:0xPID" in hex; 0x1050 is any YubiKey.
hardware_key = "0x1050"

# Share the connections your own ssh opens. Use the ControlPath from your
# ~/.ssh/config, or leave both out to keep git-autofetch's connections separate.
control_path = "~/.ssh/cm-%r@%h:%p"
control_persist = "24h"

# An agent launchd services don't know about, here 1Password's.
ssh_auth_sock = "~/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"

# A graphical program for ssh's passphrase and touch prompts, such as
# https://github.com/wmxscott/seckey-dialog
askpass = "/opt/homebrew/opt/seckey-dialog/bin/seckey-dialog"

[[repos]]
path = "~/src/website"

[[repos]]
path = "~/src"
recursive = true
max_depth = 2
```

The config is read again before every cycle, so changes apply without a restart. A path that doesn't exist, or a folder that isn't a repository and isn't marked `recursive`, is logged and skipped.

### Reuse the connection your own ssh opens

By default git-autofetch keeps its shared connections to itself, in `/tmp/git-autofetch-<uid>/`. If your `~/.ssh/config` already shares connections, point it at the same sockets. Then a login you do by hand also serves git-autofetch, and one git-autofetch opens also speeds up your own `git push`:

```toml
control_path = "~/.ssh/cm-%r@%h:%p"   # whatever ControlPath your ssh config uses
control_persist = "168h"
```

### Agents

launchd services don't see agent settings from your shell. If your key lives in a custom agent, name its socket, for example for 1Password:

```toml
ssh_auth_sock = "~/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
```

An `IdentityAgent` line in `~/.ssh/config` works too, since the canary fetch uses your ssh config.

## Hardware security keys

Set `hardware_key` to your key's USB id. To find it, plug the key in and run:

```sh
ioreg -p IOUSB -l | grep -E '"(USB Product Name|idVendor|idProduct)"'
```

ioreg prints the ids in decimal; `hardware_key` takes hex, so a vendor of `4176` is `"0x1050"`.

To fetch straight away when you plug the key in, rather than at the next cycle, install the resume agent:

```sh
git-autofetch install-resume-agent
```

It asks launchd to run `git-autofetch resume` whenever a device with that USB id appears. Remove it with `git-autofetch uninstall-resume-agent`, and install it again after changing `hardware_key`.

## Commands

| Command | |
|---|---|
| `git-autofetch run` | Keep running, fetching every `interval`. This is what the service runs |
| `git-autofetch fetch` | Run one cycle now and print the results |
| `git-autofetch resume` | Forget any back-off and fetch now |
| `git-autofetch status` | Show the last cycle, what each host is waiting for, and when the next cycle runs |
| `git-autofetch list-repos` | Print the repositories the config finds, and the ssh hosts they use |
| `git-autofetch install-resume-agent` | Fetch as soon as the hardware key is plugged in. `--print` shows the launch agent instead of installing it |
| `git-autofetch uninstall-resume-agent` | Remove it |

Add `-v` before the command, as in `git-autofetch -v fetch`, to log every step.

## Files

| Path | |
|---|---|
| `~/.config/git-autofetch/config.toml` | Config |
| `~/.local/state/git-autofetch/` | Last cycle, back-off timers and the lock that keeps runs apart. Follows `$XDG_STATE_HOME` |
| `/tmp/git-autofetch-<uid>/` | Shared connection sockets, unless you set `control_path`. Kept short because macOS limits socket paths to 104 bytes |
| `$(brew --prefix)/var/log/git-autofetch.log` | Service log, with Homebrew |

## Logs

A cycle where nothing changed logs nothing. git-autofetch logs when a host becomes ready or starts waiting, when a repository starts or stops failing, and when the set of repositories changes. So the log reads as a history of what went wrong and when it recovered. `git-autofetch status` shows the current state.

## Limitations

- **macOS only.** The hardware-key and idle checks use `ioreg`, and the service uses launchd.
- **OpenSSH.** git-autofetch sets `GIT_SSH_COMMAND`, which overrides a repository's own `core.sshCommand`.
- **Hosts behind `ProxyJump` or `ProxyCommand`** aren't checked for reachability before fetching. Their connections are shared like any other.

## Development

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
```

The fetch cycle takes its system access (git, ssh, ioreg, the network) as a parameter, so `tests/test_cycle.py` drives every host scenario against a fake. `tests/test_system.py` checks the real commands against real repositories and ssh, including that a fetch without a shared connection fails instead of logging in.

## License

[MIT](LICENSE)
