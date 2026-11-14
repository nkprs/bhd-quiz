(() => {
  const root = document.querySelector('[data-quiz-root]');
  if (!root) return;
  const card = root.querySelector('[data-question-card]');
  const progressLabel = root.querySelector('[data-progress-label]');
  const progressBar = root.querySelector('[data-progress-bar]');
  const timer = root.querySelector('[data-timer]');
  const timerValue = root.querySelector('[data-timer-value]');
  const timerRing = root.querySelector('[data-timer-ring]');
  const message = root.querySelector('[data-quiz-message]');
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const numericFormats = {
    time: {placeholder: '22:10', suffix: '', maxDigits: 4, help: 'Формат: ЧЧ:ММ'},
    months: {placeholder: '8', suffix: 'месяцев', maxDigits: 2, help: 'Только целое число'},
    weight_grams: {placeholder: '3150', suffix: 'г', maxDigits: 5, help: 'Вес указываем в граммах'},
    height_cm: {placeholder: '51', suffix: 'см', maxDigits: 3, help: 'Рост указываем в сантиметрах'}
  };
  let state = null;
  let serverOffset = 0;
  let selected = null;
  let submitting = false;
  let currentQuestionId = null;

  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));

  function showMessage(text, kind = 'info', persistent = false) {
    message.hidden = false;
    message.className = `quiz-message ${kind}`;
    message.textContent = text;
    message.dataset.persistent = persistent ? 'true' : 'false';
  }

  function applyNumericMask(kind, value) {
    const format = numericFormats[kind] || numericFormats.months;
    const digits = value.replace(/\D/g, '').slice(0, format.maxDigits);
    if (kind === 'time' && digits.length > 2) {
      return `${digits.slice(0, 2)}:${digits.slice(2)}`;
    }
    return digits;
  }

  function renderWaiting(data) {
    const manual = !data.timer.enabled;
    card.innerHTML = `<div class="waiting-state"><span class="waiting-spark">✦</span>
      <h2>Ответ принят!</h2><p>${manual ? 'Ждём, когда ведущий откроет следующий вопрос.' : 'Следующий вопрос появится у всех одновременно.'}</p></div>`;
  }

  function renderQuestion(data) {
    const question = data.question;
    if (currentQuestionId === question.id) return;
    currentQuestionId = question.id;
    selected = null;
    message.hidden = true;
    message.dataset.persistent = 'false';
    const image = question.image ? `<figure class="question-image"><img src="${escapeHtml(question.image)}" alt=""><figcaption>${escapeHtml(question.caption || '')}</figcaption></figure>` : '';
    const isNumeric = question.type === 'numeric';
    const numericFormat = numericFormats[question.answer_kind] || numericFormats.months;
    const answerControl = isNumeric
      ? `<div class="numeric-answer-box"><label for="numeric-answer">Ваша догадка</label>
          <div class="masked-input-row"><input id="numeric-answer" data-numeric-answer type="text"
                 inputmode="numeric" autocomplete="off" pattern="[0-9:]*"
                 placeholder="${escapeHtml(numericFormat.placeholder)}"
                 aria-describedby="numeric-answer-help">
            ${numericFormat.suffix ? `<span aria-hidden="true">${escapeHtml(numericFormat.suffix)}</span>` : ''}</div>
          <small id="numeric-answer-help">${escapeHtml(numericFormat.help)}</small></div>`
      : `<div class="answer-list" role="group" aria-label="Варианты ответа">${question.options.map((option, index) => `<button class="answer-option" type="button" data-option-id="${escapeHtml(option.id)}">
          <span>${String.fromCharCode(65 + index)}</span><b>${escapeHtml(option.text)}</b><i aria-hidden="true"></i></button>`).join('')}</div>`;
    card.innerHTML = `${image}<h1>${escapeHtml(question.text)}</h1>${question.caption && !question.image ? `<p class="question-caption">${escapeHtml(question.caption)}</p>` : ''}
      ${answerControl}
      <button class="button button-coral submit-answer" type="button" disabled>Подтвердить ответ</button>`;
    if (isNumeric) {
      const input = card.querySelector('[data-numeric-answer]');
      input.addEventListener('input', () => {
        const masked = applyNumericMask(question.answer_kind, input.value);
        if (input.value !== masked) input.value = masked;
        selected = input.value.trim();
        card.querySelector('.submit-answer').disabled = !selected;
      });
      input.focus();
    } else {
      card.querySelectorAll('[data-option-id]').forEach((button) => {
        button.addEventListener('click', () => {
          if (submitting) return;
          selected = button.dataset.optionId;
          card.querySelectorAll('[data-option-id]').forEach((item) => item.classList.toggle('is-selected', item === button));
          card.querySelector('.submit-answer').disabled = false;
        });
      });
    }
    card.querySelector('.submit-answer').addEventListener('click', submitAnswer);
  }

  function render(data) {
    state = data;
    serverOffset = data.server_time * 1000 - Date.now();
    const current = Math.min(data.question_index + 1, data.question_total);
    progressLabel.textContent = `Вопрос ${current} из ${data.question_total}`;
    progressBar.style.width = `${(current / data.question_total) * 100}%`;
    if (data.round_status === 'finished') {
      window.location.assign('/result');
      return;
    }
    if (!data.quiz_open) {
      card.innerHTML = '<div class="waiting-state"><span class="waiting-spark">●</span><h2>Квиз на паузе</h2><p>Ведущий закрыл приём ответов. Ваш прогресс сохранён.</p></div>';
      return;
    }
    if (data.question && !data.already_answered) renderQuestion(data);
    else renderWaiting(data);
    updateTimer();
  }

  async function loadState() {
    try {
      const response = await fetch('/api/quiz/state', {headers: {'Accept': 'application/json'}, cache: 'no-store'});
      if (!response.ok) throw new Error('state');
      render(await response.json());
      if (message.dataset.persistent !== 'true') message.hidden = true;
    } catch (_error) {
      showMessage('Не удалось обновить вопрос. Проверим соединение ещё раз…', 'error');
    }
  }

  async function submitAnswer() {
    if (!selected || submitting || !state?.question) return;
    submitting = true;
    const button = card.querySelector('.submit-answer');
    button.disabled = true;
    button.textContent = 'Сохраняем…';
    try {
      const response = await fetch('/api/answer', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify(state.question.type === 'numeric'
          ? {question_id: state.question.id, answer: selected}
          : {question_id: state.question.id, option_id: selected})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Не удалось сохранить ответ.');
      state.already_answered = true;
      state.question = null;
      renderWaiting(state);
      showMessage('Ответ надёжно сохранён.', 'success');
    } catch (error) {
      showMessage(error.message || 'Ошибка сети. Попробуйте ещё раз.', 'error', true);
      button.disabled = false;
      button.textContent = 'Повторить сохранение';
    } finally {
      submitting = false;
    }
  }

  function updateTimer() {
    if (!state) return;
    if (!state.timer.enabled || !state.timer.deadline) {
      timer.classList.add('is-manual');
      timerValue.textContent = '∞';
      timer.setAttribute('aria-label', 'Таймер выключен ведущим');
      timerRing.style.strokeDashoffset = '0';
      return;
    }
    timer.classList.remove('is-manual');
    const now = (Date.now() + serverOffset) / 1000;
    const remaining = Math.max(0, Math.ceil(state.timer.deadline - now));
    timerValue.textContent = remaining;
    timer.setAttribute('aria-label', `Осталось ${remaining} секунд`);
    const circumference = 119.4;
    timerRing.style.strokeDasharray = `${circumference}`;
    timerRing.style.strokeDashoffset = `${circumference * (1 - remaining / state.timer.duration)}`;
    timer.classList.toggle('is-urgent', remaining <= 10);
    if (remaining === 0) setTimeout(loadState, 250);
  }

  loadState();
  setInterval(updateTimer, 250);
  setInterval(loadState, 2000);
})();

