"""The web panel's pages open with the sidebar (2026-11-14: the top bar became a sidebar, its sections in
groups): every admin page renders, the open page's group is open, a teacher sees only the checks. Offline - a
throwaway Postgres and Redis (tests run by test_part2.sh). With an argument, the pages are also saved there
as HTML (to look at in a browser):

    python tests/panel_pages_test.py [out_dir]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

fails: list[str] = []
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


PAGES = {"/": "dashboard", "/users": "users", "/checks": "checks", "/payments": "payments", "/support": "support",
         "/groups": "groups", "/teachers": "teachers", "/questions": "questions", "/charts": "charts",
         "/broadcast": "broadcast", "/errors": "errors", "/settings": "settings", "/admins": "admins", "/audit": "audit"}
GROUP = {"users": "users", "support": "users", "broadcast": "users", "checks": "checks", "questions": "checks",
         "charts": "checks", "teachers": "centers", "groups": "centers", "errors": "admin", "settings": "admin",
         "admins": "admin", "audit": "admin"}


async def main() -> None:
    from db.database import get_session
    from db.models import WebAdmin
    from webapp import panel
    from webapp.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as owner:
        await owner.post("/login", data={"username": settings.web_admin_username,
                                         "password": settings.web_admin_password})
        for path, active in PAGES.items():
            r = await owner.get(path)
            html = r.text
            group = GROUP.get(active)
            opened = f'data-group="{group}" open' in html if group else True
            check(f"{path}: opens with the sidebar, no top bar; its group open", r.status_code == 200
                  and 'class="side"' in html and "topbar" not in html and opened
                  and html.count('data-group="') == 4, r.status_code)
            check(f"{path}: the fold button, a flyout title per group, full width", 'id="bzFold"' in html
                  and html.count('class="fly-h"') == 4 and ".wrap.wide" not in html and "max-width: 1440px" in html)
            if OUT:
                OUT.mkdir(parents=True, exist_ok=True)
                (OUT / f"{active}.html").write_text(html, encoding="utf-8")
    async with get_session() as session:
        session.add(WebAdmin(username="ustoz9", name="Ustoz", password_hash=panel.hash_password("pw9"), role="teacher"))
        await session.commit()
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as teacher:
        await teacher.post("/login", data={"username": "ustoz9", "password": "pw9"})
        html = (await teacher.get("/checks")).text
        check("a teacher: the checks only, no groups, no payments", 'class="side"' in html
              and 'data-group="' not in html and 'href="/payments"' not in html and "ustoz" in html)
        if OUT:
            (OUT / "teacher.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
