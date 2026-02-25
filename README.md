# irc-stats

Modern IRC statistics generator for eggdrop channel logs. Produces a self-contained static site with charts, word clouds, a mention network graph, per-user pages, and activity heatmaps.

**Live demo:** https://lrh.interdo.me

---

## Requirements

- Python 3.9+
- Jinja2

```bash
pip install -r requirements.txt
```

---

## Usage

```bash
python3 stats.py LOGFILES... [options]
```

**Basic example:**
```bash
python3 stats.py /home/ubuntu/eggdrop/logs/lrh.log.* -o ./site -n EFnet
```

**With nick merges and ignores:**
```bash
python3 stats.py /home/ubuntu/eggdrop/logs/lrh.log.* \
  -o /var/www/lrh \
  -n EFnet \
  --merge-nicks alias1=canonical alias2=canonical \
  --ignore botname1 botname2 \
  --ignore-pattern ".*bot$" "^[Ss]erv"
```

---

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `-o`, `--output` | `./site` | Output directory |
| `-c`, `--channel` | auto-detected | Channel name override |
| `-n`, `--network` | `IRC` | Network name |
| `--title` | auto | Page title override |
| `--min-lines` | `5` | Minimum lines to include a user |
| `--top-users` | `50` | Users shown in the main table (others go to "Did Not Make the List") |
| `--wordcloud-words` | `200` | Words in the word cloud |
| `--ignore` | — | Nicks to exclude entirely (users, bots, services) |
| `--ignore-pattern` | — | Regex patterns — any matching nick is excluded (e.g. `".*bot$"`) |
| `--ignore-file` | — | File with one nick or regex per line (`#` comments supported) |
| `--bot-nicks` | — | Alias for `--ignore` (kept for compatibility) |
| `--merge-nicks` | — | Exact nick aliases: `ALIAS=CANONICAL` |
| `--nick-patterns` | — | Regex nick aliases: `PATTERN=CANONICAL` |
| `--no-auto-merge` | — | Disable auto-detection of nick variants (`Nick_`, `Nick\|away`, etc.) |
| `--no-host-merge` | — | Disable merging nicks by shared `ident@host` |
| `--debug` | — | Print unmatched log lines for parser diagnosis |

Nick merging is **on by default** for all three strategies (auto, host, nick-change). Use `--no-*` flags to disable.

---

## Nick Merging

Five layers applied in order — first match wins:

1. **Exact** (`--merge-nicks`) — manual `ALIAS=CANONICAL` pairs
2. **Regex** (`--nick-patterns`) — pattern-based: `REGEX=CANONICAL`
3. **Auto-merge** — strips common IRC suffixes (`_`, `|away`, `[afk]`, trailing digits) and groups variants
4. **Host-merge** — groups nicks that share the same `ident@host` across join events (dynamic ISP hostnames are normalized to their base domain)

---

## Log Format

Supports the following eggdrop channel log formats:

```
[HH:MM] <Nick> message
[HH:MM:SS] <Nick> message
[DD Mon YYYY HH:MM:SS] <Nick> message
--- Log opened Fri Feb 21 2026
--- Day changed Sat Feb 22 2026
nick (ident@host) joined #channel.
nick (ident@host) left #channel.
nick (ident@host) left irc: Quit: reason
nick kicked from #channel by op: reason
#CHANNEL: mode change '+b mask' by nick!user@host
Nick change: oldnick -> newnick
```

Date detection priority: full timestamp in line → `Day changed` header → filename (`lrh.log.MMDDYYYY`) → midnight-rollover heuristic.

---

## Output Structure

```
site/
├── index.html          # Main stats page
├── style.css
├── app.js
├── data.json           # Full data export
└── users/
    ├── Nick.html       # Per-user page (top N users only)
    └── Nick.json       # Per-user data
```

---

## Deployment (Apache + Cloudflare)

Example vhost config at `lrh.interdo.me.conf`. Key settings:

```apache
ErrorDocument 404 /404.html

# Cache static assets longer than HTML/JSON
<FilesMatch "\.(css|js)$">
    Header set Cache-Control "max-age=3600"
</FilesMatch>
<FilesMatch "\.(html|json)$">
    Header set Cache-Control "max-age=300"
</FilesMatch>
```

Regenerate on a cron:
```bash
0 * * * * python3 /home/ubuntu/irc-stats/stats.py /home/ubuntu/eggdrop/logs/lrh.log.* -o /var/www/lrh -n EFnet --merge-nicks ...
```

---

by [interdome](https://interdo.me)
