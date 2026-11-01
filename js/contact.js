/**
 * Contact page: turns the contact form into a call-order request and keeps
 * the account status area in sync with the shared auth system.
 */
(() => {
  'use strict';

  const { qs, qsa, escapeHtml, toPersianDigits } = window.MK;

  const serviceLabels = {
    web: 'طراحی و توسعه وب‌سایت',
    video: 'تولید ویدیو و تیزر',
    both: 'پکیج وب‌سایت + تولید ویدیو',
    other: 'مشاوره و سایر موارد',
  };
  const budgetLabels = {
    'under-30': 'کمتر از ۳۰ میلیون تومان',
    '30-70': '۳۰ تا ۷۰ میلیون تومان',
    '70-150': '۷۰ تا ۱۵۰ میلیون تومان',
    'over-150': 'بالای ۱۵۰ میلیون تومان',
  };

  function accountMarkup(user) {
    if (!user) {
      return `
        <p>برای ثبت سفارش تماس ابتدا وارد حساب کاربری خود شوید یا ثبت‌نام کنید.</p>
        <button class="btn btn-secondary btn-sm" type="button" data-open-auth>ورود / ثبت‌نام</button>`;
    }
    return `
      <div class="contact-account-user">
        <span class="contact-account-avatar" aria-hidden="true">${escapeHtml((user.firstName || 'م').slice(0, 1))}</span>
        <div>
          <strong>${escapeHtml(user.firstName)} ${escapeHtml(user.lastName)}</strong>
          <span dir="ltr">${escapeHtml(user.phone)}</span>
        </div>
      </div>
      <button class="btn btn-secondary btn-sm" type="button" data-logout>خروج از حساب</button>`;
  }

  function updateAccountArea(user) {
    const container = qs('[data-contact-account]');
    if (!container) return;
    container.innerHTML = accountMarkup(user);
    container.classList.toggle('is-authenticated', Boolean(user));
  }

  function initContactForm() {
    const form = qs('[data-contact-form]');
    if (!form) return;

    const serviceSelect = qs('#contact-service');
    const budgetSelect = qs('#contact-budget');
    const urgencySelect = qs('#contact-urgency');
    const message = qs('#contact-message');
    const submit = form.querySelector('button[type="submit"]');

    const errorBox = document.createElement('div');
    errorBox.className = 'form-alert';
    errorBox.setAttribute('role', 'alert');
    errorBox.hidden = true;
    form.prepend(errorBox);

    const showError = (messageText, field) => {
      errorBox.textContent = messageText;
      errorBox.hidden = !messageText;
      field?.focus();
    };
    const clearError = () => {
      errorBox.textContent = '';
      errorBox.hidden = true;
    };

    const requestedService = new URLSearchParams(window.location.search).get('service');
    if (requestedService && serviceSelect && serviceLabels[requestedService]) {
      serviceSelect.value = requestedService;
    }
    if (message) {
      message.addEventListener('input', () => {
        clearError();
        const count = message.value.length;
        message.closest('.field')?.querySelector('.contact-char-count')?.remove();
        const counter = document.createElement('p');
        counter.className = 'field-note contact-char-count';
        counter.textContent = `${toPersianDigits(count)} از ۱۵۰۰ کاراکتر`;
        message.closest('.field')?.append(counter);
      });
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearError();

      if (!window.MKAuth) {
        showError('سیستم حساب کاربری هنوز آماده نیست. لطفاً صفحه را دوباره بارگذاری کنید.');
        return;
      }

      const urgency = urgencySelect?.value || 'normal';
      const text = message?.value.trim() || '';
      if (text.length < 10) {
        showError('دلیل تماس باید حداقل ۱۰ کاراکتر باشد.', message);
        return;
      }
      if (text.length > 1500) {
        showError('متن دلیل تماس بیش از حد طولانی است.', message);
        return;
      }

      const service = serviceLabels[serviceSelect?.value] || 'مشاوره';
      const budget = budgetLabels[budgetSelect?.value] || 'نامشخص';
      const reason = `درخواست تماس از فرم تماس\nخدمات: ${service}\nبازه بودجه: ${budget}\n\n${text}`;
      if (reason.length > 1500) {
        showError('متن توضیحات بیش از حد طولانی است. آن را کوتاه‌تر کنید.', message);
        return;
      }

      submit?.setAttribute('disabled', 'disabled');
      try {
        window.MKAuth.openCall({ urgency, reason });
      } finally {
        submit?.removeAttribute('disabled');
      }
    });
  }

  function initFaq() {
    const items = qsa('.faq-item');
    items.forEach((item) => {
      item.addEventListener('toggle', () => {
        if (!item.open) return;
        items.forEach((other) => {
          if (other !== item) other.open = false;
        });
      });
    });
  }

  updateAccountArea(null);
  initContactForm();
  initFaq();

  if (window.MKAuth?.ready) {
    window.MKAuth.ready.then(() => updateAccountArea(window.MKAuth.getUser()));
  }
  document.addEventListener('mk:auth-changed', (event) => {
    updateAccountArea(event.detail?.user || null);
  });
})();
