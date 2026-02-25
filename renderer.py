"""
Renders the static site from templates and data.
"""

import json
import os
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _safe_nick(nick: str) -> str:
    return ''.join(c if (c.isalnum() or c in '-_') else '_' for c in nick)


class Renderer:
    def __init__(self, data: dict, output_dir: str, title: str = None, top_users: int = 50):
        self.data        = data
        self.output_dir  = Path(output_dir)
        self.title       = title or f"{data['channel']} IRC Stats"
        self.top_users   = top_users
        self.tpl_dir     = Path(__file__).parent / 'templates'
        self.static_dir  = Path(__file__).parent / 'static'

    def render(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        users_dir = self.output_dir / 'users'
        users_dir.mkdir(exist_ok=True)

        # Copy static assets
        if self.static_dir.exists():
            for f in self.static_dir.iterdir():
                shutil.copy2(f, self.output_dir / f.name)

        # Jinja2 env
        env = Environment(
            loader=FileSystemLoader(str(self.tpl_dir)),
            autoescape=select_autoescape(['html']),
        )
        env.filters['safe_nick'] = _safe_nick

        # Slim version of data for index.html (excludes heavy per-user data)
        index_data = {k: v for k, v in self.data.items() if k != 'users'}

        # Sort all users by all-time lines once; reuse for both non-top list and JSON writing
        all_users_sorted = sorted(
            self.data['users'].items(),
            key=lambda x: x[1].get('lines', 0),
            reverse=True,
        )
        top_nicks = {nick for nick, _ in all_users_sorted[:self.top_users]}

        non_top_users = [
            {
                'nick':        u.get('nick') or nick,
                'lines':       u.get('lines', 0),
                'words':       u.get('words', 0),
                'avg_wpl':     u.get('avg_wpl', 0),
                'active_days': u.get('active_days', 0),
                'first_seen':  u.get('first_seen', ''),
                'last_seen':   u.get('last_seen', ''),
            }
            for nick, u in all_users_sorted[self.top_users:]
            if u.get('lines', 0) >= 1
        ]
        index_data['non_top_users'] = non_top_users

        index_data_json = json.dumps(index_data, ensure_ascii=False, default=str)
        index_data_json = index_data_json.replace('</script>', r'<\/script>')

        # cache_bust: changes every regeneration so browsers fetch fresh CSS/JS
        import re as _re
        cache_bust = _re.sub(r'[^0-9]', '', self.data.get('generated_at', ''))

        ctx_base = dict(
            data=self.data,
            data_json=index_data_json,   # index page only needs stats, not per-user detail
            title=self.title,
            channel=self.data['channel'],
            network=self.data['network'],
            cache_bust=cache_bust,
            top_users=self.top_users,
        )

        # ── index.html ────────────────────────────────────────────────────
        tpl = env.get_template('index.html')
        html = tpl.render(**ctx_base)
        (self.output_dir / 'index.html').write_text(html, encoding='utf-8')
        print(f"  Wrote index.html")

        # ── single user shell page ────────────────────────────────────────
        # One HTML file serves all users via URL hash: users/#sayjay
        user_tpl = env.get_template('user.html')
        html = user_tpl.render(**ctx_base)
        (users_dir / 'index.html').write_text(html, encoding='utf-8')

        # ── per-user JSON files — top N users only ────────────────────────
        data_dir = users_dir / 'data'
        data_dir.mkdir(exist_ok=True)

        written = 0
        for nick, user in all_users_sorted:
            if nick not in top_nicks:
                break   # sorted descending — everything after this is non-top
            if user.get('lines', 0) < 1:
                continue
            sn = _safe_nick(user.get('nick') or nick)
            (data_dir / f'{sn}.json').write_text(
                json.dumps(user, ensure_ascii=False, default=str),
                encoding='utf-8',
            )
            written += 1
        print(f"  Wrote users/index.html + {written} user JSON files")

        # ── data.json ─────────────────────────────────────────────────────
        (self.output_dir / 'data.json').write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False, default=str),
            encoding='utf-8',
        )
        print(f"  Wrote data.json")
