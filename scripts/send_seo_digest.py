"""Daily SEO email digest: summarizes Search Console, on-page audit, index
coverage, and PageSpeed straight from the local dashboard DB (same summary
functions the dashboard itself uses, so the email never disagrees with what
you'd see logging in) and emails it via SMTP.

Requires SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO
in .env. For Gmail, SMTP_PASSWORD must be an App Password
(myaccount.google.com/apppasswords), not the account's real password.
"""

from __future__ import annotations

import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as dashboard_app  # noqa: E402
from scripts.env_utils import load_env_file, log_sync  # noqa: E402

DIGEST_WINDOW_DAYS = 7


class DigestError(RuntimeError):
    pass


def build_digest() -> tuple[str, str]:
    """Returns (subject, plain_text_body)."""
    start_date, end_date = dashboard_app.default_date_range(DIGEST_WINDOW_DAYS)
    with dashboard_app.db() as con:
        gsc = dashboard_app.search_console_summary(con, start_date, end_date)
        statuses = dashboard_app.sync_status(con)

    onpage = gsc["onpage_audit"]
    pagespeed = gsc["pagespeed"]
    coverage = gsc["index_coverage"]

    failed_sources = [s for s in statuses if not s["status"].startswith("ok")]
    slow_pages = [p for p in pagespeed["pages"] if p["performance_score"] is not None and p["performance_score"] < 50]

    headline_bits = []
    if onpage["available"]:
        headline_bits.append(f"{len(onpage['pages'])} on-page issue(s)")
    if slow_pages:
        headline_bits.append(f"{len(slow_pages)} slow page(s)")
    if failed_sources:
        headline_bits.append(f"{len(failed_sources)} sync failure(s)")
    headline = ", ".join(headline_bits) if headline_bits else "all clear"

    subject = f"DOMA SEO digest - {headline} ({end_date})"

    lines: list[str] = []
    lines.append(f"DOMA SEO daily digest - {start_date} to {end_date}")
    lines.append("=" * 60)
    lines.append("")

    lines.append("SEARCH CONSOLE (last %d days)" % DIGEST_WINDOW_DAYS)
    if gsc["available"]:
        lines.append(
            f"  Clicks: {gsc['clicks']}  |  Impressions: {gsc['impressions']}  |  "
            f"CTR: {gsc['ctr']}%  |  Avg. position: {gsc['position']}"
        )
    else:
        lines.append("  No data yet.")
    lines.append("")

    lines.append("INDEX COVERAGE")
    if coverage["available"]:
        lines.append(f"  {coverage['healthy_count']} healthy / {coverage['total_checked']} checked")
        needs_attention = coverage["total_checked"] - coverage["healthy_count"]
        if needs_attention:
            lines.append(f"  {needs_attention} page(s) need attention - see dashboard SEO tab for the list.")
    else:
        lines.append("  No data yet.")
    lines.append("")

    lines.append("ON-PAGE SEO AUDIT")
    if onpage["available"]:
        lines.append(f"  {onpage['healthy_count']} healthy / {onpage['total_checked']} checked")
        for page in onpage["pages"][:10]:
            path = page["url"].split("dentalofficemanagers.com", 1)[-1] or "/"
            lines.append(f"  - {path}")
            for finding in page["findings"][:3]:
                lines.append(f"      {finding}")
        if len(onpage["pages"]) > 10:
            lines.append(f"  ... and {len(onpage['pages']) - 10} more. See dashboard SEO tab.")
    else:
        lines.append("  No data yet.")
    lines.append("")

    lines.append("PAGE SPEED (mobile)")
    if pagespeed["available"]:
        lines.append(f"  Avg. performance score: {pagespeed['avg_performance_score']}")
        if slow_pages:
            lines.append("  Below 50:")
            for page in slow_pages:
                path = page["url"].split("dentalofficemanagers.com", 1)[-1] or "/"
                lines.append(f"    - {path}: {page['performance_score']}")
    else:
        lines.append("  No data yet.")
    lines.append("")

    if failed_sources:
        lines.append("SYNC FAILURES")
        for s in failed_sources:
            lines.append(f"  - {s['source']}: {s['detail']}")
        lines.append("")

    lines.append("-" * 60)
    lines.append("Full dashboard: see GITHUB_PAGES_URL in .env, or run app.py locally.")

    return subject, "\n".join(lines)


def send_email(subject: str, body: str, env: dict[str, str]) -> None:
    host = env.get("SMTP_HOST")
    port = env.get("SMTP_PORT")
    user = env.get("SMTP_USER")
    password = env.get("SMTP_PASSWORD")
    from_addr = env.get("EMAIL_FROM") or user
    to_addr = env.get("EMAIL_TO")

    if not all([host, port, user, password, to_addr]):
        raise DigestError("Missing SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/EMAIL_TO in .env")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, int(port), timeout=30, context=context) as server:
        server.login(user, password)
        server.send_message(msg)


def main() -> int:
    env = load_env_file()
    dashboard_app.init_db()

    subject, body = build_digest()
    send_email(subject, body, env)

    log_sync(dashboard_app, "seo_digest_email", "ok", subject)
    print(f"SEO digest sent: {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
