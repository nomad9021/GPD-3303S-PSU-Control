#!/usr/bin/env sh
# GPD-3303S Control — one-line installer for Linux and macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/nomad9021/GPD-3303S-PSU-Control/main/install.sh | sh
#
# Installs into an isolated environment (uv, pipx or a private venv — whichever
# is available), links a `gpd3303s` launcher into ~/.local/bin, and records the
# method used so the app's built-in updater can upgrade itself later.

set -eu

REPO="${GPD3303S_REPO:-nomad9021/GPD-3303S-PSU-Control}"
REF="${GPD3303S_REF:-}"
PACKAGE="gpd3303s-control"
BIN_DIR="${GPD3303S_BIN_DIR:-$HOME/.local/bin}"
VENV_DIR="${GPD3303S_VENV_DIR:-$HOME/.local/share/$PACKAGE/venv}"
MARKER_DIR="$HOME/.local/share/$PACKAGE"

# On macOS the data directory follows Apple's layout, matching config.data_dir().
if [ "$(uname -s)" = "Darwin" ]; then
  MARKER_DIR="$HOME/Library/Application Support/$PACKAGE"
fi

RED=''; GREEN=''; YELLOW=''; BOLD=''; RESET=''
if [ -t 1 ]; then
  RED=$(printf '\033[31m'); GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m')
  BOLD=$(printf '\033[1m'); RESET=$(printf '\033[0m')
fi

info()  { printf '%s==>%s %s\n' "$GREEN" "$RESET" "$1"; }
warn()  { printf '%s warn%s %s\n' "$YELLOW" "$RESET" "$1" >&2; }
die()   { printf '%serror%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }
have()  { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# Work out which release to install.
# ---------------------------------------------------------------------------
resolve_ref() {
  if [ -n "$REF" ]; then
    printf '%s' "$REF"
    return
  fi
  # Prefer the newest published release; fall back to the default branch when
  # the repository has no releases yet.
  tag=''
  if have curl; then
    tag=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
      | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1) || tag=''
  elif have wget; then
    tag=$(wget -qO- "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
      | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1) || tag=''
  fi
  if [ -n "$tag" ]; then printf '%s' "$tag"; else printf 'HEAD'; fi
}

record_method() {
  mkdir -p "$MARKER_DIR"
  printf '%s' "$1" > "$MARKER_DIR/install-method"
}

link_launcher() {
  # $1 = absolute path to the installed executable
  mkdir -p "$BIN_DIR"
  if [ "$(dirname "$1")" != "$BIN_DIR" ]; then
    ln -sf "$1" "$BIN_DIR/gpd3303s"
  fi
}

check_path() {
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
      warn "$BIN_DIR is not on your PATH."
      printf '       Add this to your shell profile:\n\n         export PATH="%s:$PATH"\n\n' "$BIN_DIR"
      ;;
  esac
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
if [ -z "${GPD3303S_SPEC:-}" ]; then
  have git || die "git is required to install from GitHub. Install git and re-run."
fi

# GPD3303S_SPEC lets you install from a local checkout or a custom requirement
# instead of a published release, which is also how the test suite exercises this.
if [ -n "${GPD3303S_SPEC:-}" ]; then
  SPEC="$GPD3303S_SPEC"
  REF="$SPEC"
else
  REF=$(resolve_ref)
  SPEC="git+https://github.com/$REPO@$REF"
fi

printf '\n%sGPD-3303S Control%s — installing %s\n\n' "$BOLD" "$RESET" "$REF"

# ---------------------------------------------------------------------------
# Install, preferring the most isolated tool available.
# ---------------------------------------------------------------------------
if have uv; then
  info "Installing with uv"
  uv tool install --force "$SPEC"
  record_method "uv-tool"
  # uv puts tool shims in its own bin dir; find the one it just made.
  UV_BIN="$(uv tool dir 2>/dev/null)/../bin" || UV_BIN=""
  if [ -x "$HOME/.local/bin/gpd3303s" ]; then
    :  # uv already installed into ~/.local/bin
  elif [ -n "$UV_BIN" ] && [ -x "$UV_BIN/gpd3303s" ]; then
    link_launcher "$(cd "$UV_BIN" && pwd)/gpd3303s"
  fi

elif have pipx; then
  info "Installing with pipx"
  pipx install --force "$SPEC"
  record_method "pipx"

else
  info "Installing into a private virtual environment"
  PYTHON=""
  for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    if have "$candidate"; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PYTHON="$candidate"
        break
      fi
    fi
  done
  [ -n "$PYTHON" ] || die "Python 3.9 or newer is required but was not found."

  "$PYTHON" -m venv "$VENV_DIR" 2>/dev/null || die "Could not create a virtual environment.
       On Debian/Ubuntu install the venv package first:  sudo apt install python3-venv"
  "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
  "$VENV_DIR/bin/python" -m pip install --quiet --upgrade "$SPEC"
  record_method "venv"
  link_launcher "$VENV_DIR/bin/gpd3303s"
fi

# ---------------------------------------------------------------------------
# Serial port access on Linux needs group membership, which trips up most users.
# ---------------------------------------------------------------------------
if [ "$(uname -s)" = "Linux" ]; then
  if ! id -nG 2>/dev/null | tr ' ' '\n' | grep -qx 'dialout\|uucp'; then
    warn "Your user is not in the 'dialout' group, so opening /dev/ttyUSB* may fail."
    printf '       Grant access with:\n\n         sudo usermod -aG dialout %s\n\n       then log out and back in.\n\n' "$(id -un)"
  fi
fi

check_path

printf '\n%sInstalled.%s Start it with:\n\n    %sgpd3303s%s\n\n' "$GREEN" "$RESET" "$BOLD" "$RESET"
printf 'No hardware handy? Try the built-in simulator:\n\n    gpd3303s --simulate\n\n'
