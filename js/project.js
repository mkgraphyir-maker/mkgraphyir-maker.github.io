/**
 * MK Graphy — project detail page, rendered from the backend project API.
 */
(() => {
  'use strict';

  const {
    qs, escapeHtml, initImageFallbacks, initReveal, isBackend, getStaticProjects,
  } = window.MK;

  const icon = (name) =>
    `<svg class="icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;

  function renderProject(project, projects) {
    const total = projects.length;
    const index = projects.findIndex((item) => item.slug === project.slug);
    const prevProject = projects[(index - 1 + total) % total];
    const nextProject = projects[(index + 1) % total];

    const stats = (project.stats || [])
      .map(
        (stat) => `
          <div class="stat-card">
            <div class="stat-value">${escapeHtml(stat.value)}</div>
            <div class="stat-label">${escapeHtml(stat.label)}</div>
          </div>
        `,
      )
      .join('');

    const services = (project.servicesProvided || [])
      .map(
        (service) => `
          <div class="meta-service">
            ${icon('check')}
            <span>${escapeHtml(service)}</span>
          </div>
        `,
      )
      .join('');

    const technologies = (project.technologies || [])
      .map((technology) => `<span class="tech-pill">${escapeHtml(technology)}</span>`)
      .join('');

    const gallery = (project.gallery || [])
      .map(
        (image, imageIndex) => `
          <div class="gallery-item" data-media>
            <img
              src="${escapeHtml(image)}"
              alt="تصویر ${escapeHtml(String(imageIndex + 1))} از پروژه ${escapeHtml(project.title)}"
              width="800"
              height="600"
              loading="lazy"
            />
          </div>
        `,
      )
      .join('');

    const testimonial = project.testimonial
      ? `
        <div class="project-testimonial">
          ${icon('quote')}
          <blockquote>«${escapeHtml(project.testimonial)}»</blockquote>
          <cite>— نظر کارفرمای محترم پروژه (${escapeHtml(project.client)})</cite>
        </div>
      `
      : '';

    return `
      <nav class="breadcrumb" aria-label="مسیر صفحه">
        <a href="index.html">صفحه اصلی</a>
        ${icon('chevron-left')}
        <a href="index.html#portfolio">نمونه‌کارها</a>
        ${icon('chevron-left')}
        <span class="current">${escapeHtml(project.title)}</span>
      </nav>

      <article>
        <header class="project-banner" data-reveal>
          <div class="project-banner-content">
            <span class="project-banner-badge">${escapeHtml(project.categoryName)}</span>
            <h1>${escapeHtml(project.title)}</h1>
            <p>${escapeHtml(project.subtitle)}</p>
            <div class="project-banner-actions">
              <button class="btn btn-primary" type="button" data-open-call>
                ${icon('phone')}
                <span>سفارش پروژه مشابه</span>
              </button>
              <a class="btn btn-secondary" href="index.html#portfolio">همه نمونه‌کارها</a>
            </div>
          </div>
          <div class="project-banner-visual" data-media>
            <img
              src="${escapeHtml(project.bannerImage || project.image)}"
              alt="${escapeHtml(project.title)}"
              width="800"
              height="600"
              fetchpriority="high"
            />
          </div>
        </header>

        <div class="project-layout">
          <div class="project-main" data-reveal>
            <h2 class="project-section-title">درباره و شرح کامل پروژه</h2>
            <p>${escapeHtml(project.fullDescription)}</p>

            <div class="project-stats">${stats}</div>

            <div class="info-panel info-panel--challenge">
              <h3 class="info-panel-title">
                ${icon('clock')}
                <span>چالش و نیازمندی کارفرما</span>
              </h3>
              <p>${escapeHtml(project.challenge)}</p>
            </div>

            <div class="info-panel info-panel--solution">
              <h3 class="info-panel-title">
                ${icon('sparkles')}
                <span>راهکار خلاقانه استودیو MK Graphy</span>
              </h3>
              <p>${escapeHtml(project.solution)}</p>
            </div>

            <h2 class="project-section-title project-section-title--spaced">گالری تصاویر پروژه</h2>
            <div class="project-gallery">${gallery}</div>

            ${testimonial}

            <div class="project-pager">
              <a class="pager-link" href="project.html?id=${encodeURIComponent(prevProject.slug)}">
                <span>پروژه قبلی</span>
                <strong>${escapeHtml(prevProject.title)}</strong>
              </a>
              <a class="pager-link pager-link--next" href="project.html?id=${encodeURIComponent(nextProject.slug)}">
                <span>پروژه بعدی</span>
                <strong>${escapeHtml(nextProject.title)}</strong>
              </a>
            </div>
          </div>

          <aside class="project-sidebar" data-reveal style="--reveal-delay: 120ms">
            <div class="sidebar-card">
              <h3>مشخصات پروژه</h3>

              <div class="meta-list">
                <div class="meta-row">
                  <span class="label">کارفرما</span>
                  <span class="value">${escapeHtml(project.client)}</span>
                </div>
                <div class="meta-row">
                  <span class="label">سال اجرا</span>
                  <span class="value">${escapeHtml(project.year)}</span>
                </div>
                <div class="meta-row">
                  <span class="label">مدت زمان</span>
                  <span class="value">${escapeHtml(project.duration)}</span>
                </div>
                <div class="meta-row">
                  <span class="label">دسته‌بندی</span>
                  <span class="value">${escapeHtml(project.categoryName)}</span>
                </div>
              </div>

              <div class="meta-block">
                <span class="label">خدمات ارائه‌شده</span>
                <div class="meta-services">${services}</div>
              </div>

              <div class="meta-block">
                <span class="label">تکنولوژی‌ها و ابزارها</span>
                <div class="tech-pills">${technologies}</div>
              </div>

              <div class="sidebar-cta">
                <button class="btn btn-primary btn-block" type="button" data-open-call>
                  <span>سفارش پروژه مشابه</span>
                  ${icon('phone')}
                </button>
              </div>
              <p class="sidebar-note">مشاوره اولیه رایگان است.</p>
            </div>
          </aside>
        </div>
      </article>
    `;
  }

  function renderError(message) {
    return `
      <div class="project-error-page">
        <div class="project-error-card">
          <span class="mk-dialog-icon">${icon('sparkles')}</span>
          <h1>پروژه پیدا نشد</h1>
          <p>${escapeHtml(message)}</p>
          <a class="btn btn-primary" href="index.html#portfolio">بازگشت به نمونه‌کارها</a>
        </div>
      </div>
    `;
  }

  async function initProjectPage() {
    const root = qs('[data-project-root]');
    if (!root) return;

    const projectId = new URLSearchParams(window.location.search).get('id');
    if (!projectId) {
      root.innerHTML = renderError('شناسه پروژه در آدرس وجود ندارد. یکی از نمونه‌کارها را انتخاب کنید.');
      return;
    }

    try {
      let projects = [];
      if (isBackend()) {
        const payload = await window.MKApi.get('/api/projects');
        projects = Array.isArray(payload?.projects) ? payload.projects : [];
      } else {
        projects = getStaticProjects();
      }

      const project = projects.find((item) => item.slug === projectId);
      if (!project) {
        root.innerHTML = renderError('پروژه مورد نظر حذف شده یا هنوز منتشر نشده است.');
        return;
      }

      document.title = `${project.title} | MK Graphy`;
      const description = qs('meta[name="description"]');
      if (description) description.setAttribute('content', project.shortDescription);
      const ogTitle = qs('meta[property="og:title"]');
      if (ogTitle) ogTitle.setAttribute('content', `${project.title} | MK Graphy`);

      root.innerHTML = renderProject(project, projects);
      initImageFallbacks(root);
      initReveal();
    } catch (error) {
      root.innerHTML = renderError(error.message || 'دریافت اطلاعات پروژه با خطا مواجه شد.');
    }
  }

  initProjectPage();
})();
