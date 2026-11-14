(() => {
  const field = document.querySelector('[data-wish-text]');
  const counter = document.querySelector('[data-wish-counter]');
  if (!field || !counter) return;
  const update = () => {
    counter.textContent = `${field.value.length} из 500`;
  };
  field.addEventListener('input', update);
  update();
})();
