#!/bin/sh
# hw-compat-check installer.
#
# Usage:
#   curl -fsSL https://github.com/virtuozzo/vl10-hw-compat/releases/latest/download/install.sh | sh
#
# Or, to install a specific version:
#   curl -fsSL .../install.sh | HW_COMPAT_CHECK_VERSION=0.1.0 sh
#
# Environment overrides:
#   HW_COMPAT_CHECK_VERSION    Version tag to install. Default: latest.
#   HW_COMPAT_CHECK_REPO       GitHub org/repo. Default: virtuozzo/hw-compat-check.
#   HW_COMPAT_CHECK_ROOT       Install root. Default: /usr/local if writable, else $HOME/.local.

set -e

REPO=${HW_COMPAT_CHECK_REPO:-virtuozzo/vl10-hw-compat}
VERSION=${HW_COMPAT_CHECK_VERSION:-latest}

# ---- pretty printing ----
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_BOLD=$(printf '\033[1m')
    C_DIM=$(printf '\033[2m')
    C_GREEN=$(printf '\033[32m')
    C_YELLOW=$(printf '\033[33m')
    C_RED=$(printf '\033[31m')
    C_RESET=$(printf '\033[0m')
else
    C_BOLD= C_DIM= C_GREEN= C_YELLOW= C_RED= C_RESET=
fi

say()  { printf '%s\n' "$*"; }
info() { printf '%s%s%s %s\n' "$C_BOLD" "::" "$C_RESET" "$*"; }
warn() { printf '%swarning:%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# ---- preflight ----
have() { command -v "$1" >/dev/null 2>&1; }

if have curl; then
    DL='curl -fsSL'
elif have wget; then
    DL='wget -qO-'
else
    die "neither curl nor wget found. Install one and retry."
fi

if ! have python3; then
    die "python3 not found on PATH. hw-compat-check needs Python 3.6 or newer."
fi

py_ok=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,6) else 0)' 2>/dev/null || echo 0)
if [ "$py_ok" != "1" ]; then
    die "python3 is older than 3.6. Please install a newer Python."
fi

have lspci || warn "lspci not found — hw-compat-check needs it to enumerate PCI devices."
have modprobe || warn "modprobe not found — hw-compat-check needs it for driver resolution."

# Pick install root.
if [ -n "${HW_COMPAT_CHECK_ROOT:-}" ]; then
    ROOT=$HW_COMPAT_CHECK_ROOT
elif [ "$(id -u)" = "0" ] || [ -w /usr/local ]; then
    ROOT=/usr/local
else
    ROOT=$HOME/.local
fi

SHARE=$ROOT/share/hw-compat-check
BIN=$ROOT/bin
mkdir -p "$BIN" "$SHARE"

# Resolve download URL.
if [ "$VERSION" = "latest" ]; then
    URL_TARBALL="https://github.com/${REPO}/releases/latest/download/hw-compat-check.tar.gz"
else
    URL_TARBALL="https://github.com/${REPO}/releases/download/${VERSION}/hw-compat-check-${VERSION}.tar.gz"
fi

info "Downloading hw-compat-check (${VERSION}) from ${REPO}"
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

if ! $DL "$URL_TARBALL" > "$tmpdir/hw-compat-check.tar.gz"; then
    die "download failed: $URL_TARBALL"
fi

info "Installing into ${SHARE}"
# Replace contents atomically: stage to tmp, then swap.
stage=$tmpdir/stage
mkdir -p "$stage"
tar -xzf "$tmpdir/hw-compat-check.tar.gz" -C "$stage"

# The tarball contains a single top-level directory (hw-compat-check-VERSION/).
src=$(find "$stage" -mindepth 1 -maxdepth 1 -type d | head -n1)
if [ -z "$src" ]; then
    die "unexpected tarball layout"
fi

rm -rf "$SHARE"
mkdir -p "$(dirname "$SHARE")"
mv "$src" "$SHARE"

ln -sf "$SHARE/bin/hw-compat-check" "$BIN/hw-compat-check"
chmod +x "$SHARE/bin/hw-compat-check"

info "Installed: ${BIN}/hw-compat-check"

case ":$PATH:" in
    *:"$BIN":*) ;;
    *)
        warn "${BIN} is not on PATH."
        say "  Add this to your shell profile:"
        say "      export PATH=\"${BIN}:\$PATH\""
        ;;
esac

say ""
say "${C_GREEN}Run:${C_RESET} ${C_BOLD}hw-compat-check${C_RESET}"
