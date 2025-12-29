#!/usr/bin/env sh
set -eu

echo "Activating feature 'zoxide'"

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must be run as root"
  exit 1
fi

# Arch mapping
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) ARCH="x86_64" ;;
  aarch64|arm64) ARCH="aarch64" ;;
  *)
    echo "Unsupported architecture: $ARCH"
    exit 1
    ;;
esac

# zoxide releases typically ship musl builds usable on Ubuntu/Debian
TARGET="${ARCH}-unknown-linux-musl"

# Get latest tag (e.g. v0.9.6)
TAG="$(curl -fsSL https://api.github.com/repos/ajeetdsouza/zoxide/releases/latest \
  | grep -m1 '"tag_name"' \
  | sed -E 's/.*"([^"]+)".*/\1/')"

# Try both: with and without leading v in filename
VERSION_NO_V="${TAG#v}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

download() {
  url="$1"
  out="$2"
  echo "Downloading: $url"
  curl -fL --retry 3 --retry-delay 1 -o "$out" "$url"
}

# Candidate asset names (some projects include the v in asset filename, others don't)
CANDIDATES="
zoxide-${TAG}-${TARGET}.tar.gz
zoxide-${VERSION_NO_V}-${TARGET}.tar.gz
"

archive=""
for name in $CANDIDATES; do
  url="https://github.com/ajeetdsouza/zoxide/releases/download/${TAG}/${name}"
  if download "$url" "$tmpdir/zoxide.tgz" 2>/dev/null; then
    archive="$tmpdir/zoxide.tgz"
    echo "OK: $name"
    break
  fi
done

if [ -z "$archive" ]; then
  echo "Failed to download zoxide release asset for tag=${TAG} target=${TARGET}"
  echo "Tried:"
  printf '%s\n' $CANDIDATES
  exit 2
fi

# Extract and install
tar -xzf "$archive" -C "$tmpdir"

# Find the binary in extracted contents
bin_path="$(find "$tmpdir" -type f -name zoxide -perm -111 | head -n 1 || true)"
if [ -z "$bin_path" ]; then
  echo "zoxide binary not found in the archive"
  exit 2
fi

install -m 0755 "$bin_path" /usr/local/bin/zoxide
echo "Installed: $(/usr/local/bin/zoxide --version)"

