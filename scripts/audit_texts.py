"""Проверка всех экранов: HTML разбирается, длина в лимитах Telegram."""
import sys
import asyncio, os, re
os.environ.update(BOT_TOKEN="1:x", DATABASE_URL="sqlite+aiosqlite:///:memory:",
                  PC_USERNAME="DaryaPMI", PC_CONTACT="Дарья (P&C)")
from html.parser import HTMLParser
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from app.db import init_db, SessionLocal
from app import services, texts

ALLOWED = {"b","strong","i","em","u","s","code","pre","a","blockquote","tg-emoji","tg-spoiler","br"}

class Check(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.stack=[]; self.bad=[]
    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED: self.bad.append(f"неизвестный тег <{tag}>")
        else: self.stack.append(tag)
    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag: self.bad.append(f"лишний/непарный </{tag}>")
        else: self.stack.pop()

def plain_len(s):
    s = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', s)
    return len(re.sub(r'<[^>]+>', '', s))

problems = []
def check(name, text, limit=4096):
    p = Check(); p.feed(text); p.close()
    if p.bad: problems.append(f"{name}: HTML — {', '.join(p.bad)}")
    if p.stack: problems.append(f"{name}: не закрыт тег <{p.stack[-1]}>")
    n = plain_len(text)
    if n > limit: problems.append(f"{name}: длина {n} > лимита {limit}")
    if "&" in re.sub(r'&(amp|lt|gt|quot|#\d+);', '', text): problems.append(f"{name}: голый & в тексте")
    return n

async def main():
    await init_db()
    async with SessionLocal() as s:
        for _w in (1, 2, 3): await services.set_week_open(s, _w, True)
        u = await services.get_or_create_user(s, 1, "ivan")
        await services.register_user(s, u, "Иван Петров", "Отдел продаж", "Ташкент", phone="+998901234567")
        await services.approve_user(s, u, 999)
        team = await services.create_team(s, None, "Добряки", "🔥"); await services.join_team(s, u, team.id)
        await s.commit(); u = await services.get_user(s, 1)
        tasks = await services.list_tasks(s)
        rows = await services.leaderboard(s); upts = await services.user_points_map(s)

        widest = []
        check("RULES", texts.RULES)
        check("welcome", texts.welcome(u))
        for step in ("full_name","phone","team","confirm"):
            check(f"registration_step:{step}", texts.registration_step(step, {"full_name":"Иван Петров","phone":"+998901234567","team_name":"🔥 Добряки"}))
        check("main_menu", texts.main_menu(u, 200, 200, 1, {"total_teams":1,"approved_total":1,"submitted":1,"approved":1,"my_rank":1,"total_users":5}))
        check("tasks_soon", texts.tasks_soon())
        check("teams_list", texts.teams_list(rows, u))
        check("team_card", texts.team_card(team, [u], 200, 1, upts, u))
        check("leaderboard", texts.leaderboard_text(rows))
        check("help_screen", texts.help_screen())
        check("help_team_sent", texts.help_team_sent(1))
        check("pending_status", texts.pending_status(u))
        check("my_results", texts.my_results(u, [], 0))
        for w in (1,2,3):
            wt = [t for t in tasks if t.week == w]
            widest.append((plain_len(texts.week_announce(w, wt)), f"week_announce:{w}"))
            check(f"tasks_list:{w}", texts.tasks_list(w, wt, {}))
            check(f"week_announce:{w}", texts.week_announce(w, wt), 1024)
            check(f"week_reminder:{w}", texts.week_reminder(w))
        for t in tasks:
            n = check(f"task_card:{t.code}", texts.task_card(t, None, 1), 1024)
            widest.append((n, f"task_card:{t.code}"))
            # экраны шагов и итог
            sub = await services.start_submission(s, u, t, t.options[0].id if t.options else None)
            steps = services.submission_steps(sub)
            if not steps: problems.append(f"задание {t.code}: нет пошаговой инструкции")
            for i in range(len(steps)):
                widest.append((check(f"step:{t.code}:{i+1}", texts.submission_step(sub, i)), f"step:{t.code}:{i+1}"))
            check(f"review:{t.code}", texts.submission_review(sub))
            check(f"editor:{t.code}", texts.submission_editor(sub))
            check(f"channel_card:{t.code}", texts.submission_channel_card(sub))
            await services.cancel_submission(s, sub); await s.commit()
        check("registration_channel_card", texts.registration_channel_card(u))
        check("push_approved", texts.push_approved(u))
        check("push_team_assigned", texts.push_team_assigned(u, None))
        check("push_disqualified", texts.push_disqualified("нарушение"))
        check("push_deleted", texts.push_deleted())
        check("user_deleted", texts.user_deleted({"name":"Иван","submissions":2,"points":400}))
        check("user_delete_confirm", texts.user_delete_confirm(u, 2, 400))
        check("top_digest", texts.top_digest(rows, [("Иван","🔥 Добряки",200)]))
        for i in range(6): check(f"motivation:{i}", texts.motivation(i))
        check("submission_sent", texts.submission_sent(tasks[0]))
    widest.sort(reverse=True)
    print("самые длинные экраны:", [(n, name) for n, name in widest[:4]])
    print("ПРОБЛЕМ:", len(problems))
    for p in problems: print("  -", p)

asyncio.run(main())
