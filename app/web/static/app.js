/* We Grow Dobro — Telegram Mini App (vanilla JS, no build step). */
(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  const app = document.getElementById('app');
  const tabs = document.getElementById('tabs');
  let data = null;
  let tab = 'home';
  let week = null;
  let expanded = new Set();

  if (tg) { tg.ready(); tg.expand(); }

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const STATUS_CLASS = { approved: 'approved', pending: 'pending', rejected: 'rejected', draft: 'draft' };

  async function load() {
    const initData = tg ? tg.initData : '';
    try {
      const r = await fetch('/api/bootstrap', { headers: { Authorization: 'tma ' + initData } });
      if (!r.ok) throw new Error(r.status === 401 ? 'Открой приложение через кнопку в боте Telegram.' : 'Ошибка сервера ' + r.status);
      data = await r.json();
      week = data.marathon.current_week || 1;
      tabs.hidden = false;
      render();
    } catch (e) {
      app.innerHTML = '<div class="card"><b>Не удалось загрузить данные</b><div class="hint">' + esc(e.message) + '</div></div>';
    }
  }

  function render() {
    if (!data) return;
    const views = { home, tasks: tasksView, team, top };
    app.innerHTML = views[tab]();
    tabs.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    window.scrollTo(0, 0);
  }

  function weekStats(w) {
    const ts = data.tasks.filter((t) => t.week === w);
    const submitted = ts.filter((t) => t.submission && ['pending', 'approved'].includes(t.submission.status)).length;
    const approved = ts.filter((t) => t.submission && t.submission.status === 'approved').length;
    return { total: ts.length, submitted, approved };
  }

  function home() {
    const m = data.me, mar = data.marathon;
    let html = '<h1>🌱 ' + esc(mar.title) + '</h1>';
    if (!m.registered) {
      html += '<div class="alert">Ты ещё не зарегистрирован. Вернись в чат с ботом и нажми «Зарегистрироваться».</div>';
    }
    if (m.status === 'disqualified') html += '<div class="alert">🚫 Вы дисквалифицированы: результаты не учитываются в командном зачёте.</div>';
    html += '<div class="card"><div class="row"><div><b>' + esc(m.name) + '</b><div class="hint">' + esc(m.department || '') + '</div></div>' +
      '<div class="big">' + m.points + '<span class="hint" style="font-size:13px"> б.</span></div></div></div>';
    if (m.team) {
      html += '<div class="card"><div class="row"><div>' + esc(m.team.emoji) + ' <b>' + esc(m.team.name) + '</b><div class="hint">' + m.team.members.length + '/' + mar.team_size + ' участников</div></div>' +
        '<div style="text-align:right"><div class="big">' + m.team.points + '</div><div class="hint">' + m.team.rank + ' место</div></div></div></div>';
    } else {
      html += '<div class="warn">⚠️ Ты не в команде. Выбери команду в боте (меню → Команды) или попроси помощь P&C.</div>';
    }
    if (mar.status === 'before') {
      html += '<div class="card">🗓 Марафон стартует <b>' + fmt(mar.weeks[0].start) + '</b>. Собери команду из ' + mar.team_size + ' человек!</div>';
    } else if (mar.status === 'after') {
      html += '<div class="card">🏁 Марафон завершён. Спасибо за участие!</div>';
    } else {
      const ws = weekStats(mar.current_week);
      const w = mar.weeks.find((x) => x.number === mar.current_week);
      html += '<div class="card"><div class="row"><b>Неделя ' + mar.current_week + '</b><span class="hint">' + esc(w.label) + '</span></div>' +
        '<div class="stats" style="margin-top:10px"><div class="stat"><div class="v">' + ws.submitted + '/4</div><div class="l">отправлено</div></div>' +
        '<div class="stat"><div class="v">' + ws.approved + '</div><div class="l">зачтено</div></div>' +
        '<div class="stat"><div class="v">' + (4 - ws.submitted) + '</div><div class="l">ещё можно</div></div></div>' +
        (ws.submitted === 0 ? '<div class="warn" style="margin-top:10px">❗ Минимум 1 задание в неделю — не забудь отправить отчёт в боте.</div>' : '') +
        '<div class="progress"><i style="width:' + (ws.submitted / 4) * 100 + '%"></i></div></div>';
    }
    html += '<h2>Мои результаты</h2>';
    mar.weeks.forEach((w) => {
      const ts = data.tasks.filter((t) => t.week === w.number && t.submission);
      html += '<div class="card"><b>' + w.number + ' неделя</b> <span class="hint">' + esc(w.label) + '</span>';
      if (!ts.length) html += '<div class="hint">— отчётов нет</div>';
      ts.forEach((t) => {
        const s = t.submission;
        html += '<div class="member"><span>' + esc(t.emoji) + ' №' + t.code + ' ' + esc(t.title) + '</span><span class="badge ' + STATUS_CLASS[s.status] + '">' + esc(s.status_label) + (s.status === 'approved' ? ' +' + s.points : '') + '</span></div>';
      });
      html += '</div>';
    });
    html += '<button class="btn secondary" onclick="Telegram.WebApp.close()">Открыть чат с ботом</button>';
    return html;
  }

  function tasksView() {
    const mar = data.marathon;
    let html = '<h1>📋 Задания</h1><div class="weeks">' +
      mar.weeks.map((w) => '<button data-week="' + w.number + '" class="' + (w.number === week ? 'active' : '') + '">' + w.number + ' нед.</button>').join('') + '</div>';
    const w = mar.weeks.find((x) => x.number === week);
    const isOpen = mar.current_week === week;
    html += '<div class="hint" style="margin-bottom:8px">' + esc(w.label) + ' · ' + (isOpen ? '🟢 неделя активна' : (mar.current_week > week || mar.status === 'after') ? '⚪ завершена' : '🔒 откроется позже') + '</div>';
    data.tasks.filter((t) => t.week === week).forEach((t) => {
      const s = t.submission;
      const ex = expanded.has(t.id) ? ' expanded' : '';
      html += '<div class="card task' + ex + '" data-id="' + t.id + '"><div class="t">' + esc(t.emoji) + ' №' + t.code + '. ' + esc(t.title) + '</div>' +
        '<div class="meta"><span>⭐ ' + esc(t.points_label) + ' б.</span><span>📷 мин. ' + t.min_photos + '</span>' +
        (s ? '<span class="badge ' + STATUS_CLASS[s.status] + '">' + esc(s.status_label) + '</span>' : (t.open ? '<span class="badge open">доступно</span>' : '<span class="badge locked">закрыто</span>')) + '</div>' +
        '<div class="body">' + esc(t.description) + '<div class="cond"><b>Условия зачёта:</b>\n' + esc(t.conditions) + '</div>' +
        t.options.map((o) => '<div class="option"><b>▸ ' + esc(o.title) + ' — ' + o.points + ' б.</b>\n' + esc(o.conditions) + '</div>').join('') +
        (s && s.review_comment ? '<div class="alert" style="margin-top:8px">Комментарий P&C: ' + esc(s.review_comment) + '</div>' : '') +
        (t.open && (!s || s.status === 'rejected' || s.status === 'draft') ? '<button class="btn" data-submit="' + t.id + '">📤 Отправить отчёт в боте</button>' : '') +
        '</div></div>';
    });
    return html;
  }

  function team() {
    const m = data.me, mar = data.marathon;
    if (!m.team) return '<h1>👥 Команда</h1><div class="warn">Ты пока не в команде. Открой бот → «Команды», чтобы вступить или создать свою. Если не можешь выбрать — нажми «Помощь P&C» в боте.</div>' + '<button class="btn secondary" onclick="Telegram.WebApp.close()">Открыть бот</button>';
    const t = m.team;
    let html = '<h1>' + esc(t.emoji) + ' ' + esc(t.name) + '</h1>' +
      '<div class="stats"><div class="stat"><div class="v">' + t.points + '</div><div class="l">баллов</div></div><div class="stat"><div class="v">' + t.rank + '</div><div class="l">место</div></div><div class="stat"><div class="v">' + t.members.length + '/' + mar.team_size + '</div><div class="l">участников</div></div></div>' +
      '<h2>Состав</h2><div class="card">';
    t.members.forEach((mm) => { html += '<div class="member"><span>' + esc(mm.name) + (mm.captain ? ' 👑' : '') + '</span><b>' + mm.points + ' б.</b></div>'; });
    html += '</div>';
    if (t.members.length < mar.team_size) html += '<div class="hint">Свободных мест: ' + (mar.team_size - t.members.length) + '. Пригласи коллег — пусть выберут команду в боте.</div>';
    return html;
  }

  function top() {
    let html = '<h1>🏆 Рейтинг команд</h1><div class="card">';
    if (!data.leaderboard.length) html += '<div class="hint">Команд пока нет.</div>';
    const medals = ['🥇', '🥈', '🥉'];
    data.leaderboard.forEach((r, i) => {
      html += '<div class="lb' + (r.mine ? ' mine' : '') + '"><div class="pos">' + (medals[i] || i + 1) + '</div><div class="name">' + esc(r.emoji) + ' ' + esc(r.name) + '<div class="hint">' + r.members + ' чел.</div></div><b>' + r.points + '</b></div>';
    });
    return html + '</div>';
  }

  function fmt(iso) { const d = new Date(iso); return d.getDate().toString().padStart(2, '0') + '.' + (d.getMonth() + 1).toString().padStart(2, '0'); }

  tabs.addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; tab = b.dataset.tab; render(); });
  app.addEventListener('click', (e) => {
    const wb = e.target.closest('[data-week]');
    if (wb) { week = +wb.dataset.week; render(); return; }
    const sb = e.target.closest('[data-submit]');
    if (sb) {
      // Submitting files goes through the bot chat (Telegram Mini Apps can't upload to the bot directly).
      if (tg) { tg.showAlert('Отчёт отправляется в чате с ботом: Задания → задание → «Выполнить». Сейчас приложение закроется.', () => tg.close()); }
      return;
    }
    const card = e.target.closest('.task');
    if (card) { const id = +card.dataset.id; expanded.has(id) ? expanded.delete(id) : expanded.add(id); render(); }
  });

  load();
})();
