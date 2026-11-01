(() => {
  'use strict';

/**
 * MK Graphy — shared vanilla JavaScript helpers
 * Lightweight, dependency-free utilities used by every page.
 */

const qs = (selector, root = document) => root.querySelector(selector);
const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const prefersReducedMotion = () =>
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const toPersianDigits = (value) =>
  String(value).replace(/\d/g, (digit) => '۰۱۲۳۴۵۶۷۸۹'[Number(digit)]);

const escapeHtml = (value = '') =>
  String(value).replace(/[&<>"']/g, (character) => {
    const map = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    };
    return map[character];
  });

const isBackend = () =>
  document.querySelector('meta[name="mk-backend"]')?.content === '1';

const staticAssetPath = (value) =>
  typeof value === 'string' && value.startsWith('/assets/') ? value.slice(1) : value;

const getStaticProjects = () =>
  (Array.isArray(window.MK_STATIC_PROJECTS) ? window.MK_STATIC_PROJECTS : [])
    .map((project) => ({
      ...project,
      image: staticAssetPath(project.image),
      bannerImage: staticAssetPath(project.bannerImage),
      gallery: Array.isArray(project.gallery) ? project.gallery.map(staticAssetPath) : [],
    }))
    .sort((first, second) => {
      const firstOrder = Number(first.sortOrder) || 1000;
      const secondOrder = Number(second.sortOrder) || 1000;
      return firstOrder - secondOrder || (Number(first.id) || 0) - (Number(second.id) || 0);
    });

/* --------------------------------------------------------------------------
   Sticky header state
   -------------------------------------------------------------------------- */

function initHeaderScroll() {
  const header = qs('[data-header]');
  if (!header) return;

  const update = () => {
    header.classList.toggle('is-scrolled', window.scrollY > 8);
  };

  update();
  window.addEventListener('scroll', update, { passive: true });
}

/* --------------------------------------------------------------------------
   Mobile menu
   -------------------------------------------------------------------------- */

function initMobileMenu() {
  const toggle = qs('[data-menu-toggle]');
  const menu = qs('[data-mobile-menu]');
  if (!toggle || !menu) return;

  const closeButton = qs('[data-menu-close]', menu);
  const body = document.body;

  const openMenu = () => {
    menu.hidden = false;
    body.classList.add('is-locked');
    toggle.setAttribute('aria-expanded', 'true');
    toggle.setAttribute('aria-label', 'بستن منو');
    closeButton?.focus();
  };

  const closeMenu = ({ restoreFocus = true } = {}) => {
    if (menu.hidden) return;
    menu.hidden = true;
    body.classList.remove('is-locked');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'باز کردن منو');
    if (restoreFocus) toggle.focus();
  };

  toggle.addEventListener('click', () => {
    if (menu.hidden) openMenu();
    else closeMenu();
  });

  closeButton?.addEventListener('click', () => closeMenu());

  qsa('a', menu).forEach((link) => {
    link.addEventListener('click', () => closeMenu({ restoreFocus: false }));
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !menu.hidden) {
      closeMenu();
    }
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth > 1024) closeMenu({ restoreFocus: false });
  });
}

/* --------------------------------------------------------------------------
   Scroll reveal
   -------------------------------------------------------------------------- */

let revealObserver = null;

function initReveal() {
  const elements = qsa('[data-reveal]');

  if (!window.IntersectionObserver || prefersReducedMotion()) {
    elements.forEach((element) => element.classList.add('is-revealed'));
    return;
  }

  if (!revealObserver) {
    revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('is-revealed');
          revealObserver.unobserve(entry.target);
        });
      },
      { rootMargin: '0px 0px -7% 0px', threshold: 0.08 },
    );
  }

  elements.forEach((element) => {
    if (element.classList.contains('is-revealed') || element.dataset.revealBound) return;
    element.dataset.revealBound = 'true';

    if (element.getBoundingClientRect().top < window.innerHeight * 0.95) {
      requestAnimationFrame(() => element.classList.add('is-revealed'));
    } else {
      revealObserver.observe(element);
    }
  });
}

/* --------------------------------------------------------------------------
   Image fallbacks (keeps broken external images from looking broken)
   -------------------------------------------------------------------------- */

function initImageFallbacks(root = document) {
  qsa('[data-media] img', root).forEach((image) => {
    if (image.dataset.fallbackBound) return;
    image.dataset.fallbackBound = 'true';

    const markAsBroken = () => {
      const wrapper = image.closest('[data-media]');
      if (wrapper) wrapper.classList.add('is-fallback');
    };

    if (image.complete && image.naturalWidth === 0) {
      markAsBroken();
    }

    image.addEventListener('error', markAsBroken, { once: true });
  });
}

/* --------------------------------------------------------------------------
   Back to top + footer year
   -------------------------------------------------------------------------- */

function initBackToTop() {
  const button = qs('[data-to-top]');
  if (!button) return;

  button.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  });
}

function initFooterYear() {
  let year = '۱۴۰۵';
  try {
    year = new Intl.DateTimeFormat('fa-IR', { year: 'numeric' }).format(new Date());
  } catch {
    // keep the Persian fallback year
  }

  qsa('[data-year]').forEach((element) => {
    element.textContent = year;
  });
}

/* --------------------------------------------------------------------------
   Boot
   -------------------------------------------------------------------------- */

function initShared() {
  initHeaderScroll();
  initMobileMenu();
  initReveal();
  initImageFallbacks();
  initBackToTop();
  initFooterYear();
}

  window.MK = {
    qs,
    qsa,
    prefersReducedMotion,
    toPersianDigits,
    escapeHtml,
    isBackend,
    getStaticProjects,
    initReveal,
    initImageFallbacks,
    initShared,
  };

  initShared();
})();
