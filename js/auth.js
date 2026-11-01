/**
 * Account, sign-in/sign-up and call-order UI for MK Graphy.
 * Talks only to the site's own Python backend.
 */
(() => {
  'use strict';

  const { escapeHtml, toPersianDigits, qs, qsa } = window.MK;
  const backendAvailable = window.MK.isBackend();

  const state = {
    user: null,
    csrfToken: null,
    pendingCall: null,
    activeModal: null,
    lastFocus: null,
    authMode: 'login',
  };

  const icon = (name) =>
    `<svg class="icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;

  const authModalMarkup = `
    <div class="mk-modal" id="auth-modal" hidden>
      <div class="mk-modal-backdrop" data-close-modal></div>
      <div class="mk-dialog mk-auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title">
        <button class="mk-modal-close" type="button" data-close-modal aria-label="بستن">${icon('close')}</button>
        <div class="mk-dialog-brand">
          <span class="brand-mark" aria-hidden="true">MK</span>
          <span>حساب کاربری MK Graphy</span>
        </div>
        <h2 id="auth-title" data-auth-title>ورود به حساب کاربری</h2>
        <p class="mk-dialog-subtitle" data-auth-subtitle>
          برای ثبت سفارش تماس و مشاهده وضعیت درخواست‌ها، وارد حساب خود شوید.
        </p>

        <div class="auth-tabs" role="tablist" aria-label="ورود یا ثبت‌نام">
          <button class="auth-tab is-active" type="button" role="tab" aria-selected="true" data-auth-tab="login">ورود</button>
          <button class="auth-tab" type="button" role="tab" aria-selected="false" data-auth-tab="signup">ثبت‌نام</button>
        </div>

        <p class="mk-modal-note" data-auth-note hidden></p>
        <div class="mk-alert" data-auth-error role="alert" hidden></div>

        <form class="mk-form" data-login-form novalidate>
          <div class="mk-field">
            <label for="login-phone">شماره موبایل</label>
            <input class="mk-input" id="login-phone" name="phone" type="tel" inputmode="tel"
              autocomplete="tel" placeholder="۰۹۱۲ ۳۴۵ ۶۷۸۹" dir="ltr" required>
          </div>
          <div class="mk-field">
            <label for="login-password">رمز عبور</label>
            <input class="mk-input" id="login-password" name="password" type="password"
              autocomplete="current-password" placeholder="رمز عبور خود را وارد کنید" required>
          </div>
          <button class="btn btn-primary btn-block" type="submit" data-login-submit>
            ${icon('arrow-left')}
            <span>ورود به حساب</span>
          </button>
          <p class="mk-form-hint">حساب ندارید؟ روی تب «ثبت‌نام» بزنید.</p>
        </form>

        <form class="mk-form" data-signup-form novalidate hidden>
          <div class="mk-form-grid">
            <div class="mk-field">
              <label for="signup-first-name">نام</label>
              <input class="mk-input" id="signup-first-name" name="firstName" type="text"
                autocomplete="given-name" placeholder="مثال: پارسا" required>
            </div>
            <div class="mk-field">
              <label for="signup-last-name">نام خانوادگی</label>
              <input class="mk-input" id="signup-last-name" name="lastName" type="text"
                autocomplete="family-name" placeholder="مثال: راد" required>
            </div>
          </div>
          <div class="mk-field">
            <label for="signup-phone">شماره موبایل</label>
            <input class="mk-input" id="signup-phone" name="phone" type="tel" inputmode="tel"
              autocomplete="tel" placeholder="۰۹۱۲ ۳۴۵ ۶۷۸۹" dir="ltr" required>
          </div>
          <div class="mk-form-grid">
            <div class="mk-field">
              <label for="signup-password">رمز عبور امن</label>
              <input class="mk-input" id="signup-password" name="password" type="password"
                autocomplete="new-password" placeholder="حداقل ۱۰ کاراکتر" required>
            </div>
            <div class="mk-field">
              <label for="signup-password-confirm">تکرار رمز عبور</label>
              <input class="mk-input" id="signup-password-confirm" name="passwordConfirm" type="password"
                autocomplete="new-password" placeholder="تکرار رمز عبور" required>
            </div>
          </div>
          <p class="mk-form-hint">رمز عبور باید حداقل ۱۰ کاراکتر، شامل حرف بزرگ و عدد باشد و رمز عمومی نباشد.</p>
          <button class="btn btn-primary btn-block" type="submit" data-signup-submit>
            ${icon('arrow-left')}
            <span>ساخت حساب کاربری</span>
          </button>
        </form>
      </div>
    </div>
  `;

  const callModalMarkup = `
    <div class="mk-modal" id="call-modal" hidden>
      <div class="mk-modal-backdrop" data-close-modal></div>
      <div class="mk-dialog mk-call-dialog" role="dialog" aria-modal="true" aria-labelledby="call-title">
        <button class="mk-modal-close" type="button" data-close-modal aria-label="بستن">${icon('close')}</button>
        <div class="mk-dialog-brand">
          <span class="mk-dialog-icon">${icon('phone')}</span>
          <span>ثبت سفارش تماس</span>
        </div>
        <h2 id="call-title">درخواست تماس با استودیو</h2>
        <p class="mk-dialog-subtitle">
          شماره و مشخصات شما از حساب کاربری خوانده می‌شود. فوریت و دلیل تماس را مشخص کنید تا
          کارشناس مناسب با شما ارتباط بگیرد.
        </p>
        <div class="call-user" data-call-user></div>
        <div class="mk-alert" data-call-error role="alert" hidden></div>

        <form class="mk-form" data-call-form novalidate>
          <fieldset class="urgency-fieldset">
            <legend>فوریت درخواست</legend>
            <div class="urgency-options">
              <label class="urgency-option">
                <input type="radio" name="urgency" value="immediate">
                <span><strong>فوری</strong><small>کمتر از ۲ ساعت</small></span>
              </label>
              <label class="urgency-option">
                <input type="radio" name="urgency" value="today">
                <span><strong>امروز</strong><small>در ساعات کاری امروز</small></span>
              </label>
              <label class="urgency-option">
                <input type="radio" name="urgency" value="this_week">
                <span><strong>این هفته</strong><small>طی چند روز آینده</small></span>
              </label>
              <label class="urgency-option">
                <input type="radio" name="urgency" value="normal" checked>
                <span><strong>عادی</strong><small>بدون عجله</small></span>
              </label>
            </div>
          </fieldset>

          <div class="mk-field">
            <label for="call-reason">دلیل تماس و توضیحات</label>
            <textarea class="mk-input" id="call-reason" name="reason" rows="4" minlength="10"
              maxlength="1500" placeholder="مثال: برای طراحی یک وب‌سایت فروشگاهی و تولید تیزر معرفی محصول نیاز به مشاوره دارم." required></textarea>
            <div class="mk-field-meta">
              <span>حداقل ۱۰ کاراکتر</span>
              <span data-call-count>۰ / ۱۵۰۰</span>
            </div>
          </div>

          <button class="btn btn-primary btn-block" type="submit" data-call-submit>
            ${icon('check-circle')}
            <span>ثبت سفارش تماس</span>
          </button>
          <p class="mk-form-hint">پس از ثبت، درخواست شما در پنل مدیریت قابل بررسی است.</p>
        </form>
      </div>
    </div>
  `;

  const successModalMarkup = `
    <div class="mk-modal" id="success-modal" hidden>
      <div class="mk-modal-backdrop" data-close-modal></div>
      <div class="mk-dialog mk-success-dialog" role="dialog" aria-modal="true" aria-labelledby="success-title">
        <div class="mk-success-icon">${icon('check-circle')}</div>
        <h2 id="success-title">سفارش شما ثبت شد</h2>
        <p data-success-message>
          درخواست تماس یا پروژه شما با موفقیت ثبت شد. کارشناسان ما به‌زودی با شما تماس می‌گیرند
          یا برای شما پیام می‌فرستند.
        </p>
        <button class="btn btn-primary btn-block" type="button" data-close-modal>متوجه شدم</button>
      </div>
    </div>
  `;

  const noticeModalMarkup = `
    <div class="mk-modal" id="notice-modal" hidden>
      <div class="mk-modal-backdrop" data-close-modal></div>
      <div class="mk-dialog mk-success-dialog" role="dialog" aria-modal="true" aria-labelledby="notice-title">
        <div class="mk-dialog-icon">${icon('sparkles')}</div>
        <h2 id="notice-title">برای این بخش سرور لازم است</h2>
        <p>
          نمونه‌کارها در حالت استاتیک نمایش داده می‌شوند؛ اما برای ورود، ثبت‌نام و ثبت سفارش تماس
          باید بک‌اند Python را اجرا کنید:
        </p>
        <p class="mk-notice-code" dir="ltr">python backend/app.py</p>
        <p>
          سپس سایت را از آدرس
          <span dir="ltr">http://127.0.0.1:8000</span>
          باز کنید.
        </p>
        <button class="btn btn-primary btn-block" type="button" data-close-modal>متوجه شدم</button>
      </div>
    </div>
  `;

  function buildModals() {
    const wrapper = document.createElement('div');
    wrapper.innerHTML = authModalMarkup + callModalMarkup + successModalMarkup + noticeModalMarkup;
    while (wrapper.firstElementChild) {
      document.body.appendChild(wrapper.firstElementChild);
    }
  }

  function updateAuthUI() {
    const desktop = qsa('[data-auth-slot]');
    const mobile = qsa('[data-mobile-auth-slot]');

    if (state.user) {
      const name = escapeHtml(state.user.firstName || '');
      const adminLink = state.user.role === 'admin'
        ? '<a class="user-admin-link" href="/admin">پنل مدیریت</a>'
        : '';
      const userMarkup = `
        <div class="user-chip">
          <span class="user-chip-name">سلام، ${name}</span>
          ${adminLink}
          <button class="user-logout" type="button" data-logout>خروج</button>
        </div>`;
      const mobileMarkup = `
        <div class="mobile-user-chip">
          <div>
            <span class="user-chip-name">سلام، ${name}</span>
            <span class="mobile-user-phone" dir="ltr">${escapeHtml(state.user.phone || '')}</span>
          </div>
          <div class="mobile-user-actions">
            ${state.user.role === 'admin' ? '<a class="btn btn-secondary btn-sm" href="/admin">پنل مدیریت</a>' : ''}
            <button class="btn btn-secondary btn-sm" type="button" data-logout>خروج</button>
          </div>
        </div>`;
      desktop.forEach((slot) => { slot.innerHTML = userMarkup; });
      mobile.forEach((slot) => { slot.innerHTML = mobileMarkup; });
    } else {
      desktop.forEach((slot) => {
        slot.innerHTML = '<button class="btn btn-secondary btn-sm auth-btn" type="button" data-open-auth>ورود / ثبت‌نام</button>';
      });
      mobile.forEach((slot) => {
        slot.innerHTML = '<button class="btn btn-secondary btn-block" type="button" data-open-auth>ورود / ثبت‌نام</button>';
      });
    }
    document.dispatchEvent(new CustomEvent('mk:auth-changed', { detail: { user: state.user } }));
  }

  function closeMobileMenu() {
    const menu = qs('[data-mobile-menu]');
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    document.body.classList.remove('is-locked');
    const toggle = qs('[data-menu-toggle]');
    toggle?.setAttribute('aria-expanded', 'false');
    toggle?.setAttribute('aria-label', 'باز کردن منو');
  }

  function isAnyModalOpen() {
    return Boolean(state.activeModal);
  }

  function setModal(id, open) {
    const modal = document.getElementById(id);
    if (!modal) return;
    if (open) {
      qsa('.mk-modal:not([hidden])').forEach((other) => {
        if (other.id !== id) other.hidden = true;
      });
      state.activeModal = id;
      state.lastFocus = document.activeElement;
      modal.hidden = false;
      document.body.classList.add('is-locked');
      window.requestAnimationFrame(() => {
        const focusable = modal.querySelector(
          'input:not([type="hidden"]), select, textarea, button:not([disabled]), [href]',
        );
        focusable?.focus();
      });
    } else {
      modal.hidden = true;
      if (state.activeModal === id) state.activeModal = null;
      const mobileMenu = qs('[data-mobile-menu]');
      if (!isAnyModalOpen() && (!mobileMenu || mobileMenu.hidden)) {
        document.body.classList.remove('is-locked');
      }
      if (!isAnyModalOpen() && state.lastFocus && typeof state.lastFocus.focus === 'function') {
        state.lastFocus.focus();
      }
    }
  }

  function closeAllModals() {
    qsa('.mk-modal').forEach((modal) => {
      modal.hidden = true;
    });
    state.activeModal = null;
    const mobileMenu = qs('[data-mobile-menu]');
    if (!mobileMenu || mobileMenu.hidden) document.body.classList.remove('is-locked');
  }

  function showAuthError(message) {
    const box = qs('[data-auth-error]');
    if (!box) return;
    box.textContent = message;
    box.hidden = !message;
  }

  function showCallError(message) {
    const box = qs('[data-call-error]');
    if (!box) return;
    box.textContent = message;
    box.hidden = !message;
  }

  function setAuthMode(mode) {
    state.authMode = mode === 'signup' ? 'signup' : 'login';
    const loginForm = qs('[data-login-form]');
    const signupForm = qs('[data-signup-form]');
    const title = qs('[data-auth-title]');
    const subtitle = qs('[data-auth-subtitle]');
    qsa('[data-auth-tab]').forEach((tab) => {
      const active = tab.dataset.authTab === state.authMode;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', String(active));
    });
    if (loginForm) loginForm.hidden = state.authMode !== 'login';
    if (signupForm) signupForm.hidden = state.authMode !== 'signup';
    if (title) title.textContent = state.authMode === 'signup' ? 'ساخت حساب کاربری' : 'ورود به حساب کاربری';
    if (subtitle) {
      subtitle.textContent = state.authMode === 'signup'
        ? 'نام، شماره موبایل و یک رمز عبور امن برای ساخت حساب وارد کنید.'
        : 'برای ثبت سفارش تماس و پیگیری درخواست‌ها، وارد حساب خود شوید.';
    }
    showAuthError('');
  }

  function showBackendNotice() {
    setModal('notice-modal', true);
  }

  function openAuth({ mode = 'login', note = '', pendingCall = null } = {}) {
    if (!backendAvailable) {
      showBackendNotice();
      return;
    }
    if (pendingCall !== null) state.pendingCall = pendingCall;
    setAuthMode(mode);
    const noteBox = qs('[data-auth-note]');
    if (noteBox) {
      noteBox.textContent = note;
      noteBox.hidden = !note;
    }
    setModal('auth-modal', true);
  }

  function openCall(prefill = {}) {
    if (!backendAvailable) {
      showBackendNotice();
      return;
    }
    if (!state.user) {
      state.pendingCall = prefill;
      openAuth({
        mode: 'login',
        note: 'برای ثبت سفارش تماس ابتدا وارد حساب کاربری خود شوید یا ثبت‌نام کنید.',
        pendingCall: prefill,
      });
      return;
    }

    setModal('call-modal', true);
    const userBox = qs('[data-call-user]');
    if (userBox) {
      userBox.innerHTML = `
        <span class="call-user-avatar" aria-hidden="true">${escapeHtml((state.user.firstName || 'م').slice(0, 1))}</span>
        <span class="call-user-copy">
          <strong>${escapeHtml(state.user.firstName)} ${escapeHtml(state.user.lastName)}</strong>
          <span dir="ltr">${escapeHtml(state.user.phone)}</span>
        </span>`;
    }
    const reason = qs('#call-reason');
    if (reason) {
      reason.value = prefill.reason || '';
      updateCallCount();
    }
    const urgency = prefill.urgency && ['immediate', 'today', 'this_week', 'normal'].includes(prefill.urgency)
      ? prefill.urgency
      : 'normal';
    const radio = qs(`input[name="urgency"][value="${urgency}"]`);
    if (radio) radio.checked = true;
    showCallError('');
    window.requestAnimationFrame(() => reason?.focus());
  }

  function updateCallCount() {
    const reason = qs('#call-reason');
    const counter = qs('[data-call-count]');
    if (!reason || !counter) return;
    counter.textContent = `${toPersianDigits(reason.value.length)} / ۱۵۰۰`;
  }

  function showSuccess(message) {
    const messageBox = qs('[data-success-message]');
    if (messageBox) {
      messageBox.textContent = message || 'درخواست تماس یا پروژه شما با موفقیت ثبت شد. کارشناسان ما به‌زودی با شما تماس می‌گیرند یا برای شما پیام می‌فرستند.';
    }
    closeAllModals();
    setModal('success-modal', true);
  }

  function setSession(payload) {
    state.user = payload?.user || null;
    state.csrfToken = payload?.csrfToken || null;
    window.MKApi.setCsrf(state.csrfToken);
    updateAuthUI();
  }

  async function refreshSession() {
    if (!backendAvailable) return;
    try {
      const payload = await window.MKApi.get('/api/session');
      setSession(payload);
    } catch {
      if (state.user) {
        state.user = null;
        state.csrfToken = null;
        window.MKApi.setCsrf(null);
        updateAuthUI();
      }
    }
  }

  function finishPendingCall() {
    if (state.pendingCall === null) return;
    const pending = state.pendingCall;
    state.pendingCall = null;
    closeAllModals();
    openCall(pending);
  }

  async function handleLogin(event) {
    event.preventDefault();
    showAuthError('');
    const form = event.currentTarget;
    const phone = form.querySelector('[name="phone"]')?.value.trim() || '';
    const password = form.querySelector('[name="password"]')?.value || '';
    const submit = qs('[data-login-submit]');
    if (!phone || !password) {
      showAuthError('شماره موبایل و رمز عبور را وارد کنید.');
      return;
    }
    submit.disabled = true;
    try {
      const payload = await window.MKApi.post('/api/auth/login', { phone, password });
      setSession(payload);
      form.reset();
      closeAllModals();
      finishPendingCall();
    } catch (error) {
      showAuthError(error.message);
    } finally {
      submit.disabled = false;
    }
  }

  async function handleSignup(event) {
    event.preventDefault();
    showAuthError('');
    const form = event.currentTarget;
    const data = {
      firstName: form.querySelector('[name="firstName"]')?.value.trim() || '',
      lastName: form.querySelector('[name="lastName"]')?.value.trim() || '',
      phone: form.querySelector('[name="phone"]')?.value.trim() || '',
      password: form.querySelector('[name="password"]')?.value || '',
      passwordConfirm: form.querySelector('[name="passwordConfirm"]')?.value || '',
    };
    const submit = qs('[data-signup-submit]');
    if (!data.firstName || !data.lastName || !data.phone || !data.password || !data.passwordConfirm) {
      showAuthError('همه فیلدهای ثبت‌نام را کامل کنید.');
      return;
    }
    if (data.password !== data.passwordConfirm) {
      showAuthError('تکرار رمز عبور با رمز عبور یکسان نیست.');
      return;
    }
    submit.disabled = true;
    try {
      const payload = await window.MKApi.post('/api/auth/signup', data);
      setSession(payload);
      form.reset();
      closeAllModals();
      finishPendingCall();
    } catch (error) {
      showAuthError(error.message);
    } finally {
      submit.disabled = false;
    }
  }

  async function handleCallSubmit(event) {
    event.preventDefault();
    showCallError('');
    const selected = qs('input[name="urgency"]:checked');
    const reason = qs('#call-reason');
    const submit = qs('[data-call-submit]');
    const urgency = selected?.value || '';
    const text = reason?.value.trim() || '';

    if (!urgency) {
      showCallError('فوریت درخواست را انتخاب کنید.');
      return;
    }
    if (text.length < 10) {
      showCallError('دلیل تماس باید حداقل ۱۰ کاراکتر باشد.');
      reason?.focus();
      return;
    }

    submit.disabled = true;
    try {
      const payload = await window.MKApi.post('/api/call-orders', { urgency, reason: text });
      if (reason) reason.value = '';
      updateCallCount();
      showSuccess(payload?.message);
    } catch (error) {
      if (error.status === 401) {
        setSession({ user: null, csrfToken: null });
        state.pendingCall = { urgency, reason: text };
        openAuth({
          mode: 'login',
          note: 'نشست شما منقضی شده است. برای ادامه دوباره وارد شوید.',
        });
      } else {
        showCallError(error.message);
      }
    } finally {
      submit.disabled = false;
    }
  }

  async function logout() {
    try {
      await window.MKApi.post('/api/auth/logout', {});
    } catch {
      // Logging out locally is still safe if the server session is gone.
    }
    state.user = null;
    state.csrfToken = null;
    window.MKApi.setCsrf(null);
    updateAuthUI();
    closeAllModals();
  }

  function handleDocumentClick(event) {
    const target = event.target;
    if (!(target instanceof Element)) return;

    if (target.closest('[data-open-auth]')) {
      event.preventDefault();
      closeMobileMenu();
      openAuth();
      return;
    }
    if (target.closest('[data-open-call]')) {
      event.preventDefault();
      closeMobileMenu();
      openCall({});
      return;
    }
    if (target.closest('[data-logout]')) {
      event.preventDefault();
      logout();
      return;
    }
    if (target.closest('[data-auth-tab]')) {
      setAuthMode(target.closest('[data-auth-tab]').dataset.authTab);
      return;
    }
    if (target.closest('[data-close-modal]')) {
      const modal = target.closest('.mk-modal');
      if (modal) setModal(modal.id, false);
    }
  }

  function handleKeydown(event) {
    if (event.key === 'Escape' && state.activeModal) {
      setModal(state.activeModal, false);
      return;
    }
    if (event.key !== 'Tab' || !state.activeModal) return;
    const modal = document.getElementById(state.activeModal);
    if (!modal) return;
    const focusable = Array.from(
      modal.querySelectorAll(
        'button:not([disabled]), [href], input:not([type="hidden"]), select, textarea, [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((element) => element.offsetParent !== null);
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function bindEvents() {
    document.addEventListener('click', handleDocumentClick);
    document.addEventListener('keydown', handleKeydown);
    qs('[data-login-form]')?.addEventListener('submit', handleLogin);
    qs('[data-signup-form]')?.addEventListener('submit', handleSignup);
    qs('[data-call-form]')?.addEventListener('submit', handleCallSubmit);
    qs('#call-reason')?.addEventListener('input', updateCallCount);
    window.addEventListener('mk:unauthorized', () => {
      if (state.user) {
        state.user = null;
        state.csrfToken = null;
        window.MKApi.setCsrf(null);
        updateAuthUI();
      }
    });
  }

  buildModals();
  bindEvents();
  updateAuthUI();
  updateCallCount();
  window.MKAuth = {
    ready: backendAvailable ? refreshSession() : Promise.resolve(),
    backendAvailable,
    openAuth,
    openCall,
    getUser: () => state.user,
    isAuthenticated: () => Boolean(state.user),
    refresh: refreshSession,
    logout,
    showSuccess,
  };
})();
