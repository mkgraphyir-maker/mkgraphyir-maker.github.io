/**
 * Admin sign-in page. The server already redirects authenticated admins to /admin.
 */
(() => {
  'use strict';

  const form = document.querySelector('[data-admin-login-form]');
  const errorBox = document.querySelector('[data-login-error]');
  const submit = document.querySelector('[data-login-submit]');

  const showError = (message) => {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = !message;
  };

  form?.addEventListener('submit', async (event) => {
    event.preventDefault();
    showError('');
    const phone = form.querySelector('[name="phone"]')?.value.trim() || '';
    const password = form.querySelector('[name="password"]')?.value || '';
    if (!phone || !password) {
      showError('شماره موبایل و رمز عبور را وارد کنید.');
      return;
    }

    submit.disabled = true;
    try {
      const payload = await window.MKApi.post('/api/auth/login', { phone, password });
      if (payload?.user?.role !== 'admin') {
        window.MKApi.setCsrf(payload?.csrfToken || null);
        try {
          await window.MKApi.post('/api/auth/logout', {});
        } catch {
          // Ignore logout errors; the important part is denying admin access.
        }
        showError('این حساب کاربری دسترسی مدیریت ندارد.');
        return;
      }
      window.location.replace('/admin');
    } catch (error) {
      showError(error.message || 'ورود به پنل مدیریت انجام نشد.');
    } finally {
      submit.disabled = false;
    }
  });
})();
