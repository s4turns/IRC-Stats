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

**With ignores:**
```bash
python3 stats.py /home/ubuntu/eggdrop/logs/lrh.log.* \
  -o /var/www/lrh \
  -n EFnet \
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
| `--no-host-merge` | — | Disable host-based identity grouping (see below) |
| `--debug` | — | Print unmatched log lines for parser diagnosis |

---

## Identity & Nick Merging

User identity is **host-based**: each unique `ident@host` from join events is treated as one person. Nick-change propagation is intentionally not used — it produced false positives when different people reused the same nick at different times.

**How it works:**

1. **Join events** record each nick's `ident@host`. Hostnames are normalized to strip dynamic ISP prefixes (e.g. `pool-12-34.isp.net` → `isp.net`) and irccloud session suffixes (`/ip.x.x.x.x`), so users with rotating IPs from the same provider are correctly grouped.

2. **Grouping** — nicks that share a normalized `ident@host` across any join event are merged into one identity. The display name shown is the most recently seen nick in that group.

Use `--no-host-merge` to disable grouping entirely (useful for shared shell servers where many users appear from the same host).

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
    ├── index.html      # Per-user page (served via URL hash)
    └── data/
        └── Nick.json   # Per-user data (top N users only)
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
0 * * * * python3 /home/ubuntu/irc-stats/stats.py /home/ubuntu/eggdrop/logs/lrh.log.* -o /var/www/lrh -n EFnet --ignore botname
```

---

by [interdome](https://interdo.me)
