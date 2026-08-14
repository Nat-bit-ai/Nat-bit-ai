#!/usr/bin/env python3
"""
Pulls real data from the GitHub API and renders it into the SVG card
templates in /templates, writing the finished cards into /assets.

Run by .github/workflows/update-profile-data.yml on a schedule, so the
README always reflects real, current GitHub data -- while keeping the
exact hand-designed card look (nothing here touches the visual style).

Env vars required:
  GH_USERNAME   - the GitHub username the profile belongs to
  GH_TOKEN      - a token with at least public read access
                  (the default GITHUB_ACTIONS token works for public data)
"""

import os
import sys
import datetime
import requests

USERNAME = os.environ["GH_USERNAME"]
TOKEN = os.environ["GH_TOKEN"]

GRAPHQL_URL = "https://api.github.com/graphql"
REST_ROOT = "https://api.github.com"
HEADERS = {"Authorization": f"bearer {TOKEN}"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")
ASSETS = os.path.join(ROOT, "assets")

# Same gradient ids already defined in templates/tech_stack_learning.svg,
# cycled through for however many languages we end up showing (max 7).
BAR_GRADIENTS = [
    "bar-flutter", "bar-dart", "bar-node", "bar-express",
    "bar-postgres", "bar-dsa", "bar-sys",
]
DOT_COLORS = [
    "#38bdf8", "#818cf8", "#39d353", "#c084fc",
    "#60a5fa", "#fbbf24", "#fb7185",
]

HEATMAP_COLS = 14   # weeks
HEATMAP_ROWS = 5    # Mon..Fri
CELL = 13           # px spacing matching the original template geometry
CELL_X0 = 28
CELL_Y0 = 10


def gql(query, variables):
    r = requests.post(
        GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def fmt_count(n):
    """750 -> '750+', 1200 -> '1.2k+' style compact display."""
    if n >= 1000:
        return f"{n/1000:.1f}k+".replace(".0k+", "k+")
    return f"{n}+"


def fetch_profile():
    query = """
    query($login: String!) {
      user(login: $login) {
        createdAt
        followers { totalCount }
        repositories(ownerAffiliations: OWNER, isFork: false) { totalCount }
        contributionsCollection {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays { date contributionCount weekday }
            }
          }
        }
      }
    }
    """
    data = gql(query, {"login": USERNAME})["user"]
    return data


def fetch_total_commits(created_at):
    """Sum totalCommitContributions across every year since account creation."""
    start_year = datetime.datetime.fromisoformat(
        created_at.replace("Z", "+00:00")
    ).year
    this_year = datetime.datetime.utcnow().year

    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
        }
      }
    }
    """
    total = 0
    for year in range(start_year, this_year + 1):
        frm = f"{year}-01-01T00:00:00Z"
        to = f"{year}-12-31T23:59:59Z"
        data = gql(query, {"login": USERNAME, "from": frm, "to": to})
        total += data["user"]["contributionsCollection"]["totalCommitContributions"]
    return total


def fetch_top_languages():
    repos = requests.get(
        f"{REST_ROOT}/users/{USERNAME}/repos",
        headers=HEADERS,
        params={"per_page": 100, "type": "owner"},
        timeout=30,
    ).json()

    totals = {}
    for repo in repos:
        if repo.get("fork"):
            continue
        langs = requests.get(repo["languages_url"], headers=HEADERS, timeout=30).json()
        for lang, byte_count in langs.items():
            totals[lang] = totals.get(lang, 0) + byte_count

    grand_total = sum(totals.values()) or 1
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:7]
    return [(name, round(100 * count / grand_total)) for name, count in ranked]


def compute_streaks(days):
    """days: list of {date, contributionCount} in chronological order."""
    today = datetime.date.today()
    current = 0
    longest = 0
    run = 0
    for d in days:
        date = datetime.date.fromisoformat(d["date"])
        if d["contributionCount"] > 0:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    # current streak: walk backwards from today (or yesterday, since today
    # may not have contributions registered yet)
    by_date = {d["date"]: d["contributionCount"] for d in days}
    cursor = today
    while True:
        key = cursor.isoformat()
        if key not in by_date:
            break
        if by_date[key] > 0:
            current += 1
            cursor -= datetime.timedelta(days=1)
        else:
            if cursor == today:
                # today just hasn't happened yet -- check yesterday instead
                cursor -= datetime.timedelta(days=1)
                continue
            break
    return current, longest


def build_language_rows(languages):
    rows = []
    y = 106
    for i, (name, pct) in enumerate(languages):
        dot = DOT_COLORS[i % len(DOT_COLORS)]
        grad = BAR_GRADIENTS[i % len(BAR_GRADIENTS)]
        bar_w = round(195 * pct / 100)
        rows.append(f'''  <text x="495" y="{y+4}" class="item-label">{name}</text>
  <text x="605" y="{y+4}" class="percent-label" fill="{dot}">{pct}%</text>
  <rect x="645" y="{y-5}" width="195" height="10" class="bar-bg" />
  <rect x="645" y="{y-5}" width="{bar_w}" height="10" class="bar-fill" fill="url(#{grad})" />''')
        y += 32
    return "\n\n".join(rows)


def build_heatmap(weekday_days):
    """weekday_days: last HEATMAP_COLS*HEATMAP_ROWS weekday entries,
    chronological, grouped into columns of HEATMAP_ROWS (Mon..Fri)."""
    counts = [d["contributionCount"] for d in weekday_days]
    max_c = max(counts) if counts else 0

    def color_for(c):
        if c == 0:
            return "#161b22"
        if max_c <= 0:
            return "#0e4429"
        ratio = c / max_c
        if ratio < 0.25:
            return "#0e4429"
        if ratio < 0.5:
            return "#006d32"
        if ratio < 0.75:
            return "#26a641"
        return "#39d353"

    cells = []
    for col in range(HEATMAP_COLS):
        x = CELL_X0 + col * CELL
        for row in range(HEATMAP_ROWS):
            idx = col * HEATMAP_ROWS + row
            y = CELL_Y0 + row * CELL
            c = weekday_days[idx]["contributionCount"] if idx < len(weekday_days) else 0
            cells.append(
                f'<rect x="{x}" y="{y}" width="10" height="10" rx="2" fill="{color_for(c)}" />'
            )
    return "\n    ".join(cells)


def render_template(name, replacements):
    path = os.path.join(TEMPLATES, name)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    for key, value in replacements.items():
        content = content.replace("{{" + key + "}}", str(value))
    out_path = os.path.join(ASSETS, name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"wrote {out_path}")


def main():
    profile = fetch_profile()
    followers = profile["followers"]["totalCount"]
    repos = profile["repositories"]["totalCount"]
    calendar = profile["contributionsCollection"]["contributionCalendar"]
    contributions_last_year = calendar["totalContributions"]

    all_days = [d for w in calendar["weeks"] for d in w["contributionDays"]]
    current_streak, longest_streak = compute_streaks(all_days)

    total_commits = fetch_total_commits(profile["createdAt"])

    weekday_days = [d for d in all_days if 1 <= d["weekday"] <= 5]
    weekday_days = weekday_days[-(HEATMAP_COLS * HEATMAP_ROWS):]

    languages = fetch_top_languages()

    render_template("hero_card.svg", {
        "PROJECTS": fmt_count(repos),
        "COMMITS": fmt_count(total_commits),
        "FOLLOWERS": fmt_count(followers),
        "CONTRIBUTIONS": fmt_count(contributions_last_year),
    })

    render_template("tech_stack_learning.svg", {
        "LANGUAGE_ROWS": build_language_rows(languages),
    })

    render_template("stats_dashboard.svg", {
        "TOTAL_COMMITS": fmt_count(total_commits),
        "REPOS": fmt_count(repos),
        "FOLLOWERS": fmt_count(followers),
        "CONTRIBUTIONS": fmt_count(contributions_last_year),
        "HEATMAP_CELLS": build_heatmap(weekday_days),
        "CURRENT_STREAK": current_streak,
        "LONGEST_STREAK": longest_streak,
    })


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"generate_svgs.py failed: {exc}", file=sys.stderr)
        sys.exit(1)
