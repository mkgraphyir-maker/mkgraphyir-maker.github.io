/**
 * MK Graphy — homepage behaviour: animated hero slider and API-driven portfolio.
 */
(() => {
  'use strict';

  const {
    qs, qsa, escapeHtml, toPersianDigits, prefersReducedMotion, initImageFallbacks,
    isBackend, getStaticProjects,
  } = window.MK;

  const icon = (name) =>
    `<svg class="icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;

  const projectLink = (project) => `project.html?id=${encodeURIComponent(project.slug)}`;

  /* ----------------------------------------------------------------------
     Hero slider
     ---------------------------------------------------------------------- */

  function initHeroSlider() {
    const slider = qs('[data-slider]');
    if (!slider) return;

    const section = slider.closest('.hero');
    const slides = qsa('[data-slide]', slider);
    if (!section || slides.length < 2) return;

    const dots = qsa('[data-slide-to]', section);
    const prevButton = qs('[data-slider-prev]', section);
    const nextButton = qs('[data-slider-next]', section);
    const progress = qs('[data-slider-progress]', section);
    const currentLabel = qs('[data-slide-current]', section);
    const status = qs('[data-slider-status]', section);

    const total = slides.length;
    const duration = 6500;
    const reduceMotion = prefersReducedMotion();

    let index = Math.max(
      0,
      slides.findIndex((slide) => slide.classList.contains('is-active')),
    );
    let timer = null;
    let paused = false;
    let hovered = false;

    const format = (value) => toPersianDigits(String(value).padStart(2, '0'));

    const restartProgress = () => {
      if (!progress) return;
      progress.classList.remove('is-running');
      void progress.offsetWidth;
      progress.classList.add('is-running');
      progress.classList.toggle('is-paused', paused);
    };

    const clearTimer = () => {
      if (timer) {
        window.clearInterval(timer);
        timer = null;
      }
    };

    const schedule = () => {
      clearTimer();
      if (paused || reduceMotion) return;
      timer = window.setInterval(() => update(index + 1), duration);
    };

    const update = (target) => {
      index = (target + total) % total;

      slides.forEach((slide, slideIndex) => {
        const isActive = slideIndex === index;
        slide.classList.toggle('is-active', isActive);
        slide.setAttribute('aria-hidden', String(!isActive));
      });

      dots.forEach((dot, dotIndex) => {
        const isActive = dotIndex === index;
        dot.classList.toggle('is-active', isActive);
        dot.setAttribute('aria-selected', String(isActive));
        dot.tabIndex = isActive ? 0 : -1;
      });

      if (currentLabel) currentLabel.textContent = format(index + 1);
      if (status) status.textContent = `اسلاید ${format(index + 1)} از ${format(total)}`;

      restartProgress();
      schedule();
    };

    const setPaused = (nextValue) => {
      if (paused === nextValue) return;
      paused = nextValue;

      if (paused) {
        clearTimer();
        progress?.classList.add('is-paused');
      } else {
        restartProgress();
        schedule();
      }
    };

    const syncPaused = () => {
      setPaused(hovered || document.hidden || reduceMotion);
    };

    prevButton?.addEventListener('click', () => update(index - 1));
    nextButton?.addEventListener('click', () => update(index + 1));

    dots.forEach((dot) => {
      dot.addEventListener('click', () => update(Number(dot.dataset.slideTo)));
    });

    section.addEventListener('mouseenter', () => {
      hovered = true;
      syncPaused();
    });
    section.addEventListener('mouseleave', () => {
      hovered = false;
      syncPaused();
    });
    document.addEventListener('visibilitychange', syncPaused);

    section.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        update(index + 1);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        update(index - 1);
      }
    });

    let touchStartX = null;
    let touchStartY = null;
    section.addEventListener('touchstart', (event) => {
      const touch = event.changedTouches[0];
      touchStartX = touch.clientX;
      touchStartY = touch.clientY;
    }, { passive: true });
    section.addEventListener('touchend', (event) => {
      if (touchStartX === null || touchStartY === null) return;
      const touch = event.changedTouches[0];
      const deltaX = touch.clientX - touchStartX;
      const deltaY = touch.clientY - touchStartY;
      touchStartX = null;
      touchStartY = null;
      if (Math.abs(deltaX) > 45 && Math.abs(deltaX) > Math.abs(deltaY) * 1.4) {
        update(index + (deltaX < 0 ? 1 : -1));
      }
    }, { passive: true });

    dots.forEach((dot, dotIndex) => {
      dot.tabIndex = dotIndex === index ? 0 : -1;
    });

    update(index);
    syncPaused();
  }

  /* ----------------------------------------------------------------------
     Portfolio — four mixed projects first, then "show more"
     ---------------------------------------------------------------------- */

  const INITIAL_PROJECTS = 4;

  function projectCardMarkup(project) {
    const isWeb = project.type === 'web';
    const href = projectLink(project);
    const technologies = (project.technologies || [])
      .slice(0, 3)
      .map((technology) => `<span class="tech-pill">${escapeHtml(technology)}</span>`)
      .join('');

    return `
      <article class="project-card project-card--${escapeHtml(project.type)}" data-type="${escapeHtml(project.type)}">
        <a class="project-media" href="${href}" data-media aria-label="مشاهده جزئیات ${escapeHtml(project.title)}">
          <img
            src="${escapeHtml(project.image)}"
            alt="${escapeHtml(project.title)}"
            width="800"
            height="550"
            loading="lazy"
          />
          <span class="project-badge">${escapeHtml(project.categoryName)}</span>
          <span class="project-kind">${icon(isWeb ? 'code' : 'video')}</span>
        </a>
        <div class="project-body">
          <h3 class="project-title">${escapeHtml(project.title)}</h3>
          <p class="project-desc">${escapeHtml(project.shortDescription)}</p>
          <div class="project-tags">${technologies}</div>
          <a class="project-link" href="${href}">
            <span>مشاهده جزئیات کامل</span>
            ${icon('arrow-left')}
          </a>
        </div>
      </article>
    `;
  }

  function initPortfolio() {
    const grid = qs('[data-portfolio-grid]');
    if (!grid) return;
    const moreWrap = qs('[data-portfolio-more-wrap]');
    const moreButton = qs('[data-portfolio-more]');

    let projects = [];
    let expanded = false;

    const render = () => {
      const visible = expanded ? projects : projects.slice(0, INITIAL_PROJECTS);
      grid.innerHTML = visible.map(projectCardMarkup).join('');
      qsa('.project-card', grid).forEach((card, index) => {
        card.classList.add('is-entering');
        card.style.setProperty('--enter-delay', `${index * 45}ms`);
      });
      initImageFallbacks(grid);

      if (moreWrap) moreWrap.hidden = projects.length <= INITIAL_PROJECTS;
      if (moreButton) {
        moreButton.textContent = expanded ? 'مشاهده کمتر' : 'مشاهده بیشتر';
        moreButton.setAttribute('aria-expanded', String(expanded));
      }
    };

    const load = async () => {
      grid.innerHTML = '<div class="portfolio-loading">در حال بارگذاری نمونه‌کارها…</div>';

      const useProjects = (nextProjects) => {
        projects = nextProjects;
        if (projects.length === 0) {
          grid.innerHTML = '<div class="portfolio-empty">هنوز نمونه‌کاری برای نمایش ثبت نشده است.</div>';
          if (moreWrap) moreWrap.hidden = true;
          return;
        }
        render();
      };

      // When the page is opened as a static file (or served by a simple
      // static server without the Python backend), use the bundled fallback
      // data instead of making a failing API request.
      if (!isBackend()) {
        useProjects(getStaticProjects());
        return;
      }

      try {
        const payload = await window.MKApi.get('/api/projects');
        useProjects(Array.isArray(payload?.projects) ? payload.projects : []);
      } catch (error) {
        grid.innerHTML = `
          <div class="portfolio-empty">
            <p>${escapeHtml(error.message || 'دریافت نمونه‌کارها با خطا مواجه شد.')}</p>
            <button class="btn btn-secondary btn-sm" type="button" data-portfolio-retry>تلاش دوباره</button>
          </div>`;
        qs('[data-portfolio-retry]', grid)?.addEventListener('click', load);
        if (moreWrap) moreWrap.hidden = true;
      }
    };

    moreButton?.addEventListener('click', () => {
      expanded = !expanded;
      render();
      if (!expanded) {
        document.getElementById('portfolio')?.scrollIntoView({
          behavior: prefersReducedMotion() ? 'auto' : 'smooth',
          block: 'start',
        });
      }
    });

    load();
  }

  initHeroSlider();
  initPortfolio();
})();
