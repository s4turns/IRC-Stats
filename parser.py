"""
Eggdrop channel log parser.

Supports formats:
  [HH:MM] <Nick> message
  [HH:MM:SS] <Nick> message
  [DD Mon YYYY HH:MM:SS] <Nick> message
  --- Log opened Fri Feb 21 2026
  --- Day changed Sat Feb 22 2026
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional


@dataclass
class Event:
    timestamp: datetime
    type: str       # message, action, join, part, kick, topic, mode, nick_change
    nick: str
    text: str = ""
    target: str = ""   # kick victim, mode target
    extra: str = ""    # kick reason, mode string, new nick


class EggdropParser:
    # Line timestamp patterns
    RE_TS_FULL = re.compile(
        r'^\[(\d{1,2} \w{3} \d{4} \d{2}:\d{2}:\d{2})\]\s+(.+)$'
    )
    RE_TS_SHORT = re.compile(
        r'^\[(\d{2}:\d{2}(?::\d{2})?)\]\s+(.+)$'
    )

    # Date header patterns
    RE_DATE_OPENED = re.compile(
        r'^-{2,3} Log (?:opened|started|closed)\s+\w+\s+(\w+\s+\d+\s+\d+)', re.IGNORECASE
    )
    RE_DATE_CHANGED = re.compile(
        r'^-{2,3} Day changed\s+\w+\s+(\w+\s+\d+\s+\d+)', re.IGNORECASE
    )

    # Filename date: matches 8 digits after a dot, at end of name or before another dot
    # Handles: lrh.log.01012026  (#ch.20260101.log  etc.)
    RE_FNAME_DATE = re.compile(r'\.(\d{8})(?:\.|$)')

    # Body patterns
    RE_MESSAGE   = re.compile(r'^<([^>]+)>\s+(.+)$')
    RE_ACTION    = re.compile(r'^(?:Action|\* ?):?\s+(\S+)\s+(.+)$')
    RE_JOIN      = re.compile(r'^(\S+) \(([^)]*)\) joined (\S+)\.?$')
    # Part: "nick (~host) left #ch." OR "nick left #ch (reason)."
    RE_PART      = re.compile(r'^(\S+) (?:\([^)]*\) )?left (#\S+)(?:\s*\((.+?)\))?\.?$')
    # Quit: "nick (~host) left irc: reason" OR "nick quit (reason)."
    RE_QUIT      = re.compile(r'^(\S+) (?:\([^)]*\) )?(?:left irc|quit)(?:[: ]+(.+?)\.?)?$')
    # Kick: "nick kicked from #ch by op: reason" OR "nick was kicked from #ch by op (reason)."
    RE_KICK      = re.compile(r'^(\S+) (?:was )?kicked (?:from|off) \S+ by ([^\s:(]+)(?:[: ]+(.+?)\.?)?$')
    RE_TOPIC_SET = re.compile(r'^(\S+) (?:changed|set) (?:the )?topic (?:to|on \S+ to):?\s+(.+)$', re.IGNORECASE)
    # "Topic changed on #ch by nick!user@host: text"  (eggdrop default format)
    # Uses non-greedy \S+? to stop at the first ": " (colon-space) separator,
    # which handles IPv6 hosts that contain colons (e.g. 2a03:5180:f:1::8:1b53).
    RE_TOPIC_BY  = re.compile(r'^Topic changed on \S+ by (\S+?): (.+)$', re.IGNORECASE)
    RE_TOPIC_CHG = re.compile(r'^Topic (?:changed|is now|set by \S+ to):?\s+(.+)$', re.IGNORECASE)
    # Mode: "nick sets mode: ..." (classic eggdrop)
    RE_MODE      = re.compile(r'^(\S+) sets? mode[s]?:? (.+)$', re.IGNORECASE)
    # Mode: "#CHANNEL: mode change 'MODE' by nick!user@host" (this log format)
    RE_MODE2     = re.compile(r'^#[^:]+: mode change \'([^\']+)\' by (\S+)', re.IGNORECASE)
    # Nick change: "Nick change: old -> new" OR "nick changed nick to new"
    RE_NICK      = re.compile(r'^(\S+) (?:changed nick|is now known as|nick change to) (\S+)\.?$', re.IGNORECASE)
    RE_NICK2     = re.compile(r'^Nick change: (\S+) -> (\S+)$', re.IGNORECASE)

    def __init__(self):
        pass

    def _date_from_filename(self, filepath: str) -> Optional[date]:
        name = os.path.basename(filepath)
        m = self.RE_FNAME_DATE.search(name)
        if m:
            raw = m.group(1)
            # Try common 8-digit date formats: MMDDYYYY first (eggdrop default),
            # then YYYYMMDD, then DDMMYYYY as last resort
            for fmt in ('%m%d%Y', '%Y%m%d', '%d%m%Y'):
                try:
                    return datetime.strptime(raw, fmt).date()
                except ValueError:
                    pass
        return None

    def _parse_date_header(self, line: str) -> Optional[date]:
        for pattern in (self.RE_DATE_CHANGED, self.RE_DATE_OPENED):
            m = pattern.match(line)
            if m:
                raw = m.group(1).strip()
                # Try "Feb 21 2026" and "Feb  1 2026" (extra space for single digits)
                for fmt in ('%b %d %Y', '%b  %d %Y'):
                    try:
                        return datetime.strptime(raw, fmt).date()
                    except ValueError:
                        pass
                # Try with day first "21 Feb 2026"
                try:
                    return datetime.strptime(raw, '%d %b %Y').date()
                except ValueError:
                    pass
        return None

    def _parse_body(self, ts: datetime, body: str) -> Optional[Event]:
        body = body.strip()

        # Eggdrop prefixes server events with "*** " — strip it so patterns can match.
        # Keep skipping lines without a space (raw server protocol noise like ***KILL).
        if body.startswith('*** '):
            body = body[4:]
        elif body.startswith('***') or body.startswith('==='):
            return None

        m = self.RE_MESSAGE.match(body)
        if m:
            return Event(ts, 'message', m.group(1), m.group(2))

        m = self.RE_ACTION.match(body)
        if m:
            return Event(ts, 'action', m.group(1), m.group(2))

        m = self.RE_KICK.match(body)
        if m:
            return Event(ts, 'kick', m.group(2), target=m.group(1), extra=m.group(3) or '')

        m = self.RE_TOPIC_SET.match(body)
        if m:
            return Event(ts, 'topic', m.group(1), m.group(2))

        m = self.RE_TOPIC_BY.match(body)
        if m:
            return Event(ts, 'topic', m.group(1).split('!')[0], m.group(2))

        m = self.RE_TOPIC_CHG.match(body)
        if m:
            return Event(ts, 'topic', '', m.group(1))

        m = self.RE_JOIN.match(body)
        if m:
            ident_host = m.group(2)  # e.g. "~user@hostname.example.com"
            if '@' in ident_host:
                ident, host = ident_host.split('@', 1)
                # Strip leading ~ (unverified ident marker) so ~john and john match
                key = ident.lstrip('~') + '@' + host
            else:
                key = ident_host
            return Event(ts, 'join', m.group(1), extra=key.lower())

        m = self.RE_PART.match(body)
        if m:
            return Event(ts, 'part', m.group(1), m.group(3) or '')

        m = self.RE_QUIT.match(body)
        if m:
            return Event(ts, 'part', m.group(1), m.group(2) or '')

        m = self.RE_NICK.match(body)
        if m:
            return Event(ts, 'nick_change', m.group(1), extra=m.group(2))

        m = self.RE_NICK2.match(body)
        if m:
            return Event(ts, 'nick_change', m.group(1), extra=m.group(2))

        m = self.RE_MODE.match(body)
        if m:
            return Event(ts, 'mode', m.group(1), m.group(2))

        # #CHANNEL: mode change 'MODE' by nick!user@host
        m = self.RE_MODE2.match(body)
        if m:
            setter = m.group(2).split('!')[0]  # strip ident@host if present
            return Event(ts, 'mode', setter, m.group(1))

        return None

    def parse_file(self, filepath: str, debug: bool = False) -> list:
        events = []
        current_date = self._date_from_filename(filepath)
        prev_time_minutes = -1  # for rollover heuristic
        unmatched_server = []   # *** lines that didn't match any pattern

        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue

                # Date header?
                d = self._parse_date_header(line)
                if d is not None:
                    current_date = d
                    prev_time_minutes = -1
                    continue

                # Full timestamp in line?
                m = self.RE_TS_FULL.match(line)
                if m:
                    try:
                        ts = datetime.strptime(m.group(1), '%d %b %Y %H:%M:%S')
                        event = self._parse_body(ts, m.group(2))
                        if event:
                            events.append(event)
                    except ValueError:
                        pass
                    continue

                # Short timestamp [HH:MM] or [HH:MM:SS]
                m = self.RE_TS_SHORT.match(line)
                if m:
                    time_str = m.group(1)
                    body = m.group(2)
                    try:
                        fmt = '%H:%M:%S' if len(time_str) == 8 else '%H:%M'
                        t = datetime.strptime(time_str, fmt)
                        cur_minutes = t.hour * 60 + t.minute

                        # Heuristic: if time jumped back more than 6h, new day
                        if prev_time_minutes >= 0 and cur_minutes < prev_time_minutes - 360:
                            if current_date:
                                current_date = current_date + timedelta(days=1)
                        prev_time_minutes = cur_minutes

                        if current_date:
                            ts = datetime(
                                current_date.year, current_date.month, current_date.day,
                                t.hour, t.minute, t.second
                            )
                        else:
                            # No date info at all — use epoch date as placeholder
                            ts = datetime(2000, 1, 1, t.hour, t.minute, t.second)

                        event = self._parse_body(ts, body)
                        if event:
                            events.append(event)
                        elif debug and body.strip().startswith('*** '):
                            unmatched_server.append(body.strip())
                    except ValueError:
                        pass

        if debug and unmatched_server:
            print(f"  [debug] {filepath}: {len(unmatched_server)} unmatched *** lines, first 10:")
            for ln in unmatched_server[:10]:
                print(f"    {ln}")

        return events

    def parse_files(self, filepaths: list, debug: bool = False) -> list:
        all_events = []
        for fp in filepaths:
            all_events.extend(self.parse_file(fp, debug=debug))
        all_events.sort(key=lambda e: e.timestamp)

        # Always print event type breakdown so issues are visible
        from collections import Counter
        counts = Counter(e.type for e in all_events)
        print(f"  Event breakdown: " + ", ".join(
            f"{t}={n}" for t, n in sorted(counts.items())
        ))
        return all_events
