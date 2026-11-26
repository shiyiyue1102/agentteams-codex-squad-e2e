(() => {
  const preview = document.querySelector('[data-dashboard-demo]');
  const slides = [...preview.querySelectorAll('[data-demo-slide]')];
  const buttons = [...preview.querySelectorAll('[data-demo-page]')];
  const pages = preview.querySelector('.demo-pages');
  const overlay = preview.querySelector('[data-demo-hover]');
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  let current = 0;
  let timer;
  let hoverTimer;

  function schedule() {
    clearTimeout(timer);
    if (!motion.matches && !document.hidden && !preview.matches(':hover, :focus-within')) {
      timer = setTimeout(() => show(current + 1), 6500);
    }
  }

  function closePreview() {
    clearTimeout(hoverTimer);
    overlay.hidden = true;
  }

  function show(index) {
    closePreview();
    slides[current].hidden = true;
    buttons[current].removeAttribute('aria-current');
    current = (index + slides.length) % slides.length;
    slides[current].hidden = false;
    buttons[current].setAttribute('aria-current', 'true');
    schedule();
  }

  buttons.forEach((button, index) => button.addEventListener('click', () => show(index)));
  pages.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    show(current + (event.key === 'ArrowRight' ? 1 : -1));
    buttons[current].focus();
  });
  slides.forEach((slide) => {
    const link = slide.querySelector('a');
    link.addEventListener('pointerenter', (event) => {
      if (event.pointerType !== 'mouse') return;
      hoverTimer = setTimeout(() => {
        overlay.querySelector('img').src = link.href;
        overlay.hidden = false;
      }, 150);
    });
    link.addEventListener('pointerleave', closePreview);
  });
  ['pointerenter', 'pointerleave', 'focusin', 'focusout'].forEach((type) => {
    preview.addEventListener(type, () => requestAnimationFrame(schedule));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closePreview();
  });
  window.addEventListener('blur', closePreview);
  document.addEventListener('visibilitychange', () => {
    closePreview();
    schedule();
  });
  motion.addEventListener('change', schedule);
  pages.hidden = false;
  schedule();

  document.querySelectorAll('[data-copy-install]').forEach((button) => {
    button.addEventListener('click', async () => {
      const card = button.closest('.install-command');
      const command = card.querySelector('code');
      const status = card.querySelector('[data-copy-status]');
      try {
        await navigator.clipboard.writeText(command.textContent);
        button.textContent = '已复制 ✓';
        status.textContent = '安装命令已复制。';
      } catch {
        button.textContent = '请手动复制';
        status.textContent = '无法访问剪贴板，请选择下方命令并复制。';
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(command);
        selection.removeAllRanges();
        selection.addRange(range);
      }
    });
  });
})();
