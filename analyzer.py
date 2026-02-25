"""
IRC statistics analyzer.
Consumes parsed events and produces a rich stats dictionary.
"""

import re
import random
from collections import defaultdict, Counter
from datetime import datetime, timedelta


# ─── Stop words ───────────────────────────────────────────────────────────────

STOP_WORDS = {
    # English
    'a','an','the','and','or','but','in','on','at','to','for','of','with',
    'by','from','as','is','was','are','were','be','been','being','have','has',
    'had','do','does','did','will','would','could','should','may','might',
    'shall','can','not','no','nor','so','yet','both','either','neither',
    'only','own','same','than','too','very','just','into','through','about',
    'up','down','out','off','over','under','again','then','once','here',
    'there','when','where','why','how','all','each','few','more','most',
    'other','some','such','if','its','get','got','like','also',
    'i','me','my','myself','we','our','ours','ourselves',
    'you','your','yours','yourself','yourselves',
    'he','him','his','himself','she','her','hers','herself',
    'it','itself','they','them','their','theirs','themselves',
    'what','which','who','whom','this','that','these','those',
    'am','s','t','re','ve','ll','d',
    # Contractions
    "im","ive","id","ill","youre","youve","youll","youd",
    "hes","shes","its","were","theyre","theyve","theyll",
    "dont","doesnt","didnt","wont","wouldnt","couldnt","shouldnt",
    "isnt","arent","wasnt","werent","hasnt","havent","hadnt",
    "cant","cannot","gonna","gotta","wanna","kinda","sorta",
    # IRC filler
    'ok','okay','yeah','yep','nope','hey','hi','hm','hmm','um','uh',
    'ah','oh','lol','haha','heh','lmao','rofl','omg','wtf','idk',
    'tbh','imo','imho','afk','brb','irl','atm','fyi','iirc','iiuc',
    'np','ty','thx','plz','pls','omfg','lmfao','kk','rly','gtg',
    # Common short words that add no value
    'get','got','yes','no','so','do','did','go','see','use','used',
    'just','now','new','old','two','one','any','way','say','said',
    'know','think','like','well','even','make','need','want',
    'come','look','back','still','good','time','last','first',
    'year','years','day','days','thing','things','people','man',
    'much','many','too','can','only','able',
    # URL fragments (from links shared in chat)
    'http','https','www','com','net','org','edu','gov','io',
    'cdn','url','gif','jpg','jpeg','png','mp4','html','php',
    # IRC client / network artifacts
    'irccloud','freenode','libera','efnet','quakenet','undernet',
    'twitch','youtube','discord','reddit','twitter','instagram',
}

# ─── Regexes ──────────────────────────────────────────────────────────────────

RE_URL     = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
RE_SMILEY  = re.compile(
    r'[:;=8]-?[)(DPdpOoS3\[\]{}|\\/@]'
    r'|[)(DPdO][-:;=]'
    r'|\^[._]?\^|>_<|:-?/|:-?\||>.<'
    r'|\bxd+\b|\bxdd+\b|\bhaha\b|\blmao\b|\brofl\b|\bkek\b',
    re.IGNORECASE
)
RE_WORD    = re.compile(r"[a-zA-Z']{3,}")
RE_NICK_IN = re.compile(r'\b(\w+)\b')     # for mention detection


def _safe_nick(nick: str) -> str:
    return ''.join(c if (c.isalnum() or c in '-_') else '_' for c in nick)


def _count_mode_b(mode_str: str) -> int:
    """Count +b (ban) flags in a mode string, e.g. '+bb mask1 mask2' → 2."""
    parts = mode_str.split()
    if not parts:
        return 0
    count = 0
    adding = False
    for ch in parts[0]:
        if ch == '+':
            adding = True
        elif ch == '-':
            adding = False
        elif ch == 'b' and adding:
            count += 1
    return count


_RE_IRCCLOUD_IP = re.compile(r'/ip\.\d+\.\d+\.\d+\.\d+$')
_RE_HAS_DIGIT   = re.compile(r'\d')


