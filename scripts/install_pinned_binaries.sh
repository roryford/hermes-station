#!/bin/sh
# Install pinned upstream binaries (not in Debian repos at a useful version).
# Reads scripts/pinned-binaries.tsv (sibling file) and installs each tool to
# /usr/local/bin/<name>, verifying SHA256 and supporting amd64+arm64.
set -eu

here="$(dirname "$0")"
tsv="${PINNED_BINARIES_TSV:-${here}/pinned-binaries.tsv}"

arch="$(dpkg --print-architecture)"
case "$arch" in
    amd64|arm64) ;;
    *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac

curl_flags='-fsSL --retry 5 --retry-all-errors --retry-delay 5 --retry-max-time 60'

# Strip comments + blanks to a temp file so the read loop runs in the parent
# shell (a piped `while read` runs in a subshell, swallowing `exit`).
filtered=$(mktemp)
grep -v '^[[:space:]]*#' "$tsv" | grep -v '^[[:space:]]*$' > "$filtered"

while IFS="$(printf '\t')" read -r name version url_amd64 url_arm64 sha_amd64 sha_arm64 archive_kind inner_amd64 inner_arm64; do
    case "$arch" in
        amd64) url="$url_amd64"; sha="$sha_amd64"; inner="$inner_amd64" ;;
        arm64) url="$url_arm64"; sha="$sha_arm64"; inner="$inner_arm64" ;;
    esac

    # Substitute ${VERSION} in url + inner path.
    url=$(printf '%s' "$url" | sed "s|\${VERSION}|${version}|g")
    inner=$(printf '%s' "$inner" | sed "s|\${VERSION}|${version}|g")

    tmpdir=$(mktemp -d)
    case "$archive_kind" in
        raw)
            # shellcheck disable=SC2086
            curl $curl_flags -o "$tmpdir/$name" "$url"
            echo "${sha}  $tmpdir/$name" | sha256sum -c -
            install -m 0755 "$tmpdir/$name" "/usr/local/bin/$name"
            ;;
        tgz)
            # shellcheck disable=SC2086
            curl $curl_flags -o "$tmpdir/archive.tgz" "$url"
            echo "${sha}  $tmpdir/archive.tgz" | sha256sum -c -
            tar -xzf "$tmpdir/archive.tgz" -C "$tmpdir" "$inner"
            install -m 0755 "$tmpdir/$inner" "/usr/local/bin/$name"
            ;;
        txz)
            # shellcheck disable=SC2086
            curl $curl_flags -o "$tmpdir/archive.tar.xz" "$url"
            echo "${sha}  $tmpdir/archive.tar.xz" | sha256sum -c -
            tar -xJf "$tmpdir/archive.tar.xz" -C "$tmpdir" "$inner"
            install -m 0755 "$tmpdir/$inner" "/usr/local/bin/$name"
            ;;
        *)
            echo "unknown archive_kind '$archive_kind' for $name" >&2
            exit 1
            ;;
    esac
    rm -rf "$tmpdir"
    echo "installed: $name $version -> /usr/local/bin/$name"
done < "$filtered"

rm -f "$filtered"
