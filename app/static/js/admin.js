(() => {
  const root = document.querySelector('[data-admin-root]');
  if (!root) return;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const errorBox = root.querySelector('[data-admin-error]');
  let state = null;
  let serverOffset = 0;

  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));
  const labels = {waiting: 'Ожидание', running: 'Игра идёт', finished: 'Завершён'};

  function render(data) {
    state = data;
    serverOffset = data.round.server_time * 1000 - Date.now();
    const round = data.round;
    root.querySelector('[data-round-status]').textContent = `${labels[round.status] || round.status} · ${round.is_open ? 'открыт' : 'закрыт'}`;
    root.querySelector('[data-round-status]').className = `status-pill status-${round.status}`;
    root.querySelector('[data-question-number]').textContent = round.status === 'finished' ? 'Финал' : `Вопрос ${round.current_question + 1} из ${round.question_total}`;
    root.querySelector('[data-question-text]').textContent = round.question_text;
    root.querySelector('[data-demo-note]').hidden = !round.demo_content;
    root.querySelector('[data-participant-count]').textContent = data.participants.length;
    const body = root.querySelector('[data-participants]');
    body.innerHTML = data.participants.length ? data.participants.map((person) => `<tr>
      <td>${escapeHtml(person.display_name)}</td><td>${person.answered_count}/${round.question_total}</td>
      <td><b>${person.score}</b></td><td>${person.completed_at ? 'Готово' : (person.missed_count ? `Пропущено: ${person.missed_count}` : 'Играет')}</td></tr>`).join('') : '<tr><td colspan="4">Пока никого нет</td></tr>';
    const timerButton = root.querySelector('[data-action="toggle-timer"]');
    timerButton.textContent = round.timer_enabled ? 'Выключить таймер' : 'Запустить 60 секунд';
    root.querySelector('[data-action="start"]').disabled = round.status !== 'waiting';
    root.querySelector('[data-action="next"]').disabled = round.status !== 'running';
    timerButton.disabled = round.status !== 'running';
    updateTimer();
  }

  function updateTimer() {
    if (!state) return;
    const round = state.round;
    const target = root.querySelector('[data-admin-timer]');
    if (!round.timer_enabled || !round.deadline) {
      target.textContent = '∞';
      return;
    }
    target.textContent = Math.max(0, Math.ceil(round.deadline - (Date.now() + serverOffset) / 1000));
  }

  async function loadState() {
    try {
      const response = await fetch('/admin/api/state', {cache: 'no-store'});
      if (!response.ok) throw new Error('Не удалось обновить пульт.');
      render(await response.json());
      errorBox.hidden = true;
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
    }
  }

  async function runAction(action) {
    const button = root.querySelector(`[data-action="${action}"]`);
    const confirmation = root.querySelector('[data-reset-confirmation]')?.value || '';
    button.disabled = true;
    try {
      const response = await fetch(`/admin/api/action/${action}`, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify({confirmation})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Действие не выполнено.');
      await loadState();
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
    } finally {
      button.disabled = false;
    }
  }

  root.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => runAction(button.dataset.action)));
  root.querySelector('[data-copy-link]').addEventListener('click', async (event) => {
    try {
      await navigator.clipboard.writeText(event.currentTarget.dataset.copyLink);
      event.currentTarget.textContent = 'Ссылка скопирована';
    } catch (_error) {
      errorBox.textContent = 'Не удалось скопировать автоматически. Выделите ссылку выше.';
      errorBox.hidden = false;
    }
  });
  loadState();
  setInterval(updateTimer, 250);
  setInterval(loadState, 2500);
})();