def _normalize_host(host: str) -> str:
    """
    Normalize a hostname for ident@host grouping so that users with dynamic IPs
    from the same ISP or gateway get merged.

    Rules:
    - Strip irccloud per-session IP suffix: /ip.1.2.3.4
    - If the first hostname segment contains a digit (dynamic pool hostname),
      collapse to the last 2 segments (registered domain).
      e.g. pool-12-34.isp.net -> isp.net
           dhcp-1.2.isp.example.com -> example.com
    - Raw IPv4 addresses (all-numeric segments) are kept as-is.
    - Hostnames with no digits in the first segment are kept as-is.
    """
    host = _RE_IRCCLOUD_IP.sub('', host)
    parts = host.split('.')
    if len(parts) >= 3:
        # Don't collapse raw IPv4 addresses
        if not all(p.isdigit() for p in parts):
            if _RE_HAS_DIGIT.search(parts[0]):
                return '.'.join(parts[-2:])
    return host


def _is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 5:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) > 0.5


def _word_tokens(text: str) -> list:
    # Strip URLs first so "https://..." doesn't contribute fragments
    text = RE_URL.sub('', text)
    tokens = []
    for w in RE_WORD.findall(text):
        # Normalise: lowercase, remove apostrophes so "i'm"→"im", "don't"→"dont"
        clean = w.lower().replace("'", "")
        if len(clean) >= 3 and clean not in STOP_WORDS and not clean.isnumeric():
            tokens.append(clean)
    return tokens


# ─── Analyzer ─────────────────────────────────────────────────────────────────

