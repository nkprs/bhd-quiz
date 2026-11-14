(() => {
  const root = document.querySelector('[data-ranking-root]');
  if (!root) return;
  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));
  function render(rows) {
    const list = root.querySelector('[data-ranking-list]');
    if (!rows.length) return;
    list.innerHTML = rows.map((row) => `<div class="rank-row rank-${row.place <= 3 ? row.place : 'other'}${row.place === 1 ? ' is-winner' : ''}${row.is_own ? ' is-own' : ''}">
      <span class="rank-place">${row.place === 1 ? '🏆' : row.place === 2 ? '🥈' : row.place === 3 ? '🥉' : row.place}</span>
      <span class="rank-name">${escapeHtml(row.name)}${row.is_own ? '<small>это вы</small>' : ''}</span>
      <b class="rank-score">${row.score} <small>из ${row.max_score}</small></b></div>`).join('');
  }
  async function refresh() {
    if (document.hidden) return;
    try {
      const response = await fetch('/api/ranking', {cache: 'no-store'});
      if (response.ok) render((await response.json()).ranking);
    } catch (_error) { /* Existing ranking stays visible during a brief outage. */ }
  }
  setInterval(refresh, 10000);
})();

