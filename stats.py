#!/usr/bin/env python3
"""
IRC Stats Generator
Usage: python stats.py [options] logfile [logfile ...]

Example:
    python stats.py /home/ubuntu/eggdrop/logs/*.log -o ./site -n EFnet
"""

import argparse
import glob as glob_mod
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from parser import EggdropParser
from analyzer import Analyzer
from renderer import Renderer


def main():
    ap = argparse.ArgumentParser(
        description='Modern IRC Statistics Generator for eggdrop logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument('logs', nargs='+',
                    help='Log file(s) or glob patterns (quote globs to let Python expand them)')
    ap.add_argument('-o', '--output', default='./site',
                    help='Output directory (default: ./site)')
    ap.add_argument('-c', '--channel', default=None,
                    help='Channel name override (auto-detected from filename)')
    ap.add_argument('-n', '--network', default='IRC',
                    help='Network name (default: IRC)')
    ap.add_argument('--title', default=None,
                    help='Page title override')
    ap.add_argument('--min-lines', type=int, default=5,
                    help='Minimum lines for a user to appear in stats (default: 5)')
    ap.add_argument('--top-users', type=int, default=50,
                    help='Max users in the main table (default: 50)')
    ap.add_argument('--wordcloud-words', type=int, default=200,
                    help='Number of words in the word cloud (default: 200)')
    ap.add_argument('--ignore', nargs='*', default=[], metavar='NICK',
                    help='Nicks to exclude entirely from stats (users, bots, services)')
    ap.add_argument('--bot-nicks', nargs='*', default=[], metavar='NICK',
                    help='Alias for --ignore (kept for compatibility)')
    ap.add_argument('--ignore-pattern', nargs='*', default=[], metavar='REGEX',
                    help='Regex patterns — any nick matching is excluded (e.g. ".*bot$" "^[Ss]erv")')
    ap.add_argument('--ignore-file', default=None, metavar='FILE',
                    help='File with one nick or regex per line to exclude (# comments supported)')
    ap.add_argument('--merge-nicks', nargs='*', default=[],
                    metavar='ALIAS=CANONICAL',
                    help='Exact nick aliases to merge, e.g. Nick_=Nick away_Nick=Nick')
    ap.add_argument('--nick-patterns', nargs='*', default=[],
                    metavar='REGEX=CANONICAL',
                    help='Regex patterns to merge nicks, e.g. "nick[_~|].*=nick" "j.*=john"')
    ap.add_argument('--debug', action='store_true',
                    help='Print unmatched *** lines to help diagnose parsing issues')
    ap.add_argument('--auto-merge', action='store_true', dest='auto_merge',
                    help='Enable auto-detection of common IRC nick variants (Nick_, Nick|away, Nick123, etc.)')
    ap.add_argument('--no-host-merge', action='store_false', dest='host_merge',
                    help='Disable merging nicks that share the same ident@hostname from join events')
    ap.set_defaults(auto_merge=False, host_merge=True)
    args = ap.parse_args()

    # ── Expand globs ───────────────────────────────────────────────────────
    log_files = []
    for pattern in args.logs:
        expanded = glob_mod.glob(pattern)
        if expanded:
            log_files.extend(expanded)
        elif os.path.isfile(pattern):
            log_files.append(pattern)
        else:
            print(f"Warning: no files matched '{pattern}'", file=sys.stderr)

    if not log_files:
        print("Error: no log files found.", file=sys.stderr)
        sys.exit(1)

    log_files = sorted(set(log_files))
    print(f"Found {len(log_files)} log file(s)")

    # ── Nick merges (exact) ────────────────────────────────────────────────
    nick_merges = {}
    for entry in (args.merge_nicks or []):
        if '=' in entry:
            alias, canonical = entry.split('=', 1)
            nick_merges[alias.lower()] = canonical.lower()

    # ── Nick patterns (regex) ──────────────────────────────────────────────
    nick_patterns = []
    for entry in (args.nick_patterns or []):
        if '=' in entry:
            pattern_str, canonical = entry.split('=', 1)
            try:
                compiled = re.compile(pattern_str.lower(), re.IGNORECASE)
                nick_patterns.append((compiled, canonical.lower()))
            except re.error as exc:
                print(f"Warning: invalid regex '{pattern_str}': {exc}", file=sys.stderr)

    # ── Auto-detect channel name ───────────────────────────────────────────
    # Handles: #channel.20260101.log  lrh.log.01012026  #ch.log  etc.
    channel = args.channel
    if not channel:
        for f in log_files:
            name = os.path.basename(f)
            # Strip known date suffixes and extensions to get the base name
            # e.g. "lrh.log.01012026" -> "lrh"
            # e.g. "#channel.20260101.log" -> "#channel"
            parts = name.split('.')
            base = parts[0]
            if base:
                channel = base if base.startswith('#') else f'#{base}'
                break
        channel = channel or '#channel'

    # ── Build ignored nicks set + patterns ────────────────────────────────
    raw_ignores = list(args.ignore or []) + list(args.bot_nicks or [])
    raw_patterns = list(args.ignore_pattern or [])

    if args.ignore_file:
        try:
            with open(args.ignore_file, 'r', encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    # Lines with regex meta chars → pattern; plain nicks → exact
                    if any(c in line for c in r'.*+?[](){}^$|\\'):
                        raw_patterns.append(line)
                    else:
                        raw_ignores.append(line)
        except OSError as exc:
            print(f"Warning: cannot read ignore file: {exc}", file=sys.stderr)

    ignored_nicks = set(n.lower() for n in raw_ignores)

    ignore_patterns = []
    for pat in raw_patterns:
        try:
            ignore_patterns.append(re.compile(pat, re.IGNORECASE))
        except re.error as exc:
            print(f"Warning: invalid ignore pattern '{pat}': {exc}", file=sys.stderr)

    if ignored_nicks:
        print(f"  Ignoring {len(ignored_nicks)} nick(s): {', '.join(sorted(ignored_nicks))}")
    if ignore_patterns:
        print(f"  Ignoring nicks matching {len(ignore_patterns)} pattern(s)")

    # ── Parse ──────────────────────────────────────────────────────────────
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting irc-stats run")
    print("Parsing logs…")
    parser = EggdropParser()
    events = parser.parse_files(log_files, debug=args.debug)
    if not events:
        print("Error: no events could be parsed from the log files.", file=sys.stderr)
        print("  Make sure the files are eggdrop channel logs with [HH:MM] timestamps.", file=sys.stderr)
        sys.exit(1)
    print(f"  Parsed {len(events):,} events")

    # ── Analyze ────────────────────────────────────────────────────────────
    print("Analyzing…")
    analyzer = Analyzer(
        events=events,
        channel=channel,
        network=args.network,
        min_lines=args.min_lines,
        top_users=args.top_users,
        wordcloud_words=args.wordcloud_words,
        ignored_nicks=ignored_nicks,
        ignore_patterns=ignore_patterns,
        nick_merges=nick_merges,
        nick_patterns=nick_patterns,
        auto_merge=args.auto_merge,
        host_merge=args.host_merge,
    )
    data = analyzer.compute()

    total = data['stats']['all']['total_lines']
    users = len(data['users'])
    print(f"  {total:,} lines · {users} users")

    # ── Render ─────────────────────────────────────────────────────────────
    print(f"Rendering site → {args.output}")
    renderer = Renderer(
        data=data,
        output_dir=args.output,
        title=args.title,
        top_users=args.top_users,
    )
    renderer.render()

    out = os.path.abspath(args.output)
    print(f"Done! [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")


if __name__ == '__main__':
    main()
