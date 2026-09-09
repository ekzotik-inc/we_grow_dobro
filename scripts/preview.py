"""Рендер всех участнических экранов на живых данных — визуальная проверка текстов."""
import asyncio, html, os, re
os.environ.update(BOT_TOKEN="1:x", ADMIN_IDS="999",
                  DATABASE_URL="sqlite+aiosqlite:///./data/prev.db")
from app import emoji, services, texts, keyboards as kb
from app.db import SessionLocal, init_db

def show(title, s, markup=None):
    print("\n" + "═"*64); print(f"  {title}"); print("═"*64)
    # так это увидит участник: премиум-эмодзи -> их запасной символ, теги убраны
    out = re.sub(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', r'\1', s)
    out = out.replace("<blockquote expandable>", "▏(раскрывается) ").replace("<blockquote>", "▏")
    out = out.replace("</blockquote>", "")
    out = re.sub(r"</?(b|i|u|s|code|tg-spoiler)>", "", out)
    out = html.unescape(out)          # так текст видит участник, а не как HTML-исходник
    print(out)
    print(f"  [длина сообщения: {len(out)} симв.]" if len(out) > 700 else "", end="")
    if markup:
        print("─"*64)
        for row in markup.inline_keyboard:
            print("  " + "   ".join(f"[ {(emoji.char_for_id(b.icon_custom_emoji_id) + ' ') if b.icon_custom_emoji_id else ''}{b.text} ]" for b in row))

async def main():
    if os.path.exists("data/prev.db"): os.remove("data/prev.db")
    await init_db()
    async with SessionLocal() as s:
        await services.set_week_open(s, 1, True)   # первую неделю открывает админ в панели
        await s.commit()
        u = await services.get_or_create_user(s, 1000, "ivan")
        await services.register_user(s, u, "Иван Петров", "Отдел продаж", "Алматы")
        await services.approve_user(s, u, 999); await s.commit()
        show("ПРАВИЛА", texts.RULES, kb.rules_kb(False))
        show("ПРИВЕТСТВИЕ", texts.welcome(u), kb.start_kb())
        u = await services.get_user(s, 1000)
        show("МЕНЮ — без команды", texts.main_menu(u, 0, None, None, {"total_teams": 0, "approved_total": 0, "submitted": 0}), kb.main_menu_kb(u))

        team = await services.create_team(s, u, "Добряки", "🔥")
        await services.join_team(s, u, team.id)
        for i in range(1, 4):
            m = await services.get_or_create_user(s, 1000+i, f"u{i}")
            await services.register_user(s, m, f"Коллега {i}", "Отдел", "Алматы")
            await services.approve_user(s, m, 999); await services.join_team(s, m, team.id)
        await s.commit()
        u = await services.get_user(s, 1000)

        tasks = await services.list_tasks(s, 1)
        t1 = next(x for x in tasks if not x.options)
        # пошаговый мастер: показываем каждый шаг так, как его видит участник
        topt2 = next(x for x in tasks if x.options)
        sub = await services.start_submission(s, u, t1, None)
        steps = services.submission_steps(sub)
        for i, st in enumerate(steps):
            show(f"ОТЧЁТ — ШАГ {i + 1}", texts.submission_step(sub, i),
                 kb.submission_step_kb(sub, i, len(steps), services.step_done(sub, i)))
            if st.get("kind") == "note":
                await services.save_step_answer(s, sub, i, "Написал открытку Марине за помощь с отчётом")
            else:
                await services.add_file(s, sub, {"type":"photo","file_id":f"f{i}","name":None}, step=i)
            await s.commit()
            sub = await services.get_submission(s, sub.id)
        done = [services.step_done(sub, i) for i in range(len(steps))]
        show("ОТЧЁТ — ВСЁ СОБРАНО", texts.submission_review(sub),
             kb.submission_review_kb(sub, steps, done, not services.steps_left(sub)))
        sub_opt = await services.start_submission(s, u, topt2, topt2.options[0].id)
        show("ОТЧЁТ С ВАРИАНТОМ — ШАГ 1", texts.submission_step(sub_opt, 0),
             kb.submission_step_kb(sub_opt, 0, len(services.submission_steps(sub_opt)), False))
        await services.cancel_submission(s, sub_opt); await s.commit()
        await services.send_for_review(s, sub); await s.commit()
        sub = await services.get_submission(s, sub.id)
        await services.review_submission(s, sub, True, 999); await s.commit()

        u = await services.get_user(s, 1000)
        rows = await services.leaderboard(s)
        pts = await services.user_points(s, u.id)
        ws = dict(await services.week_stats_for_user(s, u.id, 1))
        ws.update(total_teams=len(rows), approved_total=1)
        show("МЕНЮ — в команде, есть зачёт", texts.main_menu(u, pts, rows[0]["points"], 1, ws), kb.main_menu_kb(u))

        subs = {x.task_id: x for x in await services.user_submissions(s, u.id)}
        show("ЗАДАНИЯ НЕДЕЛИ", texts.tasks_list(1, tasks, subs), kb.week_tabs_kb(1, tasks, subs))
        topt = next(x for x in tasks if x.options)
        show("ЗАДАНИЕ С ВЫБОРОМ", texts.task_card(topt, None, 4), kb.task_card_kb(topt, None, True, u))
        show("КОМАНДЫ", texts.teams_list(rows, u), kb.teams_kb(rows, u))
        show("МОЯ КОМАНДА", texts.team_card(rows[0]["team"], rows[0]["members"], rows[0]["points"], 1,
                                            await services.user_points_map(s), u), kb.team_card_kb(rows[0]["team"], u, False))
        show("ЗАДАНИЕ ОБЫЧНОЕ", texts.task_card(tasks[0], None, 1), kb.task_card_kb(tasks[0], None, True, u))
        show("МОЙ ВКЛАД", texts.my_results(u, await services.user_submissions(s, u.id), pts))
        show("РЕЙТИНГ", texts.leaderboard_text(rows))
        show("АНОНС НЕДЕЛИ", texts.week_announce(1, tasks))
        show("НАПОМИНАНИЕ", texts.week_reminder(1))
        p = await services.get_or_create_user(s, 2000, "new")
        await services.register_user(s, p, "Новый Сотрудник", "IT", "Алматы"); await s.commit()
        show("ЖДЁТ МОДЕРАЦИИ", texts.pending_status(p), kb.pending_kb(p))
        for w in (1, 2, 3):
            await services.set_week_open(s, w, False)
        await s.commit()
        show("ЗАДАНИЯ ЗАКРЫТЫ", texts.tasks_soon(), kb.back_kb())
    os.remove("data/prev.db")
asyncio.run(main())