class Analyzer:
    def __init__(self, events, channel, network='IRC', min_lines=5,
                 top_users=50, wordcloud_words=200,
                 ignored_nicks=None, ignore_patterns=None,
                 # legacy param name kept for direct API callers
                 bot_nicks=None,
                 nick_merges=None, nick_patterns=None, auto_merge=True,
                 host_merge=True):
        self.events          = events
        self.channel         = channel
        self.network         = network
        self.min_lines       = min_lines
        self.top_users       = top_users
        self.wc_words        = wordcloud_words
        # Combine legacy bot_nicks with new ignored_nicks
        self.ignored_nicks   = (ignored_nicks or set()) | (bot_nicks or set())
        self.ignore_patterns = ignore_patterns or []   # list of compiled regexes
        self.nick_merges     = nick_merges or {}       # alias_lower -> canonical_lower
        self.nick_patterns   = nick_patterns or []     # list of (compiled_regex, canonical_lower)
        self.auto_merge      = auto_merge
        self.host_merge      = host_merge
        # Built at compute() time
        self._auto_merge_map: dict = {}
        self._host_merge_map: dict = {}

    def _is_ignored(self, nick: str) -> bool:
        """Return True if this nick should be excluded from all stats."""
        if nick in self.ignored_nicks:
            return True
        return any(p.search(nick) for p in self.ignore_patterns)

    # ── IRC suffix patterns for auto-merge ────────────────────────────────────
    # Strip these from the end of a nick to find the "base" nick
    _AUTO_STRIP = re.compile(
        r'[_~^`|\\].*$'      # trailing _ ~ ^ ` | \ and anything after
        r'|\|[a-z]+$'        # |away |afk |home etc.
        r'|\[[^\]]*\]$'      # [away] [afk] suffix
        r'|\d+$',            # trailing digits
        re.IGNORECASE,
    )

    def _auto_base(self, nick: str) -> str:
        """Return the base nick after stripping common IRC away/alt suffixes."""
        return self._AUTO_STRIP.sub('', nick).lower().strip()

    def _build_auto_merge_map(self, raw_counts: Counter) -> None:
        """
        Group nicks by their stripped base, pick the variant with the most
        messages as the canonical nick for each group.
        """
        # Group by base nick
        base_to_nicks: dict = defaultdict(set)
        for raw_nick in raw_counts:
            base = self._auto_base(raw_nick)
            if base:
                base_to_nicks[base].add(raw_nick)

        # For each group with >1 member, pick canonical = highest message count
        self._auto_merge_map = {}
        for base, nicks in base_to_nicks.items():
            # Skip bases that are too short to be unambiguous (e.g. "l" from "l23456")
            if len(base) < 3:
                continue
            # Drop ignored nicks from the group entirely
            nicks = {n for n in nicks if not self._is_ignored(n)}
            if len(nicks) < 2:
                continue
            canonical = max(nicks, key=lambda n: raw_counts[n])
            for alias in nicks:
                if alias != canonical:
                    self._auto_merge_map[alias] = canonical

    def _build_host_merge_map(self, raw_counts: Counter) -> None:
        """
        Group nicks that share the same ident@hostname from join events.
        Hostnames are normalized to strip dynamic IP prefixes (e.g. ISP pool
        hostnames, irccloud /ip.x.x.x.x suffixes) so that users whose ISP
        reassigns their IP still get grouped.

        irccloud UIDs (ident=uid\d+) are propagated through nick-change events
        so that in-session nick changes (which produce no join event) are still
        grouped correctly.  Conflict resolution: if a destination nick already
        has its own direct irccloud UID from a join event, any inherited UID
        that differs is discarded — preventing false merges when two different
        people happen to use the same nick at different times.
        """
        _RE_IRCCLOUD_UID = re.compile(r'^uid\d+$', re.IGNORECASE)

        # Phase 1: collect keys from join events
        nick_to_keys: dict = defaultdict(set)
        for ev in self.events:
            if ev.type == 'join' and ev.extra and ev.extra != '*':
                raw_key = ev.extra  # "ident@host" (already normalized by parser)
                if '@' in raw_key:
                    ident, host = raw_key.split('@', 1)
                    key = ident + '@' + _normalize_host(host)
                else:
                    key = raw_key
                nick_to_keys[ev.nick.lower()].add(key)

        # Snapshot of nicks that have any direct join event (before propagation).
        # Any nick in this set is its own account and should not absorb foreign UIDs.
        nicks_with_direct_joins: set = set(nick_to_keys.keys())

        # Record which irccloud UID keys each nick has from direct join events.
        direct_uid_keys: dict = {}  # nick -> set of uid idents (lowercase)
        for nick, keys in nick_to_keys.items():
            uids = {k.split('@')[0].lower() for k in keys
                    if '@' in k and _RE_IRCCLOUD_UID.match(k.split('@')[0])}
            if uids:
                direct_uid_keys[nick] = uids

        # Phase 2: propagate irccloud UIDs through nick-change events.
        # When A → B and A has an irccloud UID key, B inherits it so that
        # in-session nick changes are grouped with the original account.
        for ev in self.events:
            if ev.type == 'nick_change' and ev.extra:
                src, dst = ev.nick.lower(), ev.extra.lower()
                if self._is_ignored(src) or self._is_ignored(dst):
                    continue
                for key in nick_to_keys.get(src, set()):
                    if '@' in key and _RE_IRCCLOUD_UID.match(key.split('@')[0]):
                        nick_to_keys[dst].add(key)

        # Phase 3: conflict resolution.
        # Any nick that had a direct join event is its own account — remove any
        # irccloud UID keys it inherited via nick-change propagation that don't
        # belong to it.  This covers two cases:
        #   • Non-irccloud users (e.g. g, nme/grim): own_uids is empty, so ALL
        #     inherited UID keys are stripped.
        #   • irccloud users with a different UID (e.g. shimmer with uid716804
        #     inheriting uid734328): only the foreign UID is stripped.
        for nick in nicks_with_direct_joins:
            keys = nick_to_keys.get(nick)
            if not keys:
                continue
            own_uids = direct_uid_keys.get(nick, set())
            to_remove = {k for k in keys
                         if '@' in k
                         and _RE_IRCCLOUD_UID.match(k.split('@')[0])
                         and k.split('@')[0].lower() not in own_uids}
            if to_remove:
                keys -= to_remove

        # Invert: normalized_key -> set of nicks
        key_to_nicks: dict = defaultdict(set)
        for nick, keys in nick_to_keys.items():
            for key in keys:
                key_to_nicks[key].add(nick)

        # Build merge map: for each group >1 nick, merge to most-active
        self._host_merge_map = {}
        for key, nicks in key_to_nicks.items():
            # Drop ignored nicks from the group entirely
            nicks = {n for n in nicks if not self._is_ignored(n)}
            if len(nicks) < 2:
                continue
            canonical = max(nicks, key=lambda n: raw_counts.get(n, 0))
            for alias in nicks:
                if alias != canonical:
                    self._host_merge_map[alias] = canonical

        if self._host_merge_map:
            print(f"  Host-merge: {len(self._host_merge_map)} nick alias(es) grouped by ident@host")

    def _resolve(self, nick: str) -> str:
        """
        Return the canonical lowercase nick after applying (in order):
          1. Exact nick_merges dict
          2. Regex nick_patterns (first match wins)
          3. Auto-merge map (if --auto-merge)
          4. Host-merge map (if --host-merge)
        """
        low = nick.lower()
        # 1. Exact merge
        if low in self.nick_merges:
            return self.nick_merges[low]
        # 2. Regex patterns
        for pattern, canonical in self.nick_patterns:
            if pattern.fullmatch(low):
                return canonical
        # 3. Auto-merge
        if self._auto_merge_map and low in self._auto_merge_map:
            return self._auto_merge_map[low]
        # 4. Host-merge
        if self._host_merge_map and low in self._host_merge_map:
            return self._host_merge_map[low]
        return low

    def compute(self) -> dict:
        if not self.events:
            raise ValueError("No events found in log files.")

        # ── Pre-pass: count raw messages (shared by all merge strategies) ───
        raw_counts: Counter = Counter()
        if self.auto_merge or self.host_merge:
            for ev in self.events:
                if ev.type == 'message' and not self._is_ignored(ev.nick.lower()):
                    raw_counts[ev.nick.lower()] += 1

        # ── Build merge maps before any resolution ─────────────────────────
        if self.auto_merge:
            self._build_auto_merge_map(raw_counts)
            if self._auto_merge_map:
                print(f"  Auto-merge: {len(self._auto_merge_map)} nick alias(es) grouped by suffix")
        if self.host_merge:
            self._build_host_merge_map(raw_counts)

        # ── First pass: collect all nicks seen in messages ─────────────────
        # (used for mention detection — ignored nicks excluded)
        all_nicks_lower: set = set()
        for ev in self.events:
            if ev.type in ('message', 'action', 'join', 'part', 'kick'):
                orig = ev.nick.lower()
                resolved = self._resolve(orig)
                if not self._is_ignored(resolved) and not self._is_ignored(orig):
                    all_nicks_lower.add(resolved)

        # Pre-compute stripped base for each nick (for mention matching nicks
        # with trailing IRC special chars like mits` → mits). None if same as nick.
        # Also exclude nicks that are common English words to avoid false positives
        # (e.g. nick "lol", "you", "dude" matching every message containing that word).
        _irc_suffix = re.compile(r'[`\-\[\]\\^{}|_]+$')
        _nick_base_cache: dict = {}
        _mentionable_nicks: set = set()
        for n in all_nicks_lower:
            base = _irc_suffix.sub('', n)
            _nick_base_cache[n] = base if base != n else None
            # Skip nicks (and their bases) that are stop words — they'd match
            # every ordinary sentence containing that word
            nick_check = base if base != n else n
            if nick_check not in STOP_WORDS and n not in STOP_WORDS:
                _mentionable_nicks.add(n)

        # ── Second pass: build per-user raw data ───────────────────────────
        # Keyed by resolved canonical nick (lowercase)
        user_data: dict = defaultdict(lambda: {
            'lines': 0, 'words': 0, 'chars': 0,
            'actions': 0, 'joins': 0, 'parts': 0,
            'kicks_given': 0, 'kicks_received': 0, 'bans_given': 0,
            'slaps_given': 0, 'slaps_received': 0,
            'questions': 0, 'caps_lines': 0, 'urls': 0, 'smileys': 0,
            'longest_line': '',
            'first_seen': None, 'last_seen': None,
            'hourly': [0]*24, 'weekday': [0]*7,
            'word_freq': Counter(),
            'mentions_given': Counter(),
            'mentions_received': Counter(),
            'quotes': [],
            'daily_lines': Counter(),
            'nicks_seen': set(),
            'display_nick': '',
        })

        # Channel-wide aggregates
        ch_hourly    = [0]*24
        ch_weekday   = [0]*7
        ch_daily     = Counter()          # date_str -> total lines
        ch_word_freq = Counter()
        ch_topics    = []
        mention_matrix = Counter()        # (from, to) -> count

        for ev in self.events:
            orig_nick = ev.nick.lower()
            nick = self._resolve(orig_nick)
            if self._is_ignored(nick) or self._is_ignored(orig_nick):
                continue

            u = user_data[nick]
            u['nicks_seen'].add(ev.nick)
            if not u['display_nick']:
                u['display_nick'] = ev.nick

            ts = ev.timestamp
            date_str = ts.strftime('%Y-%m-%d')
            hour = ts.hour
            wday = ts.weekday()   # 0=Mon

            if u['first_seen'] is None or ts < u['first_seen']:
                u['first_seen'] = ts
            if u['last_seen'] is None or ts > u['last_seen']:
                u['last_seen'] = ts
            # keep most-recent display nick
            if u['last_seen'] == ts:
                u['display_nick'] = ev.nick

            if ev.type == 'message':
                text = ev.text
                word_count = len(text.split())
                u['lines']    += 1
                u['words']    += word_count
                u['chars']    += len(text)
                u['hourly'][hour] += 1
                u['weekday'][wday] += 1
                u['daily_lines'][date_str] += 1

                ch_hourly[hour]  += 1
                ch_weekday[wday] += 1
                ch_daily[date_str] += 1

                if text.rstrip().endswith('?'):
                    u['questions'] += 1
                if _is_caps(text):
                    u['caps_lines'] += 1
                if RE_URL.search(text):
                    u['urls'] += 1
                if RE_SMILEY.search(text):
                    u['smileys'] += 1
                if len(text) > len(u['longest_line']):
                    u['longest_line'] = text

                # Words
                tokens = _word_tokens(text)
                u['word_freq'].update(tokens)
                ch_word_freq.update(tokens)

                # Mentions
                words_in_msg = {w.lower() for w in RE_NICK_IN.findall(text)}
                for other_nick in _mentionable_nicks:
                    if other_nick != nick and (other_nick in words_in_msg or
                            _nick_base_cache.get(other_nick) in words_in_msg):
                        u['mentions_given'][other_nick] += 1
                        user_data[other_nick]['mentions_received'][nick] += 1
                        mention_matrix[(nick, other_nick)] += 1

                # Random quotes reservoir (keep up to 20, sample 5 later)
                if len(u['quotes']) < 20:
                    u['quotes'].append(text)
                else:
                    idx = random.randint(0, u['lines'])
                    if idx < 20:
                        u['quotes'][idx] = text

            elif ev.type == 'action':
                u['actions'] += 1
                u['hourly'][hour] += 1
                u['weekday'][wday] += 1
                # Slap detection: "/me slaps Nick ..."
                if ev.text.lower().startswith('slap'):
                    u['slaps_given'] += 1
                    words = ev.text.split()
                    if len(words) >= 2:
                        target = self._resolve(words[1].lower())
                        if not self._is_ignored(target) and target != nick:
                            user_data[target]['slaps_received'] += 1
                u['daily_lines'][date_str] += 1
                ch_daily[date_str] += 1

            elif ev.type == 'join':
                u['joins'] += 1

            elif ev.type == 'part':
                u['parts'] += 1

            elif ev.type == 'kick':
                u['kicks_given'] += 1
                target = self._resolve(ev.target)
                user_data[target]['kicks_received'] += 1

            elif ev.type == 'mode':
                bans = _count_mode_b(ev.text)
                if bans:
                    u['bans_given'] += bans

            elif ev.type == 'topic':
                ch_topics.append({
                    'text': ev.text[:300],
                    'nick': ev.nick,
                    'timestamp': ts.strftime('%Y-%m-%d %H:%M'),
                })

            elif ev.type == 'nick_change':
                new_nick = self._resolve(ev.extra)
                user_data[new_nick]['nicks_seen'].add(ev.extra)

        # ── Filter users by min_lines ──────────────────────────────────────
        qualified = {
            n: d for n, d in user_data.items()
            if d['lines'] >= self.min_lines and not self._is_ignored(n)
        }

        # ── Build full user dict ───────────────────────────────────────────
        users_out = {}
        for nick, d in qualified.items():
            lines = d['lines']
            avg_wpl = round(d['words'] / lines, 2) if lines else 0
            avg_cpl = round(d['chars'] / lines, 1) if lines else 0
            q_pct   = round(d['questions'] / lines * 100, 1) if lines else 0
            caps_pct= round(d['caps_lines'] / lines * 100, 1) if lines else 0
            sm_pct  = round(d['smileys'] / lines * 100, 1) if lines else 0
            most_active_hour = d['hourly'].index(max(d['hourly'])) if any(d['hourly']) else 0

            active_days = len(d['daily_lines'])

            users_out[nick] = {
                'nick': d['display_nick'] or nick,
                'lines': lines,
                'words': d['words'],
                'chars': d['chars'],
                'actions': d['actions'],
                'joins': d['joins'],
                'parts': d['parts'],
                'kicks_given': d['kicks_given'],
                'kicks_received': d['kicks_received'],
                'bans_given': d['bans_given'],
                'slaps_given': d['slaps_given'],
                'slaps_received': d['slaps_received'],
                'questions': q_pct,
                'caps': caps_pct,
                'urls': d['urls'],
                'smileys': sm_pct,
                'avg_wpl': avg_wpl,
                'avg_cpl': avg_cpl,
                'longest_line': d['longest_line'][:500],
                'first_seen': d['first_seen'].strftime('%Y-%m-%d') if d['first_seen'] else '',
                'last_seen': d['last_seen'].strftime('%Y-%m-%d') if d['last_seen'] else '',
                'most_active_hour': most_active_hour,
                'hourly': d['hourly'],
                'weekday': d['weekday'],
                'top_words': d['word_freq'].most_common(50),
                'mentions_given': dict(d['mentions_given'].most_common(10)),
                'mentions_received': dict(d['mentions_received'].most_common(10)),
                'quotes': random.sample(d['quotes'], min(5, len(d['quotes']))),
                'active_days': active_days,
                'daily_lines': dict(d['daily_lines']),
                'nicks_used': sorted(d['nicks_seen']),
                'safe_nick': _safe_nick(d['display_nick'] or nick),
            }

        # ── Compute channel periods ────────────────────────────────────────
        end_date   = max(self.events, key=lambda e: e.timestamp).timestamp
        start_date = min(self.events, key=lambda e: e.timestamp).timestamp

        periods = {}
        period_defs = {
            'all': None,
            '1y':  365,
            '90d': 90,
            '30d': 30,
        }
        for key, days in period_defs.items():
            if days:
                cutoff = end_date - timedelta(days=days)
            else:
                cutoff = None
            periods[key] = self._compute_period(
                qualified, user_data, ch_daily, ch_hourly, ch_weekday,
                ch_word_freq, mention_matrix, ch_topics,
                start_date, end_date, cutoff,
            )

        return {
            'channel': self.channel,
            'network': self.network,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'stats': periods,
            'users': users_out,
        }

    def _compute_period(self, qualified, user_data, ch_daily, ch_hourly,
                        ch_weekday, ch_word_freq, mention_matrix, ch_topics,
                        start_date, end_date, cutoff):
        """Build the stats dict for a given time window."""

        if cutoff is None:
            # All-time stats already fully aggregated
            p_daily    = ch_daily
            p_hourly   = ch_hourly
            p_weekday  = ch_weekday
            p_word_freq = ch_word_freq
            p_start    = start_date
        else:
            # Re-aggregate from daily_lines per user for the period
            p_daily   = Counter()
            p_hourly  = [0]*24
            p_weekday = [0]*7
            p_word_freq = Counter()  # no word breakdown by day, use all-time approx
            p_start   = cutoff

            for date_str, count in ch_daily.items():
                try:
                    d = datetime.strptime(date_str, '%Y-%m-%d')
                except ValueError:
                    continue
                if d >= cutoff:
                    p_daily[date_str] += count

            # Hourly/weekday: we can't easily filter by date from aggregated data
            # Use all-time as approximation (note this in the UI)
            # For a proper implementation, per-event data would be needed
            p_hourly  = ch_hourly
            p_weekday = ch_weekday
            p_word_freq = ch_word_freq

        total_lines = sum(p_daily.values())
        active_days = len(p_daily)

        # Per-user stats for this period
        # _canon tracks canonical nick alongside each row so we can build top_qualified
        user_rows = []
        _canon = []
        for nick, d in qualified.items():
            if cutoff is None:
                lines = d['lines']
            else:
                lines = sum(
                    cnt for ds, cnt in d['daily_lines'].items()
                    if self._date_in_period(ds, cutoff)
                )
            if lines == 0:
                continue
            u = user_data[nick]
            pct = round(lines / total_lines * 100, 2) if total_lines else 0

            u_lines_all = u['lines'] or 1  # all-time lines for pct calc
            avg_wpl     = round(u['words'] / u_lines_all, 2) if u['lines'] else 0
            q_pct       = round(u['questions'] / u_lines_all * 100, 1)
            caps_pct    = round(u['caps_lines'] / u_lines_all * 100, 1)
            sm_pct      = round(u['smileys'] / u_lines_all * 100, 1)
            disp_nick   = u['display_nick'] or nick

            user_rows.append({
                'nick': disp_nick,
                'safe_nick': _safe_nick(disp_nick),
                'lines': lines,
                'pct': pct,
                'words': u['words'],
                'avg_wpl': avg_wpl,
                'actions': u['actions'],
                'questions': q_pct,
                'caps': caps_pct,
                'urls': u['urls'],
                'kicks_given': u['kicks_given'],
                'kicks_received': u['kicks_received'],
                'bans_given': u['bans_given'],
                'slaps_given': u['slaps_given'],
                'slaps_received': u['slaps_received'],
                'avg_cpl': round(u['chars'] / u_lines_all, 1) if u['lines'] else 0,
                'smileys': sm_pct,
                'active_days': len([ds for ds in u['daily_lines']
                                    if cutoff is None or self._date_in_period(ds, cutoff)]),
                'most_active_hour': u['hourly'].index(max(u['hourly'])) if any(u['hourly']) else 0,
                'last_seen': u['last_seen'].strftime('%Y-%m-%d') if u['last_seen'] else '',
                'first_seen': u['first_seen'].strftime('%Y-%m-%d') if u['first_seen'] else '',
            })
            _canon.append(nick)

        # Sort and trim to top N
        order = sorted(range(len(user_rows)), key=lambda i: user_rows[i]['lines'], reverse=True)
        user_rows = [user_rows[i] for i in order[:self.top_users]]
        top_canon_nicks = {_canon[i] for i in order[:self.top_users]}

        # Awards only consider users who made the top N
        top_qualified = {n: d for n, d in qualified.items() if n in top_canon_nicks}

        # Most active day
        mad = max(p_daily.items(), key=lambda x: x[1]) if p_daily else ('', 0)

        # Awards
        awards = self._compute_awards(top_qualified, user_data, user_rows, cutoff)

        # Word cloud
        top_words = p_word_freq.most_common(self.wc_words)

        # Mention network (all-time; top 20 nodes)
        network = self._build_network(qualified, user_data, mention_matrix, n=20)

        # Topics (last 20, reversed)
        topics = list(reversed(ch_topics[-20:]))

        return {
            'start': p_start.strftime('%Y-%m-%d') if hasattr(p_start, 'strftime') else str(p_start),
            'end': end_date.strftime('%Y-%m-%d'),
            'total_lines': total_lines,
            'total_words': sum(u['words'] for u in user_rows),
            'unique_nicks': len(user_rows),
            'active_days': active_days,
            'most_active_day': {'date': mad[0], 'lines': mad[1]},
            'hourly': p_hourly,
            'weekday': p_weekday,
            'users': user_rows,
            'awards': awards,
            'word_cloud': top_words,
            'topics': topics,
            'mention_network': network,
            'daily_activity': dict(p_daily),
        }

    def _date_in_period(self, date_str: str, cutoff: datetime) -> bool:
        try:
            d = datetime.strptime(date_str, '%Y-%m-%d')
            return d >= cutoff
        except ValueError:
            return False

    def _compute_awards(self, qualified, user_data, user_rows, cutoff) -> dict:
        awards = {}
        if not user_rows:
            return awards

        def best(key, reverse=True):
            filtered = [u for u in user_rows if u.get(key, 0) > 0]
            if not filtered:
                return None
            row = max(filtered, key=lambda x: x.get(key, 0)) if reverse else min(filtered, key=lambda x: x.get(key, 0))
            return {'nick': row['nick'], 'value': row.get(key, 0)}

        # From full user data
        def best_full(key, rows, reverse=True):
            q_rows = [(n, user_data[n]) for n in qualified if user_data[n].get(key, 0) > 0]
            if not q_rows:
                return None
            if reverse:
                n, d = max(q_rows, key=lambda x: x[1].get(key, 0))
            else:
                n, d = min(q_rows, key=lambda x: x[1].get(key, 0))
            disp = user_data[n].get('display_nick') or n
            return {'nick': disp, 'value': d.get(key, 0)}

        # Most questions (% of lines)
        q_rows = [(u['nick'], u['lines']) for u in user_rows]
        q_data = [
            (n, user_data[n.lower()].get('questions', 0) if n.lower() in user_data else 0, l)
            for n, l in q_rows
        ]
        # Use pre-computed from qualified
        def pct_award(field_raw, field_pct, min_lines=20):
            """Best user by a percentage field."""
            cands = []
            for n, d in qualified.items():
                if d['lines'] >= min_lines:
                    pct = d[field_raw] / d['lines'] * 100 if d['lines'] else 0
                    cands.append((d.get('display_nick') or n, pct, d['lines']))
            if not cands:
                return None
            best_n, best_pct, best_l = max(cands, key=lambda x: x[1])
            return {'nick': best_n, 'value': round(best_pct, 1)}

        awards['most_questions']  = pct_award('questions', 'questions_pct', 20)
        awards['most_caps']       = pct_award('caps_lines', 'caps_pct', 20)
        awards['most_smileys']    = pct_award('smileys', 'smileys_pct', 20)

        # Night owl: most msgs between 00:00–05:59
        night_cands = []
        for n, d in qualified.items():
            night = sum(d['hourly'][:6])
            total = d['lines']
            if total >= 20 and night > 0:
                night_cands.append((d.get('display_nick') or n, night / total * 100))
        if night_cands:
            best_n, best_v = max(night_cands, key=lambda x: x[1])
            awards['night_owl'] = {'nick': best_n, 'value': round(best_v, 1)}

        # Morning bird: most msgs 06:00–11:59
        morn_cands = []
        for n, d in qualified.items():
            morn = sum(d['hourly'][6:12])
            total = d['lines']
            if total >= 20 and morn > 0:
                morn_cands.append((d.get('display_nick') or n, morn / total * 100))
        if morn_cands:
            best_n, best_v = max(morn_cands, key=lambda x: x[1])
            awards['morning_bird'] = {'nick': best_n, 'value': round(best_v, 1)}

        # Most actions
        ac_cands = [(d.get('display_nick') or n, d['actions']) for n, d in qualified.items() if d['actions'] > 0]
        if ac_cands:
            best_n, best_v = max(ac_cands, key=lambda x: x[1])
            awards['most_actions'] = {'nick': best_n, 'value': best_v}

        # Most URLs
        url_cands = [(d.get('display_nick') or n, d['urls']) for n, d in qualified.items() if d['urls'] > 0]
        if url_cands:
            best_n, best_v = max(url_cands, key=lambda x: x[1])
            awards['most_urls'] = {'nick': best_n, 'value': best_v}

        # Most kicks given / received
        kg_cands = [(d.get('display_nick') or n, d['kicks_given']) for n, d in qualified.items() if d['kicks_given'] > 0]
        if kg_cands:
            best_n, best_v = max(kg_cands, key=lambda x: x[1])
            awards['most_kicks_given'] = {'nick': best_n, 'value': best_v}

        bg_cands = [(d.get('display_nick') or n, d['bans_given']) for n, d in qualified.items() if d['bans_given'] > 0]
        if bg_cands:
            best_n, best_v = max(bg_cands, key=lambda x: x[1])
            awards['most_bans_given'] = {'nick': best_n, 'value': best_v}

        kr_cands = [(d.get('display_nick') or n, d['kicks_received']) for n, d in qualified.items() if d['kicks_received'] > 0]
        if kr_cands:
            best_n, best_v = max(kr_cands, key=lambda x: x[1])
            awards['most_kicks_received'] = {'nick': best_n, 'value': best_v}

        # Most consistent (most active days)
        cons_cands = [(d.get('display_nick') or n, len(d['daily_lines'])) for n, d in qualified.items()]
        if cons_cands:
            best_n, best_v = max(cons_cands, key=lambda x: x[1])
            awards['most_consistent'] = {'nick': best_n, 'value': best_v}

        # Wordiest (highest avg WPL, min 20 lines)
        wpl_cands = [
            (d.get('display_nick') or n, round(d['words'] / d['lines'], 2))
            for n, d in qualified.items() if d['lines'] >= 20
        ]
        if wpl_cands:
            best_n, best_v = max(wpl_cands, key=lambda x: x[1])
            awards['wordiest'] = {'nick': best_n, 'value': best_v}

        # Quietest (lowest avg WPL, min 20 lines)
        if wpl_cands:
            best_n, best_v = min(wpl_cands, key=lambda x: x[1])
            awards['quietest'] = {'nick': best_n, 'value': best_v}

        return awards

    def _build_network(self, qualified, user_data, mention_matrix, n=30) -> dict:
        # Top N users by lines
        top = sorted(qualified.keys(), key=lambda k: qualified[k]['lines'], reverse=True)[:n]
        top_set = set(top)

        nodes = []
        for nick in top:
            d = qualified[nick]
            nodes.append({
                'id': d.get('display_nick') or nick,
                'nick': nick,
                'lines': d['lines'],
            })

        links = []
        seen_pairs = set()
        for (src, tgt), weight in mention_matrix.items():
            if src in top_set and tgt in top_set and weight >= 2:
                pair = tuple(sorted([src, tgt]))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    src_disp = user_data[src].get('display_nick') or src
                    tgt_disp = user_data[tgt].get('display_nick') or tgt
                    links.append({
                        'source': src_disp,
                        'target': tgt_disp,
                        'value': weight,
                    })

        return {'nodes': nodes, 'links': links}
