/**
 * Small same-origin JSON client for the MK Graphy backend.
 * CSRF tokens are kept in memory; cookies are HTTP-only and handled by the browser.
 */
(() => {
  'use strict';

  let csrfToken = null;

  const setCsrf = (token) => {
    csrfToken = token || null;
  };

  const getCsrf = () => csrfToken;

  async function request(method, url, data, options = {}) {
    const headers = {
      Accept: 'application/json',
      ...(options.headers || {}),
    };

    if (data !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    if (csrfToken && method !== 'GET' && method !== 'HEAD') {
      headers['X-CSRF-Token'] = csrfToken;
    }

    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        credentials: 'same-origin',
        body: data === undefined ? undefined : JSON.stringify(data),
      });
    } catch (networkError) {
      const error = new Error('ارتباط با سرور برقرار نشد. اتصال خود را بررسی کنید.');
      error.status = 0;
      error.cause = networkError;
      throw error;
    }

    const raw = await response.text();
    let payload = null;
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch {
        payload = null;
      }
    }

    if (!response.ok) {
      const message = payload?.error || 'خطایی در انجام درخواست رخ داد.';
      const error = new Error(message);
      error.status = response.status;
      error.field = payload?.field || null;
      error.data = payload;
      if (response.status === 401) {
        window.dispatchEvent(new CustomEvent('mk:unauthorized'));
      }
      throw error;
    }

    return payload;
  }

  window.MKApi = {
    request,
    get: (url, options) => request('GET', url, undefined, options),
    post: (url, data, options) => request('POST', url, data, options),
    put: (url, data, options) => request('PUT', url, data, options),
    patch: (url, data, options) => request('PATCH', url, data, options),
    del: (url, data, options) => request('DELETE', url, data, options),
    setCsrf,
    getCsrf,
  };
})();
