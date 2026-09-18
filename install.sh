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
log()   { [ -n "${GPD3303S_VERBOSE:-}" ] && printf '    %s\n' "$1" || true; }
warn()  { printf '%s warn%s %s\n' "$YELLOW" "$RESET" "$1" >&2; }
die()   { printf '%serror%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }
have()  { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# Work out which release to install.
# ---------------------------------------------------------------------------
fetch_url() {
  if have curl; then
    curl -fsSL "$1" 2>/dev/null
  elif have wget; then
    wget -qO- "$1" 2>/dev/null
  fi
}

latest_release_tag() {
  fetch_url "https://api.github.com/repos/$REPO/releases/latest" \
    | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

# The version in the default branch's source, which is the newest code there is.
default_branch_version() {
  fetch_url "https://raw.githubusercontent.com/$REPO/HEAD/src/gpd3303s/__init__.py" \
    | sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' | head -n 1
}

# Prints the ref to install.
#
# Normally that is the latest release. But a release can lag the code badly --
# this repository once had a v1.0.0 release holding the retired web interface
# while the default branch had the native app, so every `curl | sh` installed
# the old UI and looked broken. So: compare the two and take whichever is
# newer, which self-heals as soon as a current release exists.
resolve_ref() {
  if [ -n "$REF" ]; then
    printf '%s' "$REF"
    return
  fi

  tag=$(latest_release_tag)
  [ -n "$tag" ] || return 0                 # no releases: caller uses the branch

  head_version=$(default_branch_version)
  if [ -z "$head_version" ]; then
    printf '%s' "$tag"                      # cannot compare; trust the release
    return
  fi

  tag_version=${tag#v}
  newest=$(printf '%s\n%s\n' "$tag_version" "$head_version" | sort -V | tail -n 1)
  if [ "$newest" = "$tag_version" ]; then
    printf '%s' "$tag"
  else
    # The branch is ahead of the newest release; install the branch.
    warn "The latest release ($tag) is older than the current code ($head_version)."
    printf '    Installing the default branch instead. Pin a release with GPD3303S_REF=%s\n\n' "$tag" >&2
    return 0
  fi
}

METHOD=""

record_method() {
  METHOD="$1"
  mkdir -p "$MARKER_DIR"
  printf '%s' "$1" > "$MARKER_DIR/install-method"
}

# ---------------------------------------------------------------------------
# Old installs are why "I reinstalled and still get the old version" happens:
# a launcher from a previous method stays on PATH and keeps winning. Clear out
# the ones we can reach, and name the ones that need root.
# ---------------------------------------------------------------------------
purge_old_installs() {
  removed=""
  name="gpd3303s"

  # Launchers on PATH that are not the one we are about to create.
  IFS=:
  for entry in $PATH; do
    [ -n "$entry" ] || continue
    candidate="$entry/$name"
    [ -f "$candidate" ] || continue
    [ "$candidate" = "$BIN_DIR/$name" ] && continue
    case "$candidate" in
      "$HOME"/*)
        rm -f "$candidate" 2>/dev/null && removed="$removed $candidate" ;;
      *)
        NEEDS_ROOT="$NEEDS_ROOT $candidate" ;;
    esac
  done
  unset IFS

  # A pip install of this package shadows nothing by itself, but it owns the
  # launchers above and will put them back on the next `pip install`.
  for py in python3 python; do
    have "$py" || continue
    if "$py" -m pip show gpd3303s-control >/dev/null 2>&1; then
      "$py" -m pip uninstall -y gpd3303s-control >/dev/null 2>&1 &&
        removed="$removed (pip: gpd3303s-control)"
    fi
    break
  done

  [ -n "$removed" ] && info "Removed previous install:$removed"
  return 0
}

# Put the app in the applications menu so it launches like any other program.
install_desktop_entry() {
  [ "$(uname -s)" = "Linux" ] || return 0

  apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  icons_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
  pixmaps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/512x512/apps"
  mkdir -p "$apps_dir" "$icons_dir" "$pixmaps_dir" || return 0

  # Ship the icon out of the installed package so the menu entry has one. The
  # app reports one file; both sizes live beside it, and installing each into
  # the theme directory it belongs to lets the icon scale in every menu.
  icon_src=$("$LAUNCHER" --icon-path 2>/dev/null || true)
  if [ -n "$icon_src" ] && [ -f "$icon_src" ]; then
    icon_dir=$(dirname "$icon_src")
    [ -f "$icon_dir/icon.png" ] &&
      cp -f "$icon_dir/icon.png" "$pixmaps_dir/gpd3303s-control.png" 2>/dev/null || true
    [ -f "$icon_dir/icon.svg" ] &&
      cp -f "$icon_dir/icon.svg" "$icons_dir/gpd3303s-control.svg" 2>/dev/null || true
  fi

  # An absolute path, never a bare name: a menu entry that goes through PATH
  # can be hijacked by whatever else is installed, which is the whole bug.
  DESKTOP_EXEC="${APP_BINARY:-$LAUNCHER}"

  cat > "$apps_dir/gpd3303s-control.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=GPD Control
GenericName=DC Power Supply Control
Comment=Control a GW Instek GPD-series programmable DC power supply
Exec=$DESKTOP_EXEC
Icon=gpd3303s-control
Terminal=false
Categories=Development;Electronics;Engineering;
Keywords=power supply;psu;gwinstek;gpd;instrument;serial;
StartupNotify=true
DESKTOP

  command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database "$apps_dir" >/dev/null 2>&1 || true
  info "Added GPD Control to your applications menu"
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
# Extra dependency groups, if the caller wants any. None are needed.
EXTRA="${GPD3303S_EXTRA:-}"

if [ -n "${GPD3303S_SPEC:-}" ]; then
  SPEC="$GPD3303S_SPEC"
  REF="$SPEC"
else
  REF=$(resolve_ref)
  if [ -n "$REF" ]; then
    SPEC="git+https://github.com/$REPO@$REF"
  else
    # No releases yet, so install the default branch. The ref has to be left
    # off entirely rather than defaulted to "HEAD": pip turns "@HEAD" into
    # `git checkout -b HEAD`, and git refuses to create a branch by that name.
    SPEC="git+https://github.com/$REPO"
    REF="default branch"
  fi
fi

# pip and uv both accept "name[extra] @ <url>" for a direct reference.
if [ -n "$EXTRA" ]; then
  case "$SPEC" in
    *" @ "*) ;;                                   # caller already qualified it
    /*|.*)  SPEC="$SPEC[$EXTRA]" ;;               # a local path takes [extra] inline
    *)      SPEC="gpd3303s-control[$EXTRA] @ $SPEC" ;;
  esac
fi

printf '\n%sGPD-3303S Control%s — installing %s\n\n' "$BOLD" "$RESET" "$REF"

# Clear out earlier installs first, so the launcher we create is the only one.
NEEDS_ROOT=""
purge_old_installs

# ---------------------------------------------------------------------------
# Install, preferring the most isolated tool available.
# ---------------------------------------------------------------------------
if have uv; then
  info "Installing with uv"
  mkdir -p "$BIN_DIR"
  # uv puts tool launchers in its own bin directory. Point that at the location
  # this script promises, so BIN_DIR is authoritative however we install.
  UV_TOOL_BIN_DIR="$BIN_DIR" uv tool install --force "$SPEC"
  record_method "uv-tool"

elif have pipx; then
  info "Installing with pipx"
  mkdir -p "$BIN_DIR"
  PIPX_BIN_DIR="$BIN_DIR" pipx install --force "$SPEC"
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

# Every path above must leave a working launcher at the advertised location;
# catching that here turns a silent mis-install into a clear failure.
LAUNCHER="$BIN_DIR/gpd3303s"
[ -x "$LAUNCHER" ] || die "installed, but no launcher was created at $LAUNCHER"

# ---------------------------------------------------------------------------
# Installing it is not the same as running it. An older copy earlier on PATH
# keeps winning silently -- a `pip install` as root leaves one in
# /usr/local/bin, which precedes ~/.local/bin nearly everywhere. Reporting
# success while the user still launches the old build is the worst outcome, so
# check and say so.
# ---------------------------------------------------------------------------
same_file() {
  # Resolve both sides where we can, so a symlink to our own launcher is not
  # mistaken for a rival install.
  a="$1"; b="$2"
  [ "$a" = "$b" ] && return 0
  if have readlink; then
    ra=$(readlink -f "$a" 2>/dev/null || printf '%s' "$a")
    rb=$(readlink -f "$b" 2>/dev/null || printf '%s' "$b")
    [ "$ra" = "$rb" ] && return 0
  fi
  return 1
}

check_shadowing() {
  resolved=$(command -v gpd3303s 2>/dev/null || true)
  [ -n "$resolved" ] || return 0            # not on PATH at all; check_path says so
  same_file "$resolved" "$LAUNCHER" && return 0

  printf '\n%serror%s Another gpd3303s is shadowing the one just installed.\n\n' "$RED" "$RESET" >&2
  printf '       just installed:  %s\n' "$LAUNCHER" >&2
  printf '       but PATH finds:  %s   <- this is what runs\n\n' "$resolved" >&2
  printf '       It is outside your home directory, so removing it needs sudo:\n\n' >&2
  printf '         sudo rm %s\n\n' "$resolved" >&2
  printf '       then run:\n\n' >&2
  printf '         gpd3303s --doctor\n\n' >&2
  SHADOWED=1
}

SHADOWED=0
check_shadowing

install_desktop_entry

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

VERSION=$("$LAUNCHER" --version 2>/dev/null || printf 'gpd3303s-control')

if [ "$SHADOWED" = "1" ]; then
  printf '\n%sInstalled %s to %s, but it is not what runs.%s\n' \
    "$YELLOW" "$VERSION" "$LAUNCHER" "$RESET"
  printf 'Remove the shadowing copy named above, then check with:\n\n    gpd3303s --doctor\n\n'
  exit 1
fi

printf '\n%sInstalled %s.%s Start it with:\n\n    %sgpd3303s%s\n\n' \
  "$GREEN" "$VERSION" "$RESET" "$BOLD" "$RESET"
printf 'No hardware handy? Try the built-in simulator:\n\n    gpd3303s --simulate\n\n'
printf 'Something looks wrong? This prints exactly what is installed and where:\n\n    gpd3303s --doctor\n\n'
