#!/bin/sh
# Install or update the offline Dyalog documentation bundle, for shells and Dockerfiles.
# It does what the ]Docs user command does, with the same layout: the bundle
# dyalog-docs-<X.Y>.tar.gz, with a sha256sum-format .sha256 beside it, unpacks
# to <root>/<X.Y>, whose bundle.json records the sha256 it was installed from.
#
#   dyalog-docs-update.sh --version 21.0                    per-user copy, from docs.dyalog.com
#   dyalog-docs-update.sh --version 21.0 --system           machine-wide copy
#   dyalog-docs-update.sh --from dyalog-docs-21.0.tar.gz    from a local archive, no network
#
# Exit status: 0 installed or already current, 1 usage, 2 download,
# 3 checksum mismatch, 4 extraction. A failed update leaves the installed copy as it was.
set -eu

usage() {
    cat <<'EOF'
Usage: dyalog-docs-update.sh [--version X.Y] [--root DIR] [--system] [--base-url URL] [--from ARCHIVE]

  --version X.Y    documentation version; required unless --from is given
  --root DIR       install into DIR/<X.Y>
  --system         install the machine-wide copy (macOS /Library/Application Support/Dyalog/docs,
                   otherwise /usr/local/share/dyalog/docs); default is the per-user copy
                   (macOS ~/Library/Application Support/Dyalog/docs,
                   otherwise ${XDG_DATA_HOME:-~/.local/share}/dyalog/docs)
  --base-url URL   download location (default https://docs.dyalog.com/<X.Y>/files)
  --from ARCHIVE   install from a local archive; its .sha256 must be beside it
EOF
}

fail() { # status message
    echo "dyalog-docs-update: $2" >&2
    exit "$1"
}

usage_error() {
    echo "dyalog-docs-update: $1" >&2
    usage >&2
    exit 1
}

version='' root='' system=0 base_url='' from=''
while [ $# -gt 0 ]; do
    case $1 in
        --version|--root|--base-url|--from)
            [ $# -ge 2 ] || usage_error "$1 needs a value"
            case $1 in
                --version) version=$2 ;;
                --root) root=$2 ;;
                --base-url) base_url=$2 ;;
                --from) from=$2 ;;
            esac
            shift 2 ;;
        --system) system=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "unknown argument $1" ;;
    esac
done

if [ -n "$from" ]; then
    [ -f "$from" ] || fail 2 "no archive $from"
    [ -f "$from.sha256" ] || fail 2 "no $(basename "$from").sha256 beside $from to verify it with"
    if [ -z "$version" ]; then
        # The archive holds one top-level directory, dyalog-docs-<X.Y>.
        version=$(tar -tzf "$from" 2>/dev/null | sed -n '1s|^dyalog-docs-\([^/]*\).*|\1|p') || true
        [ -n "$version" ] || fail 4 "$from holds no dyalog-docs-<X.Y> directory"
    fi
fi
[ -n "$version" ] || usage_error "--version is required unless --from is given"
case $version in
    *[!0-9.]*|.*|*.|*.*.*) usage_error "--version must be X.Y, not $version" ;;
    *.*) ;;
    *) usage_error "--version must be X.Y, not $version" ;;
esac

if [ -z "$root" ]; then
    case $(uname -s) in
        Darwin)
            if [ "$system" = 1 ]; then root='/Library/Application Support/Dyalog/docs'
            else root="$HOME/Library/Application Support/Dyalog/docs"; fi ;;
        *)
            if [ "$system" = 1 ]; then root=/usr/local/share/dyalog/docs
            else root="${XDG_DATA_HOME:-$HOME/.local/share}/dyalog/docs"; fi ;;
    esac
fi
[ -n "$base_url" ] || base_url="https://docs.dyalog.com/$version/files"
name="dyalog-docs-$version.tar.gz"
dest="$root/$version"

fetch() { # url file
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$2" "$1" || fail 2 "cannot fetch $1"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$2" "$1" || fail 2 "cannot fetch $1"
    else
        fail 2 "neither curl nor wget is installed"
    fi
}

sha256() { # file
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -c1-64
    elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -c1-64
    else fail 3 "neither sha256sum nor shasum is installed"; fi
}

installed_sha256() { # dir
    [ -f "$1/bundle.json" ] || return 0
    sed -n 's/^ *"sha256": *"\([0-9a-f]*\)".*/\1/p' "$1/bundle.json"
}

mkdir -p "$root" || fail 4 "cannot create $root"
# Everything temporary lives in one directory beside the install, so the final
# rename stays on one file system and a failure leaves nothing behind.
tmp=$(mktemp -d "$root/.$version.update-XXXXXX") || fail 4 "cannot create a temporary directory in $root"
# An interrupted swap puts the previous copy back before the temporary directory goes.
trap '[ -e "$dest" ] || [ ! -e "$tmp/previous" ] || mv "$tmp/previous" "$dest"; rm -rf "$tmp"' EXIT
trap 'exit 130' INT TERM

if [ -n "$from" ]; then
    archive=$from
    sum=$from.sha256
else
    archive=$tmp/$name
    sum=$tmp/$name.sha256
    fetch "$base_url/$name.sha256" "$sum"
fi
want=$(cut -c1-64 "$sum" | head -n 1 | tr 'A-F' 'a-f')
case $want in
    *[!0-9a-f]*|'') fail 3 "$sum holds no sha256" ;;
esac
[ ${#want} -eq 64 ] || fail 3 "$sum holds no sha256"

if [ "$(installed_sha256 "$dest")" = "$want" ]; then
    echo "Documentation $version is up to date"
else
    [ -n "$from" ] || fetch "$base_url/$name" "$archive"
    [ "$(sha256 "$archive")" = "$want" ] || fail 3 "the checksum of $archive does not match $sum"
    mkdir "$tmp/x"
    tar -xzf "$archive" -C "$tmp/x" 2>/dev/null || fail 4 "cannot extract $archive"
    new=$tmp/x/dyalog-docs-$version
    [ -f "$new/index.html" ] || fail 4 "$archive holds no dyalog-docs-$version/index.html"
    [ -f "$new/bundle.json" ] || fail 4 "$archive holds no dyalog-docs-$version/bundle.json"

    # Record the checksum as the last member of bundle.json, written by json.dumps(indent=2).
    awk -v sha="$want" '
        { line[NR] = $0 }
        END {
            last = NR
            while (last > 0 && line[last] !~ /^}[[:space:]]*$/) last--
            if (last < 2) exit 1
            for (i = 1; i < last - 1; i++) print line[i]
            print line[last - 1] ","
            print "  \"sha256\": \"" sha "\""
            for (i = last; i <= NR; i++) print line[i]
        }' "$new/bundle.json" > "$tmp/bundle.json" || fail 4 "cannot read $new/bundle.json"
    mv "$tmp/bundle.json" "$new/bundle.json"

    # Swap: the old copy moves aside, then back if the new one cannot take its place.
    if [ -e "$dest" ]; then
        mv "$dest" "$tmp/previous" || fail 4 "cannot move $dest aside"
    fi
    if ! mv "$new" "$dest"; then
        [ ! -e "$tmp/previous" ] || mv "$tmp/previous" "$dest"
        fail 4 "cannot move the new copy into $dest"
    fi
    built=$(sed -n 's/^ *"build_date": *"\([^"]*\)".*/\1/p' "$dest/bundle.json")
    echo "Installed documentation $version built $built"
fi
echo "  in $dest"
echo "  for F1: DYALOGHELPDIR=file://$dest"
